# vendored copy of the author's hermes_audit/architecture_gate.py (MIT licence, same as this plugin); do not edit
"""Static architecture gate for Hermes reusable components (100% Python stdlib).

No CLI, printing, or ambient environment reads inside this module; single public
entrypoint `audit_module(...)`.

Limits:
- Arbitrary dataflow and object reconstruction are out of scope; this gate is a
  fail-closed static AST approximation, not a proof of runtime compliance.
- A Name argv in `subprocess.run(a, ...)` is resolved only through a single
  same-scope assignment; if that assignment is a tuple/str/bytes literal or
  str/bytes constructor, it is flagged as an error, otherwise non-literal or
  multi-assigned bindings are unknown and allowed.
"""
from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass
import re
import symtable
import sys
from pathlib import PurePath
from typing import Any, Mapping

__all__ = ["audit_module"]

_VALID_ROLES = frozenset({"component", "runner", "runner_module", "vendored", "one_off"})
_CHECKED_BUILTINS = frozenset({
    "print", "input", "exit", "quit", "__import__", "eval", "exec", "compile",
    "getattr", "setattr", "hasattr", "delattr",
    "Exception", "BaseException",
})
_CHECKED_MODULES = frozenset({"os", "sys", "subprocess", "pathlib", "argparse", "importlib", "builtins"})
_FIXED: dict[str, str] = {
    **{name: f"builtins.{name}" for name in _CHECKED_BUILTINS},
    **{name: name for name in _CHECKED_MODULES},
}
_CHECKED_ROOTS = frozenset(_FIXED.values())
_BUILTIN_NAMES = frozenset(dir(builtins))

_BANNED_CALL_TARGETS = frozenset({
    "builtins.print",
    "builtins.input",
    "builtins.exit",
    "builtins.quit",
    "sys.exit",
})
_AMBIENT_TARGETS = frozenset({
    "os.environ",
    "os.environb",
    "os.getenv",
    "os.putenv",
    "os.getcwd",
    "os.getcwdb",
    "pathlib.Path.home",
    "pathlib.Path.cwd",
    "pathlib.PurePath.home",
    "pathlib.PurePath.cwd",
    "pathlib.PosixPath.home",
    "pathlib.PosixPath.cwd",
    "pathlib.WindowsPath.home",
    "pathlib.WindowsPath.cwd",
    "os.path.expanduser",
    "os.path.expandvars",
})
_PATHLIB_CTOR_TARGETS = frozenset({
    "pathlib.Path",
    "pathlib.PurePath",
    "pathlib.PosixPath",
    "pathlib.WindowsPath",
    "pathlib.PurePosixPath",
    "pathlib.PureWindowsPath",
})
_DYNAMIC_CALL_TARGETS = frozenset({
    "builtins.__import__",
    "builtins.eval",
    "builtins.exec",
    "builtins.compile",
    "importlib.import_module",
    "importlib.__import__",
})
_DYNAMIC_ATTR_BUILTINS = frozenset({
    "builtins.getattr",
    "builtins.setattr",
    "builtins.hasattr",
    "builtins.delattr",
})
_DYNAMIC_CHECKED_MODULES = frozenset({
    "os",
    "sys",
    "subprocess",
    "pathlib",
    "builtins",
    "importlib",
})
_BROAD_EXCEPT_TARGETS = frozenset({
    "builtins.Exception",
    "builtins.BaseException",
})
_BROAD_EXCEPT_FIX = (
    "catch only expected operational errors "
    "(OSError, subprocess.TimeoutExpired, json.JSONDecodeError, UnicodeDecodeError, ValueError)"
)
_SUBPROCESS_CALL_TARGETS = frozenset({
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
})
_SUBPROCESS_NON_RUN_TARGETS = frozenset({
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
})
_SUBPROCESS_FIX = (
    'subprocess.run(argv_list, cwd=..., capture_output=True, text=True, '
    'errors="replace", check=False, timeout=...)'
)
_FILENAME_RE = re.compile(r"^[a-z_][a-z0-9_]*\.py$")
_RUNNER_MAIN_FIX = "define `def main(argv: list[str] | None = None) -> int:` at module level (§6)"
_RUNNER_ENTRYPOINT_FIX = 'use a single `if __name__ == "__main__": raise SystemExit(main())` block (§6)'

_COVERAGE_RUNNER: dict[str, str] = {
    "s1_reuse": "review",
    "s2_location": "review",
    "s2_filename": "static",
    "s2_vendored": "n/a",
    "s3_pure_io": "n/a",
    "s3_json_envelope": "n/a",
    "s3_unknown_not_verdict": "n/a",
    "s3_narrow_except": "n/a",
    "s4_explicit_context": "n/a",
    "s4_subprocess": "n/a",
    "s5_stdlib": "n/a",
    "s6_runner_main": "static",
    "s6_runner_thin": "review",
    "s6_exit_codes": "review",
}
_COVERAGE_BY_ROLE: dict[str, dict[str, str]] = {
    "component": {
        "s1_reuse": "review",
        "s2_location": "review",
        "s2_filename": "static",
        "s2_vendored": "n/a",
        "s3_pure_io": "static",
        "s3_json_envelope": "review",
        "s3_unknown_not_verdict": "review",
        "s3_narrow_except": "static",
        "s4_explicit_context": "static",
        "s4_subprocess": "static",
        "s5_stdlib": "static",
        "s6_runner_main": "n/a",
        "s6_runner_thin": "n/a",
        "s6_exit_codes": "n/a",
    },
    "runner": _COVERAGE_RUNNER,
    "runner_module": {
        "s1_reuse": "review",
        "s2_location": "review",
        "s2_filename": "static",
        "s2_vendored": "n/a",
        "s3_pure_io": "n/a",
        "s3_json_envelope": "n/a",
        "s3_unknown_not_verdict": "n/a",
        "s3_narrow_except": "n/a",
        "s4_explicit_context": "n/a",
        "s4_subprocess": "n/a",
        "s5_stdlib": "n/a",
        "s6_runner_main": "n/a",
        "s6_runner_thin": "review",
        "s6_exit_codes": "n/a",
    },
    "one_off": _COVERAGE_RUNNER,
    "vendored": {
        "s1_reuse": "n/a",
        "s2_location": "n/a",
        "s2_filename": "static",
        "s2_vendored": "static",
        "s3_pure_io": "n/a",
        "s3_json_envelope": "n/a",
        "s3_unknown_not_verdict": "n/a",
        "s3_narrow_except": "n/a",
        "s4_explicit_context": "n/a",
        "s4_subprocess": "n/a",
        "s5_stdlib": "n/a",
        "s6_runner_main": "n/a",
        "s6_runner_thin": "n/a",
        "s6_exit_codes": "n/a",
    },
}

_SCOPE_NODES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


@dataclass
class _Occurrence:
    scope: str
    name: str
    lineno: int
    kind: str  # "import" | "alias" | "other"
    target: str | None = None
    rhs_chain: list[str] | None = None
    scope_chain: tuple[int, ...] = (0,)
    rhs_scope_chain: tuple[int, ...] = (0,)


def _extract_attr_chain(node: ast.AST | None) -> list[str] | None:
    """Return dotted parts of an ast.Name / ast.Attribute / NamedExpr chain, or None."""
    if node is None:
        return None
    parts: list[str] = []
    cur: ast.AST = node
    while True:
        if isinstance(cur, ast.NamedExpr):
            cur = cur.value
        elif isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        else:
            break
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        parts.reverse()
        return parts
    return None


def _is_checked_target(target: str) -> bool:
    if target in _CHECKED_ROOTS:
        return True
    return any(target.startswith(f"{root}.") for root in _CHECKED_ROOTS)


def _is_checked_module(mod_name: str) -> bool:
    return any(mod_name == mod or mod_name.startswith(f"{mod}.") for mod in _CHECKED_MODULES)


def _iter_target_names(target: ast.AST) -> list[tuple[str, int]]:
    if isinstance(target, ast.Name):
        return [(target.id, target.lineno)]
    if isinstance(target, ast.Starred):
        return _iter_target_names(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        return [item for elt in target.elts for item in _iter_target_names(elt)]
    return []


def _iter_pattern_names(pattern: ast.AST) -> list[tuple[str, int]]:
    line = getattr(pattern, "lineno", 1)
    if isinstance(pattern, ast.MatchAs):
        out = _iter_pattern_names(pattern.pattern) if pattern.pattern else []
        return [*out, (pattern.name, line)] if pattern.name else out
    if isinstance(pattern, ast.MatchStar):
        return [(pattern.name, line)] if pattern.name else []
    if isinstance(pattern, ast.MatchMapping):
        out = [item for sub in pattern.patterns for item in _iter_pattern_names(sub)]
        return [*out, (pattern.rest, line)] if pattern.rest else out
    if isinstance(pattern, (ast.MatchSequence, ast.MatchOr)):
        return [item for sub in pattern.patterns for item in _iter_pattern_names(sub)]
    if isinstance(pattern, ast.MatchClass):
        subs = [*pattern.patterns, *pattern.kwd_patterns]
        return [item for sub in subs for item in _iter_pattern_names(sub)]
    return []


def _import_canonical(stmt: ast.Import | ast.ImportFrom, alias: ast.alias) -> tuple[str, str] | None:
    if isinstance(stmt, ast.Import):
        local = alias.asname or alias.name.split(".", 1)[0]
        target = alias.name if alias.asname else local
        return local, target
    if alias.name == "*":
        return None
    mod_prefix = "." * stmt.level + (stmt.module or "")
    local = alias.asname or alias.name
    if mod_prefix:
        sep = "" if mod_prefix.endswith(".") else "."
        return local, f"{mod_prefix}{sep}{alias.name}"
    return local, alias.name


def _scope_label(scope_node: ast.AST) -> str:
    if isinstance(scope_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return scope_node.name
    if isinstance(scope_node, ast.Lambda):
        return "lambda"
    if isinstance(scope_node, ast.ListComp):
        return "listcomp"
    if isinstance(scope_node, ast.SetComp):
        return "setcomp"
    if isinstance(scope_node, ast.DictComp):
        return "dictcomp"
    if isinstance(scope_node, ast.GeneratorExp):
        return "genexpr"
    return "module"


def _arg_occurrences(args: ast.arguments, scope_name: str, default_line: int) -> list[_Occurrence]:
    all_args = (
        *args.posonlyargs,
        *args.args,
        *args.kwonlyargs,
        *([args.vararg] if args.vararg else []),
        *([args.kwarg] if args.kwarg else []),
    )
    return [_Occurrence(scope_name, a.arg, getattr(a, "lineno", default_line), "other") for a in all_args]


def _is_matching_seq_pair(target: ast.AST, value: ast.AST | None) -> bool:
    return (
        isinstance(target, (ast.Tuple, ast.List))
        and isinstance(value, (ast.Tuple, ast.List))
        and len(target.elts) == len(value.elts)
        and all(not isinstance(e, ast.Starred) for e in (*target.elts, *value.elts))
    )


def _iter_target_value_pairs(target: ast.AST, value: ast.AST | None) -> list[tuple[str, int, ast.AST | None]]:
    if isinstance(target, ast.Name):
        return [(target.id, target.lineno, value)]
    if _is_matching_seq_pair(target, value):
        return [
            pair
            for t_elt, v_elt in zip(target.elts, value.elts)  # type: ignore[attr-defined]
            for pair in _iter_target_value_pairs(t_elt, v_elt)
        ]
    return [(nm, ln, None) for nm, ln in _iter_target_names(target)]


def _single_target_occurrence(target: ast.AST, value: ast.AST | None, scope: str) -> list[_Occurrence]:
    out: list[_Occurrence] = []
    for nm, ln, val in _iter_target_value_pairs(target, value):
        chain = _extract_attr_chain(val)
        if chain is not None:
            out.append(_Occurrence(scope, nm, ln, "alias", rhs_chain=chain))
        else:
            out.append(_Occurrence(scope, nm, ln, "other"))
    return out


def _assign_occurrences(node: ast.Assign, scope: str) -> list[_Occurrence]:
    return [occ for tgt in node.targets for occ in _single_target_occurrence(tgt, node.value, scope)]


def _node_occurrences(node: ast.AST, scope: str, walrus_scope: str | None = None) -> list[_Occurrence]:
    line = getattr(node, "lineno", 1)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        occs = _arg_occurrences(node.args, node.name, line)
        occs.extend(
            _Occurrence(node.name, tp.name, getattr(tp, "lineno", line), "other")
            for tp in getattr(node, "type_params", ())
            if hasattr(tp, "name")
        )
        return occs
    if isinstance(node, ast.ClassDef):
        return [
            _Occurrence(node.name, tp.name, getattr(tp, "lineno", line), "other")
            for tp in getattr(node, "type_params", ())
            if hasattr(tp, "name")
        ]
    if isinstance(node, ast.Lambda):
        return _arg_occurrences(node.args, "lambda", line)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return [
            _Occurrence(scope, nm, ln, "other")
            for gen in node.generators
            for nm, ln in _iter_target_names(gen.target)
        ]
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        pairs = [(a, _import_canonical(node, a)) for a in node.names]
        return [_Occurrence(scope, p[0], line, "import", target=p[1]) for _, p in pairs if p is not None]
    if isinstance(node, ast.Assign):
        return _assign_occurrences(node, scope)
    return _other_stmt_occurrences(node, scope, line, walrus_scope)


_COMP_SCOPE_NODES = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _other_stmt_occurrences(
    node: ast.AST,
    scope: str,
    line: int,
    walrus_scope: str | None = None,
) -> list[_Occurrence]:
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return _single_target_occurrence(node.target, node.value, scope)
    if isinstance(node, ast.Delete):
        return [_Occurrence(scope, nm, ln, "other") for tgt in node.targets for nm, ln in _iter_target_names(tgt)]
    if isinstance(node, (ast.AugAssign, ast.For, ast.AsyncFor, ast.comprehension)):
        return [_Occurrence(scope, nm, ln, "other") for nm, ln in _iter_target_names(node.target)]
    if isinstance(node, ast.withitem) and node.optional_vars is not None:
        return [_Occurrence(scope, nm, ln, "other") for nm, ln in _iter_target_names(node.optional_vars)]
    if isinstance(node, ast.ExceptHandler) and node.name:
        return [_Occurrence(scope, node.name, line, "other")]
    if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
        return _single_target_occurrence(node.target, node.value, walrus_scope or scope)
    if hasattr(ast, "match_case") and isinstance(node, ast.match_case):
        return [_Occurrence(scope, nm, ln, "other") for nm, ln in _iter_pattern_names(node.pattern)]
    if hasattr(ast, "TypeAlias") and isinstance(node, ast.TypeAlias):
        return [_Occurrence(scope, node.name.id, line, "other")]
    return []


def _walk_function_children(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    enclosing_scope: Any,
    inner_scope: Any,
    walk_fn: Any,
) -> None:
    for dec in node.decorator_list:
        walk_fn(dec, enclosing_scope)
    args = node.args
    for default in (*args.defaults, *(d for d in args.kw_defaults if d is not None)):
        walk_fn(default, enclosing_scope)
    all_args = (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg)
    for arg in all_args:
        if arg is not None and arg.annotation is not None:
            walk_fn(arg.annotation, enclosing_scope)
    if node.returns is not None:
        walk_fn(node.returns, enclosing_scope)
    for tp in getattr(node, "type_params", ()):
        walk_fn(tp, inner_scope)
    for stmt in node.body:
        walk_fn(stmt, inner_scope)


def _walk_comprehension_children(
    node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
    enclosing_scope: Any,
    inner_scope: Any,
    walk_fn: Any,
) -> None:
    if isinstance(node, ast.DictComp):
        walk_fn(node.key, inner_scope)
        walk_fn(node.value, inner_scope)
    else:
        walk_fn(node.elt, inner_scope)
    for idx, gen in enumerate(node.generators):
        walk_fn(gen.iter, enclosing_scope if idx == 0 else inner_scope)
        for if_clause in gen.ifs:
            walk_fn(if_clause, inner_scope)


def _walk_scope_children(node: ast.AST, enclosing_scope: Any, inner_scope: Any, walk_fn: Any) -> None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        _walk_function_children(node, enclosing_scope, inner_scope, walk_fn)
    elif isinstance(node, ast.ClassDef):
        for dec in node.decorator_list:
            walk_fn(dec, enclosing_scope)
        for base in node.bases:
            walk_fn(base, enclosing_scope)
        for kw in node.keywords:
            walk_fn(kw, enclosing_scope)
        for tp in getattr(node, "type_params", ()):
            walk_fn(tp, inner_scope)
        for stmt in node.body:
            walk_fn(stmt, inner_scope)
    elif isinstance(node, ast.Lambda):
        for default in (*node.args.defaults, *(d for d in node.args.kw_defaults if d is not None)):
            walk_fn(default, enclosing_scope)
        walk_fn(node.body, inner_scope)
    elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        _walk_comprehension_children(node, enclosing_scope, inner_scope, walk_fn)
    else:
        for child in ast.iter_child_nodes(node):
            walk_fn(child, inner_scope)


@dataclass(frozen=True)
class _ScopeState:
    label: str
    chain: tuple[int, ...]
    lex_chain: tuple[int, ...]
    walrus_label: str
    walrus_chain: tuple[int, ...]


@dataclass
class _WalkTables:
    raw_occs: list[tuple[_Occurrence, bool]]
    node_scopes: dict[int, tuple[int, ...]]
    chain_heads: list[tuple[int, int, str]]
    scope_labels: dict[int, str]
    scope_chains: dict[int, tuple[int, ...]]
    func_scopes: set[int]
    class_scopes: set[int]
    scope_globals: dict[int, set[str]]
    scope_nonlocals: dict[int, set[str]]
    scope_loads: dict[int, set[str]]


def _resolve_nonlocal_sid(
    chain: tuple[int, ...],
    name: str,
    func_scopes: set[int],
    local_bound: Mapping[int, set[str]],
) -> int:
    enclosing = [sid for sid in reversed(chain[:-1]) if sid in func_scopes]
    return next((sid for sid in enclosing if name in local_bound.get(sid, ())), enclosing[0] if enclosing else 0)


def _compute_local_bounds(wt: _WalkTables) -> tuple[dict[int, set[str]], dict[int, set[str]]]:
    local_bound: dict[int, set[str]] = {}
    non_def_bound: dict[int, set[str]] = {}
    for occ, is_class_def in wt.raw_occs:
        sid = occ.scope_chain[-1]
        if occ.name in wt.scope_globals.get(sid, ()) or occ.name in wt.scope_nonlocals.get(sid, ()):
            continue
        if not is_class_def:
            non_def_bound.setdefault(sid, set()).add(occ.name)
            local_bound.setdefault(sid, set()).add(occ.name)
        elif occ.name in wt.scope_loads.get(sid, ()):
            local_bound.setdefault(sid, set()).add(occ.name)
    return local_bound, non_def_bound


def _finalize_occurrences(wt: _WalkTables) -> list[_Occurrence]:
    local_bound, non_def_bound = _compute_local_bounds(wt)
    if not wt.scope_globals and not wt.scope_nonlocals:
        return [
            occ
            for occ, is_class_def in wt.raw_occs
            if not is_class_def
            or occ.name in wt.scope_loads.get(occ.scope_chain[-1], ())
            or occ.name in non_def_bound.get(occ.scope_chain[-1], ())
        ]
    for nid, sid, head in wt.chain_heads:
        if head in wt.scope_globals.get(sid, ()):
            wt.node_scopes[nid] = (0,)
        elif head in wt.scope_nonlocals.get(sid, ()):
            t_sid = _resolve_nonlocal_sid(wt.scope_chains[sid], head, wt.func_scopes, local_bound)
            wt.node_scopes[nid] = wt.scope_chains[t_sid]
    out: list[_Occurrence] = []
    for occ, is_class_def in wt.raw_occs:
        sid, nm = occ.scope_chain[-1], occ.name
        if nm in wt.scope_globals.get(sid, ()):
            occ.scope, occ.scope_chain = "module", (0,)
        elif nm in wt.scope_nonlocals.get(sid, ()):
            t_sid = _resolve_nonlocal_sid(occ.scope_chain, nm, wt.func_scopes, local_bound)
            occ.scope, occ.scope_chain = wt.scope_labels[t_sid], wt.scope_chains[t_sid]
        elif is_class_def and nm not in wt.scope_loads.get(sid, ()) and nm not in non_def_bound.get(sid, ()):
            continue
        if occ.rhs_chain:
            r_sid, r_head = occ.rhs_scope_chain[-1], occ.rhs_chain[0]
            if r_head in wt.scope_globals.get(r_sid, ()):
                occ.rhs_scope_chain = (0,)
            elif r_head in wt.scope_nonlocals.get(r_sid, ()):
                t_sid = _resolve_nonlocal_sid(occ.rhs_scope_chain, r_head, wt.func_scopes, local_bound)
                occ.rhs_scope_chain = wt.scope_chains[t_sid]
        out.append(occ)
    return out


def _enter_child_scope(node: ast.AST, sid: int, st: _ScopeState, wt: _WalkTables) -> _ScopeState:
    lbl = _scope_label(node)
    c_chain = (*st.lex_chain, sid)
    wt.scope_labels[sid], wt.scope_chains[sid] = lbl, c_chain
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        wt.func_scopes.add(sid)
    elif isinstance(node, ast.ClassDef):
        wt.class_scopes.add(sid)
    lex = st.lex_chain if isinstance(node, ast.ClassDef) else c_chain
    w_lbl = st.walrus_label if isinstance(node, _COMP_SCOPE_NODES) else lbl
    w_ch = st.walrus_chain if isinstance(node, _COMP_SCOPE_NODES) else c_chain
    return _ScopeState(lbl, c_chain, lex, w_lbl, w_ch)


def _record_walk_node(node: ast.AST, st: _ScopeState, inner: _ScopeState, wt: _WalkTables) -> None:
    cur_sid = st.chain[-1]
    wt.node_scopes[id(node)] = st.chain
    if isinstance(node, (ast.Name, ast.Attribute, ast.NamedExpr)) and (parts := _extract_attr_chain(node)) is not None:
        wt.chain_heads.append((id(node), cur_sid, parts[0]))
    if isinstance(node, ast.Global):
        wt.scope_globals.setdefault(cur_sid, set()).update(node.names)
    elif isinstance(node, ast.Nonlocal):
        wt.scope_nonlocals.setdefault(cur_sid, set()).update(node.names)
    elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        wt.scope_loads.setdefault(cur_sid, set()).add(node.id)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        occ = _Occurrence(st.label, node.name, node.lineno, "other", scope_chain=st.chain, rhs_scope_chain=st.chain)
        wt.raw_occs.append((occ, cur_sid in wt.class_scopes))
    for occ in _node_occurrences(node, inner.label, inner.walrus_label):
        occ.scope_chain = inner.walrus_chain if isinstance(node, ast.NamedExpr) else inner.chain
        occ.rhs_scope_chain = inner.chain
        wt.raw_occs.append((occ, False))


def _collect_occurrences(tree: ast.Module) -> tuple[list[_Occurrence], dict[int, tuple[int, ...]]]:
    wt = _WalkTables([], {}, [], {0: "module"}, {0: (0,)}, set(), set(), {}, {}, {})
    counter = 0

    def _walk(node: ast.AST, st: _ScopeState) -> None:
        nonlocal counter
        if isinstance(node, _SCOPE_NODES):
            counter += 1
            inner = _enter_child_scope(node, counter, st, wt)
        else:
            inner = st
        _record_walk_node(node, st, inner, wt)
        if isinstance(node, _SCOPE_NODES):
            _walk_scope_children(node, st, inner, _walk)
        else:
            for child in ast.iter_child_nodes(node):
                _walk(child, inner)

    _walk(tree, _ScopeState("module", (0,), (0,), "module", (0,)))
    return _finalize_occurrences(wt), wt.node_scopes


def _resolve_chain_in_scope(
    chain: list[str],
    scope_chain: tuple[int, ...],
    by_scope: Mapping[tuple[int, str], list[_Occurrence]],
    memo: dict[tuple[int, str], str | None],
    visiting: set[tuple[int, str]],
) -> str | None:
    head, tail = chain[0], chain[1:]
    base = _resolve_name_in_scope(head, scope_chain, by_scope, memo, visiting)
    return f"{base}.{'.'.join(tail)}" if (base and tail) else base


def _resolve_local_occ(
    sid: int,
    name: str,
    scope_chain: tuple[int, ...],
    occs: list[_Occurrence],
    by_scope: Mapping[tuple[int, str], list[_Occurrence]],
    memo: dict[tuple[int, str], str | None],
    visiting: set[tuple[int, str]],
) -> str | None:
    key = (sid, name)
    if key in memo:
        return memo[key]
    if key in visiting or any(o.kind == "other" for o in occs):
        memo[key] = None
        return None
    visiting.add(key)
    candidates: set[str | None] = {o.target for o in occs if o.kind == "import" and o.target is not None}
    if name in _FIXED:
        candidates.add(_FIXED[name])
    for o in occs:
        if o.rhs_chain is not None:
            rhs_sc = o.rhs_scope_chain or scope_chain
            candidates.add(_resolve_chain_in_scope(o.rhs_chain, rhs_sc, by_scope, memo, visiting))
    visiting.remove(key)
    res = next(iter(candidates)) if len(candidates) == 1 else None
    memo[key] = res
    return res


def _resolve_name_in_scope(
    name: str,
    scope_chain: tuple[int, ...],
    by_scope: Mapping[tuple[int, str], list[_Occurrence]],
    memo: dict[tuple[int, str], str | None],
    visiting: set[tuple[int, str]],
) -> str | None:
    for idx in range(len(scope_chain) - 1, -1, -1):
        sid = scope_chain[idx]
        occs = by_scope.get((sid, name))
        if occs:
            return _resolve_local_occ(sid, name, scope_chain[: idx + 1], occs, by_scope, memo, visiting)
    if name in _FIXED:
        return _FIXED[name]
    if name in _BUILTIN_NAMES:
        return f"builtins.{name}"
    return None


def _index_occurrences(occurrences: list[_Occurrence]) -> dict[tuple[int, str], list[_Occurrence]]:
    by_scope: dict[tuple[int, str], list[_Occurrence]] = {}
    for o in occurrences:
        sid = o.scope_chain[-1] if o.scope_chain else 0
        by_scope.setdefault((sid, o.name), []).append(o)
    return by_scope


def _discover_bindings(tree: ast.Module) -> tuple[list[_Occurrence], set[str], dict[int, tuple[int, ...]]]:
    """Pass 1: finite cycle-safe alias resolution and checked-name discovery."""
    occurrences, node_scopes = _collect_occurrences(tree)
    by_scope = _index_occurrences(occurrences)
    memo: dict[tuple[int, str], str | None] = {}
    for o in occurrences:
        if o.rhs_chain is not None:
            rhs_sc = o.rhs_scope_chain or o.scope_chain or (0,)
            o.target = _resolve_chain_in_scope(o.rhs_chain, rhs_sc, by_scope, memo, {(o.scope_chain[-1], o.name)})
            o.kind = "alias" if o.target is not None else "other"

    checked_names = set(_FIXED) | {
        o.name
        for o in occurrences
        if o.kind in ("import", "alias") and o.target is not None and _is_checked_target(o.target)
    }
    return occurrences, checked_names, node_scopes


def _candidate_targets(name: str, occurrences: list[_Occurrence]) -> set[str]:
    targets = {
        o.target
        for o in occurrences
        if o.name == name and o.kind in ("import", "alias") and o.target is not None
    }
    if name in _FIXED:
        targets.add(_FIXED[name])
    return targets


def _is_flagged_occurrence(
    occ: _Occurrence,
    by_scope: Mapping[tuple[int, str], list[_Occurrence]],
) -> bool:
    for idx in range(len(occ.scope_chain) - 1, -1, -1):
        sc_occs = by_scope.get((occ.scope_chain[idx], occ.name), ())
        checked_targets = {
            o.target
            for o in sc_occs
            if o.kind in ("import", "alias") and o.target is not None and _is_checked_target(o.target)
        }
        if idx == len(occ.scope_chain) - 1 and occ.name in _FIXED:
            checked_targets.add(_FIXED[occ.name])
        if checked_targets:
            if idx < len(occ.scope_chain) - 1:
                if occ.kind not in ("import", "alias") or occ.target is None or checked_targets != {occ.target}:
                    return True
            elif occ.kind == "other" or len(_candidate_targets(occ.name, list(sc_occs))) != 1:
                return True
    return False


def _validate_checked_occurrences(
    path: str,
    occurrences: list[_Occurrence],
    checked_names: set[str],
) -> list[tuple[int, dict[str, Any]]]:
    """Pass 2: per-occurrence validation of checked names across visible scopes."""
    by_scope = _index_occurrences(occurrences)
    flagged = [
        occ
        for occ in occurrences
        if occ.name in checked_names and _is_flagged_occurrence(occ, by_scope)
    ]
    findings: list[tuple[int, dict[str, Any]]] = []
    seen: set[tuple[str, int]] = set()
    for occ in sorted(flagged, key=lambda item: (item.lineno, item.name)):
        key = (occ.name, occ.lineno)
        if key in seen:
            continue
        seen.add(key)
        findings.append(_error_finding(
            path,
            occ.lineno,
            "unverifiable-checked-binding",
            (
                f"name {occ.name} rebinds a checked name in scope {occ.scope}; "
                "the gate cannot verify what this name refers to after rebinding"
            ),
            "use a different local name, or pass the value in as a differently named parameter",
        ))
    return findings


def _validate_star_imports(path: str, tree: ast.Module) -> list[tuple[int, dict[str, Any]]]:
    findings: list[tuple[int, dict[str, Any]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if not any(alias.name == "*" for alias in node.names):
            continue
        mod_prefix = "." * node.level + (node.module or "")
        is_checked = node.level == 0 and bool(node.module) and _is_checked_module(node.module)
        emit = _error_finding if is_checked else _warn_finding
        findings.append(emit(
            path,
            node.lineno,
            "unverifiable-star-import",
            f"star import from {mod_prefix} prevents static verification of imported bindings",
            "import the names you need explicitly",
        ))
    return findings


class _Bindings(dict[str, str]):
    """Dictionary of local->canonical bindings carrying all bound names in source."""

    def __init__(
        self,
        resolved: Mapping[str, str] | None = None,
        bound_names: set[str] | None = None,
        pathlib_instances: set[str] | None = None,
        scope_resolved: Mapping[int, Mapping[str, str]] | None = None,
        scope_bound: Mapping[int, set[str]] | None = None,
        node_scopes: Mapping[int, tuple[int, ...]] | None = None,
    ) -> None:
        super().__init__(resolved or {})
        self.bound_names: set[str] = set(bound_names) if bound_names is not None else set(self)
        self.pathlib_instances: set[str] = set(pathlib_instances or ())
        self.scope_resolved: dict[int, dict[str, str]] = {
            k: dict(v) for k, v in (scope_resolved or {}).items()
        }
        self.scope_bound: dict[int, set[str]] = {
            k: set(v) for k, v in (scope_bound or {}).items()
        }
        self.node_scopes: dict[int, tuple[int, ...]] = dict(node_scopes or {})

    @classmethod
    def from_tree(cls, tree: ast.Module) -> _Bindings:
        return _resolve_bindings(tree)

    def canonical(self, node: ast.AST) -> str | None:
        return _canonical(node, self)


def _resolve_bindings(tree: ast.Module) -> _Bindings:
    """Return scope-aware bindings with module/unambiguous flat map on dict interface."""
    occurrences, _, node_scopes = _discover_bindings(tree)
    bindings = _bindings_from_occurrences(occurrences, node_scopes)
    bindings.pathlib_instances = _discover_pathlib_instances(tree, bindings)
    return bindings


def _is_bound_in_scope(name: str, node: ast.AST, bindings: Mapping[str, str]) -> bool:
    node_scopes = getattr(bindings, "node_scopes", None)
    scope_bound = getattr(bindings, "scope_bound", None)
    if node_scopes is not None and scope_bound is not None:
        sc = node_scopes.get(id(node))
        if sc is not None:
            return any(name in scope_bound.get(sid, ()) for sid in sc)
    return name in bindings or name in getattr(bindings, "bound_names", ())


def _reflect_container_module(node: ast.AST, bindings: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Name) and node.id == "__builtins__":
        if _is_bound_in_scope(node.id, node, bindings):
            return None
        return "builtins"
    if isinstance(node, ast.Attribute) and node.attr == "__dict__":
        mod = _canonical(node.value, bindings)
        return mod if mod in _CHECKED_MODULES else None
    if isinstance(node, ast.Call) and len(node.args) == 1 and not node.keywords:
        fn = _canonical(node.func, bindings)
        arg0 = _canonical(node.args[0], bindings)
        if fn in ("builtins.vars", "builtins.type") and arg0 in _CHECKED_MODULES:
            return arg0
    return None


def _literal_str_val(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and type(node.value) is str:
        return node.value
    return None


def _resolve_reflected_canonical(node: ast.AST, bindings: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Attribute):
        base = _resolve_reflected_canonical(node.value, bindings)
        return f"{base}.{node.attr}" if base else None
    if isinstance(node, ast.Subscript):
        key = _literal_str_val(node.slice)
        if key is None:
            return None
        if _canonical(node.value, bindings) == "sys.modules":
            return key if _is_checked_module(key) else None
        mod = _reflect_container_module(node.value, bindings)
        return f"{mod}.{key}" if mod else None
    if isinstance(node, ast.Call) and len(node.args) >= 2:
        fn = _canonical(node.func, bindings)
        arg0 = _canonical(node.args[0], bindings)
        attr = _literal_str_val(node.args[1])
        if fn == "builtins.getattr" and attr:
            if arg0 in _DYNAMIC_CHECKED_MODULES or arg0 == "os.environ":
                return f"{arg0}.{attr}"
    return None


def _lookup_head_in_scope(head: str, node: ast.AST, bindings: Mapping[str, str]) -> str | None:
    node_scopes = getattr(bindings, "node_scopes", None)
    scope_resolved = getattr(bindings, "scope_resolved", None)
    scope_bound = getattr(bindings, "scope_bound", None)
    if node_scopes is not None and scope_resolved is not None and scope_bound is not None:
        sc = node_scopes.get(id(node))
        if sc is not None:
            for sid in reversed(sc):
                if head in scope_bound.get(sid, ()):
                    return scope_resolved.get(sid, {}).get(head)
            return f"builtins.{head}" if head in _BUILTIN_NAMES else None
    bound_names = getattr(bindings, "bound_names", None)
    if head in bindings:
        return bindings[head]
    if bound_names is not None and head in bound_names:
        return None
    return f"builtins.{head}" if head in _BUILTIN_NAMES else None


def _canonical(node: ast.AST, bindings: Mapping[str, str]) -> str | None:
    """Resolve an ast.Name, ast.Attribute, or literal reflection to a canonical dotted name."""
    parts = _extract_attr_chain(node)
    if parts is None:
        return _resolve_reflected_canonical(node, bindings)
    base = _lookup_head_in_scope(parts[0], node, bindings)
    if base is None:
        return None
    tail = parts[1:]
    return f"{base}.{'.'.join(tail)}" if tail else base


def _bindings_from_occurrences(
    occurrences: list[_Occurrence],
    node_scopes: Mapping[int, tuple[int, ...]] | None = None,
) -> _Bindings:
    by_scope = _index_occurrences(occurrences)
    scope_bound: dict[int, set[str]] = {}
    scope_resolved: dict[int, dict[str, str]] = {}
    for (sid, name), occs in by_scope.items():
        scope_bound.setdefault(sid, set()).add(name)
        if any(o.kind == "other" for o in occs):
            continue
        targets = _candidate_targets(name, occs)
        if len(targets) == 1:
            scope_resolved.setdefault(sid, {})[name] = next(iter(targets))

    by_name: dict[str, list[_Occurrence]] = {}
    for o in occurrences:
        by_name.setdefault(o.name, []).append(o)
    resolved: dict[str, str] = dict(scope_resolved.get(0, {}))
    mod_bound = scope_bound.get(0, ())
    for name, name_occs in by_name.items():
        if name in mod_bound or any(o.kind == "other" for o in name_occs):
            continue
        targets = _candidate_targets(name, name_occs)
        if len(targets) == 1:
            resolved[name] = next(iter(targets))
    return _Bindings(
        resolved,
        bound_names=set(by_name),
        scope_resolved=scope_resolved,
        scope_bound=scope_bound,
        node_scopes=node_scopes,
    )


def _is_pathlib_call(
    node: ast.AST,
    bindings: Mapping[str, str],
    path_vars: set[str] | frozenset[str] | None = None,
) -> bool:
    active_vars: set[str] | frozenset[str] = (
        path_vars if path_vars is not None else frozenset(getattr(bindings, "pathlib_instances", ()))
    )
    if isinstance(node, ast.Name):
        return node.id in active_vars
    if not isinstance(node, ast.Call):
        return False
    func_canon = _canonical(node.func, bindings)
    if func_canon in _PATHLIB_CTOR_TARGETS:
        return True
    return isinstance(node.func, ast.Attribute) and _is_pathlib_call(node.func.value, bindings, active_vars)


def _scope_path_vars(
    scope_node: ast.AST,
    bindings: Mapping[str, str],
    outer_vars: frozenset[str],
    mod_vars: frozenset[str] = frozenset(),
    mod_assigns: Mapping[str, list[ast.AST | None]] | None = None,
) -> frozenset[str]:
    assigns, globs = _collect_scope_env(scope_node, mod_assigns)
    local_vars = (set(outer_vars) - set(assigns) - globs) | (globs & set(mod_vars))
    changed = True
    while changed:
        changed = False
        for nm, vals in assigns.items():
            if (
                nm not in local_vars
                and vals
                and all(v is not None and _is_pathlib_call(v, bindings, local_vars) for v in vals)
            ):
                local_vars.add(nm)
                changed = True
    return frozenset(local_vars)


def _discover_pathlib_instances(tree: ast.Module, bindings: Mapping[str, str]) -> set[str]:
    mod_assigns = _collect_module_assigns(tree)
    return set(_scope_path_vars(tree, bindings, frozenset(), mod_assigns=mod_assigns))


def _error_finding(path: str, lineno: int, fid: str, detail: str, fix: str) -> tuple[int, dict[str, Any]]:
    return lineno, {"id": fid, "severity": "error", "detail": f"{path}:{lineno} {detail}", "fix": fix}


def _warn_finding(path: str, lineno: int, fid: str, detail: str, fix: str) -> tuple[int, dict[str, Any]]:
    return lineno, {"id": fid, "severity": "warn", "detail": f"{path}:{lineno} {detail}", "fix": fix}


def _check_import_mod(path: str, lineno: int, mod: str, prefix: str) -> list[tuple[int, dict[str, Any]]]:
    top = mod.split(".", 1)[0]
    if top == "argparse":
        return [_error_finding(path, lineno, "banned-import", f"{prefix} {mod}", "parse arguments in the runner")]
    if top not in sys.stdlib_module_names and top != "__future__":
        return [_error_finding(path, lineno, "non-stdlib-import", f"non-stdlib {prefix} {mod}", "components are stdlib-only (§5)")]
    return []


def _check_component_imports(path: str, tree: ast.Module) -> list[tuple[int, dict[str, Any]]]:
    out: list[tuple[int, dict[str, Any]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.extend(_check_import_mod(path, node.lineno, alias.name, "import of"))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                mod_prefix = "." * node.level + (node.module or "")
                out.append(_error_finding(
                    path, node.lineno, "relative-import",
                    f"relative import from {mod_prefix}",
                    "no sibling imports inside the shared package (§5)",
                ))
            elif node.module:
                out.extend(_check_import_mod(path, node.lineno, node.module, "import from"))
    return out


def _is_literal_bool(node: ast.AST | None, expected: bool) -> bool:
    return isinstance(node, ast.Constant) and type(node.value) is bool and node.value is expected


def _is_literal_str(node: ast.AST | None, expected: str | None = None) -> bool:
    val = _literal_str_val(node)
    return val is not None and (expected is None or val == expected)


def _is_os_subprocess_target(canon: str | None) -> bool:
    if canon is None:
        return False
    if canon in ("os.system", "os.popen"):
        return True
    return canon.startswith("os.spawn") or canon.startswith("os.exec")


# Only subprocess.run accepts the full §4 contract (timeout, capture_output, check);
# Popen has no timeout/check/capture_output parameters and call/check_call/check_output
# lack check/capture_output or hardcode check=True, so non-run calls must use subprocess.run.
_SUBPROCESS_KW_RULES: tuple[tuple[str, Any, str], ...] = (
    ("shell", lambda m: "shell" not in m or _is_literal_bool(m["shell"], False), "shell must be absent or False"),
    ("cwd", lambda m: "cwd" in m, "missing required keyword cwd"),
    ("timeout", lambda m: "timeout" in m, "missing required keyword timeout"),
    ("capture_output", lambda m: _is_literal_bool(m.get("capture_output"), True), "missing or non-True capture_output=True"),
    (
        "text",
        lambda m: _is_literal_bool(m.get("text"), True) or _is_literal_bool(m.get("universal_newlines"), True),
        "missing or non-True text=True",
    ),
    ("errors", lambda m: _is_literal_str(m.get("errors"), "replace"), "missing or invalid errors='replace'"),
    ("check", lambda m: _is_literal_bool(m.get("check"), False), "missing or non-False check=False"),
)


_STR_BYTES_CTORS = frozenset({
    "builtins.str",
    "builtins.bytes",
    "builtins.bytearray",
})


def _is_str_expr(node: ast.AST | None, bindings: Mapping[str, str] | None = None) -> bool:
    if _is_literal_str(node) or isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp):
        return _is_str_expr(node.left, bindings) or _is_str_expr(node.right, bindings)
    if isinstance(node, ast.Call):
        if bindings is not None and _canonical(node.func, bindings) in _STR_BYTES_CTORS:
            return True
        if isinstance(node.func, ast.Name) and node.func.id in ("str", "bytes", "bytearray") and bindings is None:
            return True
        if isinstance(node.func, ast.Attribute):
            recv = node.func.value
            if _is_str_expr(recv, bindings) or (isinstance(recv, ast.Name) and recv.id == "str"):
                return True
            if node.func.attr in ("format", "format_map", "join", "strip", "lstrip", "rstrip", "lower", "upper", "replace"):
                return True
    return False


def _is_invalid_argv_expr(
    node: ast.AST | None,
    bindings: Mapping[str, str] | None = None,
    scope_assigns: Mapping[str, list[ast.AST | None]] | None = None,
    visiting: frozenset[str] = frozenset(),
) -> bool:
    if node is None or isinstance(node, ast.Starred):
        return False
    if isinstance(node, ast.Tuple):
        return True
    if isinstance(node, ast.Constant) and type(node.value) in (str, bytes):
        return True
    if _is_str_expr(node, bindings):
        return True
    if isinstance(node, ast.Name) and scope_assigns is not None and node.id not in visiting:
        vals = scope_assigns.get(node.id, ())
        if len(vals) == 1 and vals[0] is not None:
            return _is_invalid_argv_expr(vals[0], bindings, scope_assigns, visiting | {node.id})
    return False


def _check_subprocess_call(
    path: str,
    node: ast.Call,
    func_canon: str,
    bindings: Mapping[str, str] | None = None,
    scope_assigns: Mapping[str, list[ast.AST | None]] | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    ln = node.lineno

    def _emit(msg: str) -> tuple[int, dict[str, Any]]:
        return _error_finding(path, ln, "subprocess-contract", f"{func_canon}: {msg}", _SUBPROCESS_FIX)

    if func_canon in _SUBPROCESS_NON_RUN_TARGETS:
        return [_emit("use subprocess.run(..., check=False) with the §4 contract")]
    if any(kw.arg is None for kw in node.keywords):
        return [_emit("cannot verify keywords passed via **")]
    kw_map: dict[str, ast.AST] = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
    out: list[tuple[int, dict[str, Any]]] = []
    has_starred = any(isinstance(a, ast.Starred) for a in node.args)
    if has_starred or len(node.args) > 1 or (node.args and "args" in kw_map):
        out.append(_emit("pass only argv positionally; everything else by keyword"))
    elif not node.args and "args" not in kw_map:
        out.append(_emit("missing required argv"))
    argv_expr = node.args[0] if node.args else kw_map.get("args")
    if _is_invalid_argv_expr(argv_expr, bindings, scope_assigns):
        out.append(_emit("argv must be a list, not a string"))
    for _kw, pred, msg in _SUBPROCESS_KW_RULES:
        if not pred(kw_map):
            out.append(_emit(msg))
    return out


def _resolve_getattr_builtin(node: ast.AST, bindings: Mapping[str, str]) -> str | None:
    if not isinstance(node, ast.Call) or _canonical(node.func, bindings) != "builtins.getattr":
        return None
    if len(node.args) < 2 or any(isinstance(a, ast.Starred) for a in node.args[:2]):
        return None
    arg0, arg1 = node.args[0], node.args[1]
    bound = getattr(bindings, "bound_names", ())
    is_builtins = _canonical(arg0, bindings) == "builtins" or (
        isinstance(arg0, ast.Name) and arg0.id == "builtins" and "builtins" not in bound
    )
    if is_builtins and (s_val := _literal_str_val(arg1)) is not None:
        return f"builtins.{s_val}" if s_val in _BUILTIN_NAMES else None
    return None


def _find_broad_exc(
    expr: ast.AST | None,
    bindings: Mapping[str, str],
    env: Mapping[str, list[ast.AST | None]],
    visiting: frozenset[str] = frozenset(),
) -> str | None:
    if expr is None:
        return None
    if isinstance(expr, ast.Starred):
        expr = expr.value
    if isinstance(expr, (ast.Tuple, ast.List, ast.Set)):
        for elt in expr.elts:
            hit = _find_broad_exc(elt, bindings, env, visiting)
            if hit is not None:
                return hit
        return None
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, (ast.Add, ast.BitOr)):
        return _find_broad_exc(expr.left, bindings, env, visiting) or _find_broad_exc(expr.right, bindings, env, visiting)
    if isinstance(expr, ast.Call) and _canonical(expr.func, bindings) == "builtins.tuple" and len(expr.args) == 1:
        return _find_broad_exc(expr.args[0], bindings, env, visiting)
    ga = _resolve_getattr_builtin(expr, bindings)
    if ga in _BROAD_EXCEPT_TARGETS:
        return ga
    canon = _canonical(expr, bindings)
    if canon in _BROAD_EXCEPT_TARGETS:
        return canon
    if isinstance(expr, ast.Name) and expr.id not in visiting:
        for rhs in env.get(expr.id, ()):
            if rhs is not None:
                hit = _find_broad_exc(rhs, bindings, env, visiting | {expr.id})
                if hit is not None:
                    return hit
    return None


def _is_verifiable_except_type(
    expr: ast.AST,
    bindings: Mapping[str, str],
    env: Mapping[str, list[ast.AST | None]] | None = None,
    visiting: frozenset[str] = frozenset(),
) -> bool:
    if isinstance(expr, ast.Starred):
        return _is_verifiable_except_type(expr.value, bindings, env, visiting)
    if isinstance(expr, ast.Tuple):
        return all(_is_verifiable_except_type(elt, bindings, env, visiting) for elt in expr.elts)
    if _resolve_getattr_builtin(expr, bindings) is not None:
        return True
    if isinstance(expr, ast.Name):
        if env and expr.id not in visiting:
            vals = env.get(expr.id, ())
            assigned = [v for v in vals if v is not None]
            if assigned and len(assigned) == len(vals):
                return all(
                    _is_verifiable_except_type(v, bindings, env, visiting | {expr.id})
                    for v in assigned
                )
        return True
    return isinstance(expr, ast.Attribute) and _extract_attr_chain(expr) is not None


def _check_except_handler(
    path: str,
    node: ast.ExceptHandler,
    bindings: Mapping[str, str],
    env: Mapping[str, list[ast.AST | None]] | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    ln = node.lineno
    if node.type is None:
        return [_error_finding(path, ln, "broad-except", "bare except:", _BROAD_EXCEPT_FIX)]
    broad = _find_broad_exc(node.type, bindings, env or {})
    if broad is not None:
        return [_error_finding(path, ln, "broad-except", f"broad exception handler catching {broad}", _BROAD_EXCEPT_FIX)]
    if not _is_verifiable_except_type(node.type, bindings, env):
        return [_warn_finding(path, ln, "unverifiable-except", "exception handler type is not a static Name/Attribute/Tuple", _BROAD_EXCEPT_FIX)]
    return []


def _node_rhs_bindings(node: ast.AST) -> list[tuple[str, ast.AST | None]]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        args = node.args
        all_args = (
            *args.posonlyargs, *args.args, *args.kwonlyargs,
            *([args.vararg] if args.vararg else []),
            *([args.kwarg] if args.kwarg else []),
        )
        return [(a.arg, None) for a in all_args]
    if isinstance(node, ast.Assign):
        if len(node.targets) == 1:
            return [(nm, val) for nm, _, val in _iter_target_value_pairs(node.targets[0], node.value)]
        return [(nm, node.value if isinstance(t, ast.Name) else None) for t in node.targets for nm, _ in _iter_target_names(t)]
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return [(nm, val) for nm, _, val in _iter_target_value_pairs(node.target, node.value)]
    if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
        return [(node.target.id, node.value)]
    if isinstance(node, (ast.AugAssign, ast.For, ast.AsyncFor, ast.comprehension)):
        return [(nm, None) for nm, _ in _iter_target_names(node.target)]
    if isinstance(node, ast.Delete):
        return [(nm, None) for tgt in node.targets for nm, _ in _iter_target_names(tgt)]
    if isinstance(node, ast.withitem) and node.optional_vars is not None:
        return [(nm, None) for nm, _ in _iter_target_names(node.optional_vars)]
    if isinstance(node, ast.ExceptHandler) and node.name:
        return [(node.name, None)]
    return []


def _collect_scope_env(
    scope_node: ast.AST,
    mod_assigns: Mapping[str, list[ast.AST | None]] | None = None,
) -> tuple[dict[str, list[ast.AST | None]], set[str]]:
    if isinstance(scope_node, ast.Module) and mod_assigns is not None:
        return {nm: list(vals) for nm, vals in mod_assigns.items()}, set()
    assigns: dict[str, list[ast.AST | None]] = {}
    globs: set[str] = set()
    for nm, val in _node_rhs_bindings(scope_node):
        assigns.setdefault(nm, []).append(val)

    def _scan(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Global):
                globs.update(child.names)
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                assigns.setdefault(child.name, []).append(None)
            if isinstance(child, _SCOPE_NODES):
                continue
            for nm, val in _node_rhs_bindings(child):
                assigns.setdefault(nm, []).append(val)
            _scan(child)

    _scan(scope_node)
    for nm in globs:
        assigns.pop(nm, None)
    return assigns, globs


def _collect_module_assigns(tree: ast.Module) -> dict[str, list[ast.AST | None]]:
    mod_assigns, _ = _collect_scope_env(tree)

    def _walk_globals(node: ast.AST, active_globs: frozenset[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _SCOPE_NODES):
                _, child_globs = _collect_scope_env(child)
                inner_globs = (active_globs | child_globs) if isinstance(child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.Lambda)) else frozenset(child_globs)
                _walk_globals(child, inner_globs)
            else:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child.name in active_globs:
                    mod_assigns.setdefault(child.name, []).append(None)
                for nm, val in _node_rhs_bindings(child):
                    if nm in active_globs:
                        mod_assigns.setdefault(nm, []).append(val)
                _walk_globals(child, active_globs)

    _walk_globals(tree, frozenset())
    return mod_assigns


def _check_finally_body(
    path: str,
    stmts: list[ast.stmt],
    in_loop: bool = False,
) -> list[tuple[int, dict[str, Any]]]:
    out: list[tuple[int, dict[str, Any]]] = []
    fix = "remove return/break/continue from finally block so in-flight exceptions propagate"
    for stmt in stmts:
        if isinstance(stmt, ast.Return):
            out.append(_error_finding(path, stmt.lineno, "finally-swallows", "return inside finally: discards in-flight exceptions", fix))
        elif isinstance(stmt, (ast.Break, ast.Continue)) and not in_loop:
            kw = "break" if isinstance(stmt, ast.Break) else "continue"
            out.append(_error_finding(path, stmt.lineno, "finally-swallows", f"{kw} inside finally: discards in-flight exceptions", fix))
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        is_loop = isinstance(stmt, (ast.For, ast.AsyncFor, ast.While))
        is_try = isinstance(stmt, ast.Try) or (hasattr(ast, "TryStar") and isinstance(stmt, ast.TryStar))
        for field, val in ast.iter_fields(stmt):
            if field == "finalbody" and is_try:
                continue
            field_loop = True if (is_loop and field == "body") else in_loop
            if isinstance(val, list):
                child_stmts = [x for x in val if isinstance(x, ast.stmt)]
                if child_stmts:
                    out.extend(_check_finally_body(path, child_stmts, field_loop))
                for item in val:
                    if isinstance(item, ast.ExceptHandler):
                        out.extend(_check_finally_body(path, item.body, field_loop))
                    elif hasattr(ast, "match_case") and isinstance(item, ast.match_case):
                        out.extend(_check_finally_body(path, item.body, field_loop))
    return out


def _check_tree_exception_suppression(
    path: str,
    tree: ast.Module,
    bindings: Mapping[str, str] | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    b = bindings if bindings is not None else _resolve_bindings(tree)
    mod_env = _collect_module_assigns(tree)
    out: list[tuple[int, dict[str, Any]]] = []

    def _walk(
        node: ast.AST,
        state: tuple[Mapping[str, list[ast.AST | None]], Mapping[str, list[ast.AST | None]]],
    ) -> None:
        env, lex_env = state
        if isinstance(node, (ast.Module, *_SCOPE_NODES)):
            assigns, globs = _collect_scope_env(node, mod_env)
            base = {k: v for k, v in lex_env.items() if k not in globs}
            cur_env = {**base, **{nm: mod_env[nm] for nm in globs if nm in mod_env}, **assigns}
            cur_state = (cur_env, lex_env if isinstance(node, ast.ClassDef) else cur_env)
        else:
            cur_env, cur_state = env, state
        if isinstance(node, ast.ExceptHandler):
            out.extend(_check_except_handler(path, node, b, cur_env))
        elif isinstance(node, ast.Call) and _canonical(node.func, b) == "contextlib.suppress":
            for arg in node.args:
                if (broad := _find_broad_exc(arg, b, cur_env)) is not None:
                    out.append(_error_finding(path, node.lineno, "broad-except", f"contextlib.suppress catching {broad}", _BROAD_EXCEPT_FIX))
                    break
        elif (isinstance(node, ast.Try) or (hasattr(ast, "TryStar") and isinstance(node, ast.TryStar))) and node.finalbody:
            out.extend(_check_finally_body(path, node.finalbody))
        if isinstance(node, _SCOPE_NODES):
            _walk_scope_children(node, state, cur_state, _walk)
        else:
            for child in ast.iter_child_nodes(node):
                _walk(child, cur_state)

    _walk(tree, ({}, {}))
    return out


def _is_checked_callable(canon: str | None) -> bool:
    if canon is None:
        return False
    return (
        canon in _BANNED_CALL_TARGETS
        or canon in _SUBPROCESS_CALL_TARGETS
        or _is_os_subprocess_target(canon)
    )


def _check_callable_target(
    path: str,
    ln: int,
    func_canon: str | None,
    call_node: ast.Call | None = None,
    bindings: Mapping[str, str] | None = None,
    scope_assigns: Mapping[str, list[ast.AST | None]] | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    if func_canon in _BANNED_CALL_TARGETS:
        return [_error_finding(path, ln, "banned-call", f"call to {func_canon}", "return data; let the runner print/exit")]
    if func_canon in _SUBPROCESS_CALL_TARGETS:
        if call_node is not None:
            return _check_subprocess_call(path, call_node, func_canon, bindings, scope_assigns)
        return [_error_finding(path, ln, "subprocess-contract", f"{func_canon}: use subprocess.run with the §4 contract", _SUBPROCESS_FIX)]
    if _is_os_subprocess_target(func_canon) and func_canon is not None:
        return [_error_finding(path, ln, "subprocess-contract", f"call to {func_canon}; use subprocess.run with the §4 contract", _SUBPROCESS_FIX)]
    return []


def _check_dynamic_attr_call(
    path: str,
    node: ast.Call,
    func_canon: str,
    bindings: Mapping[str, str],
    called_ids: set[int] | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    if not node.args:
        return []
    ln = node.lineno
    arg0 = _canonical(node.args[0], bindings)
    if arg0 not in _DYNAMIC_CHECKED_MODULES and arg0 != "os.environ":
        return []
    attr = _literal_str_val(node.args[1]) if len(node.args) >= 2 else None
    if func_canon in ("builtins.getattr", "builtins.hasattr") and attr and arg0 in _DYNAMIC_CHECKED_MODULES:
        resolved = f"{arg0}.{attr}"
        if resolved in _AMBIENT_TARGETS:
            return [_error_finding(path, ln, "ambient-context", f"ambient reference to {resolved}", "take the path/env mapping as a required parameter")]
        is_flagged_target = (func_canon == "builtins.getattr" and _is_checked_callable(resolved)) or resolved in _DYNAMIC_CALL_TARGETS
        if not is_flagged_target or (called_ids is not None and id(node) in called_ids):
            return []
    return [_warn_finding(path, ln, "unverifiable-dynamic-access", f"dynamic attribute access on {arg0} via {func_canon}", "use direct access the gate can verify")]


def _check_component_call(
    path: str,
    node: ast.Call,
    bindings: Mapping[str, str],
    called_ids: set[int] | None = None,
    path_vars: frozenset[str] | None = None,
    scope_assigns: Mapping[str, list[ast.AST | None]] | None = None,
    annotation_ids: set[int] | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    out: list[tuple[int, dict[str, Any]]] = []
    ln = node.lineno
    func_canon = _canonical_with_env(node.func, bindings, scope_assigns)
    out.extend(_check_callable_target(path, ln, func_canon, node, bindings, scope_assigns))
    if isinstance(node.func, ast.Attribute) and func_canon not in _AMBIENT_TARGETS:
        attr = node.func.attr
        if attr == "expanduser" or (attr in ("home", "cwd") and _is_pathlib_call(node.func.value, bindings, path_vars)):
            out.append(_error_finding(path, ln, "ambient-context", f"ambient call .{attr}()", "take the path/env mapping as a required parameter"))
    if func_canon in _DYNAMIC_CALL_TARGETS:
        out.append(_warn_finding(path, ln, "unverifiable-dynamic-access", f"dynamic call to {func_canon}", "use direct access the gate can verify"))
    elif func_canon in _DYNAMIC_ATTR_BUILTINS:
        out.extend(_check_dynamic_attr_call(path, node, func_canon, bindings, called_ids))
    if annotation_ids is None or id(node) not in annotation_ids:
        for arg in (*node.args, *(kw.value for kw in node.keywords)):
            arg_canon = _canonical_with_env(arg, bindings, scope_assigns)
            if _is_checked_callable(arg_canon):
                out.append(_warn_finding(path, getattr(arg, "lineno", ln), "unverifiable-dynamic-access", f"checked callable {arg_canon} passed as argument", "call directly or move to runner"))
    return out


def _collect_destructured_rhs_ids(target: ast.AST, value: ast.AST | None) -> set[int]:
    if _is_matching_seq_pair(target, value):
        ids = {id(value)}
        for t_elt, v_elt in zip(target.elts, value.elts):  # type: ignore[attr-defined]
            ids.update(_collect_destructured_rhs_ids(t_elt, v_elt))
        return ids
    return set()


def _reflect_source_target(
    node: ast.AST,
    bindings: Mapping[str, str],
    env: Mapping[str, list[ast.AST | None]] | None = None,
    visiting: frozenset[str] = frozenset(),
) -> str | None:
    if _canonical(node, bindings) == "sys.modules":
        return "sys.modules"
    mod = _reflect_container_module(node, bindings)
    if mod is not None:
        return mod
    if isinstance(node, ast.Name) and env and node.id not in visiting:
        vals = env.get(node.id, ())
        assigned = [v for v in vals if v is not None]
        if assigned and len(assigned) == len(vals):
            targets = {
                _reflect_source_target(v, bindings, env, visiting | {node.id})
                for v in assigned
            }
            if len(targets) == 1:
                return next(iter(targets))
    return None


def _resolve_reflected_with_env(
    node: ast.AST,
    bindings: Mapping[str, str],
    env: Mapping[str, list[ast.AST | None]] | None = None,
) -> str | None:
    canon = _resolve_reflected_canonical(node, bindings)
    if canon is not None or env is None:
        return canon
    if isinstance(node, ast.Attribute):
        base = _resolve_reflected_with_env(node.value, bindings, env)
        return f"{base}.{node.attr}" if base else None
    if isinstance(node, ast.Subscript):
        key = _literal_str_val(node.slice)
        src = _reflect_source_target(node.value, bindings, env)
        if key is None or src is None:
            return None
        if src == "sys.modules":
            return key if _is_checked_module(key) else None
        return f"{src}.{key}"
    return None


def _canonical_with_env(
    node: ast.AST,
    bindings: Mapping[str, str],
    env: Mapping[str, list[ast.AST | None]] | None = None,
) -> str | None:
    canon = _canonical(node, bindings)
    if canon is not None or env is None:
        return canon
    return _resolve_reflected_with_env(node, bindings, env)


def _check_reflection_node(
    path: str,
    node: ast.AST,
    bindings: Mapping[str, str],
    called_ids: set[int],
    resolved_sub_ids: set[int],
    env: Mapping[str, list[ast.AST | None]] | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    if isinstance(node, ast.Subscript):
        if id(node) in resolved_sub_ids:
            return []
    elif not (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)):
        return []
    ln = getattr(node, "lineno", 1)
    canon = _resolve_reflected_with_env(node, bindings, env)
    if canon in _AMBIENT_TARGETS:
        return [_error_finding(path, ln, "ambient-context", f"ambient reference to {canon}", "take the path/env mapping as a required parameter")]
    if _is_checked_callable(canon) and id(node) in called_ids:
        return []
    src = _reflect_source_target(node.value, bindings, env)
    if src is not None:
        return [_warn_finding(path, ln, "unverifiable-dynamic-access", f"dynamic access on {src}", "use direct access the gate can verify")]
    return []


def _class_global_nonlocal_names(node: ast.ClassDef) -> set[str]:
    names: set[str] = set()

    def _scan(cur: ast.AST) -> None:
        for child in ast.iter_child_nodes(cur):
            if isinstance(child, (ast.Global, ast.Nonlocal)):
                names.update(child.names)
            elif not isinstance(child, _SCOPE_NODES):
                _scan(child)

    _scan(node)
    return names


def _check_class_body_callables(path: str, node: ast.ClassDef, bindings: Mapping[str, str]) -> list[tuple[int, dict[str, Any]]]:
    out: list[tuple[int, dict[str, Any]]] = []
    excluded = _class_global_nonlocal_names(node)
    for stmt in node.body:
        if isinstance(stmt, ast.Assign):
            targets: list[ast.AST] = list(stmt.targets)
            stmt_val: ast.AST | None = stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            targets = [stmt.target]
            stmt_val = stmt.value
        else:
            continue
        pairs = [p for tgt in targets for p in _iter_target_value_pairs(tgt, stmt_val)]
        for nm, _ln, val in pairs:
            c = _canonical(val, bindings) if (val is not None and nm not in excluded) else None
            if _is_checked_callable(c):
                out.append(_warn_finding(path, stmt.lineno, "unverifiable-dynamic-access", f"checked callable {c} bound as class attribute", "call directly or move to runner"))
                break
    return out


def _check_collection_or_class_callables(
    path: str,
    node: ast.AST,
    bindings: Mapping[str, str],
    destructured_ids: set[int],
    annotation_ids: set[int] | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    out: list[tuple[int, dict[str, Any]]] = []
    if id(node) in destructured_ids or (annotation_ids is not None and id(node) in annotation_ids):
        return out
    if isinstance(node, ast.Set) or (isinstance(node, (ast.List, ast.Tuple)) and isinstance(node.ctx, ast.Load)):
        for elt in node.elts:
            c = _canonical(elt, bindings)
            if _is_checked_callable(c):
                out.append(_warn_finding(path, getattr(elt, "lineno", node.lineno), "unverifiable-dynamic-access", f"checked callable {c} in collection literal", "call directly or move to runner"))
    elif isinstance(node, ast.Dict):
        for item in (*(k for k in node.keys if k is not None), *node.values):
            c = _canonical(item, bindings)
            if _is_checked_callable(c):
                out.append(_warn_finding(path, getattr(item, "lineno", node.lineno), "unverifiable-dynamic-access", f"checked callable {c} in dict literal", "call directly or move to runner"))
    elif isinstance(node, ast.ClassDef):
        out.extend(_check_class_body_callables(path, node, bindings))
    return out


def _check_function_decorators_and_defaults(
    path: str,
    node: ast.AST,
    bindings: Mapping[str, str],
    path_vars: frozenset[str] | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    out: list[tuple[int, dict[str, Any]]] = []
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                out.extend(_check_callable_target(path, getattr(dec, "lineno", node.lineno), _canonical(dec, bindings)))
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        pos_args = (*node.args.posonlyargs, *node.args.args)
        pos_pairs = zip(pos_args[len(pos_args) - len(node.args.defaults):], node.args.defaults)
        kw_pairs = ((p, d) for p, d in zip(node.args.kwonlyargs, node.args.kw_defaults) if d is not None)
        for param, d in (*pos_pairs, *kw_pairs):
            s_val = _literal_str_val(d)
            if _is_pathlib_call(d, bindings, path_vars) or (s_val is not None and s_val.startswith(("/", "~"))):
                out.append(_warn_finding(
                    path, getattr(d, "lineno", node.lineno), "literal-default-path",
                    f"parameter {param.arg} default specifies a literal or Path(...) target path",
                    "take the path as a required parameter",
                ))
    return out


def _collect_annotation_ids(tree: ast.Module) -> set[int]:
    ann_ids: set[int] = set()
    for n in ast.walk(tree):
        roots: list[ast.AST] = []
        if isinstance(n, ast.arg) and n.annotation is not None:
            roots.append(n.annotation)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.returns is not None:
            roots.append(n.returns)
        elif isinstance(n, ast.AnnAssign):
            roots.append(n.annotation)
        elif hasattr(ast, "TypeAlias") and isinstance(n, ast.TypeAlias):
            roots.append(n.value)
        for root in roots:
            for sub in ast.walk(root):
                ann_ids.add(id(sub))
    return ann_ids


def _collect_ast_helper_ids(
    tree: ast.Module,
    bindings: Mapping[str, str],
    mod_assigns: Mapping[str, list[ast.AST | None]] | None = None,
) -> tuple[set[int], set[int], set[int]]:
    called_ids: set[int] = set()
    destructured_ids: set[int] = set()
    resolved_sub_ids: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            called_ids.add(id(n.func))
        elif isinstance(n, ast.Assign) and len(n.targets) == 1:
            destructured_ids.update(_collect_destructured_rhs_ids(n.targets[0], n.value))
        elif isinstance(n, ast.AnnAssign) and n.value is not None:
            destructured_ids.update(_collect_destructured_rhs_ids(n.target, n.value))
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Subscript):
            c = _resolve_reflected_with_env(n, bindings, mod_assigns)
            if c in _AMBIENT_TARGETS or (_is_checked_callable(c) and id(n) in called_ids):
                resolved_sub_ids.add(id(n.value))
    return called_ids, destructured_ids, resolved_sub_ids


def _mark_resolved_subscripts_in_scope(
    scope_node: ast.AST,
    bindings: Mapping[str, str],
    env: Mapping[str, list[ast.AST | None]],
    called_ids: set[int],
    resolved_sub_ids: set[int],
) -> None:
    for child in ast.iter_child_nodes(scope_node):
        if isinstance(child, _SCOPE_NODES):
            continue
        if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Subscript):
            c = _resolve_reflected_with_env(child, bindings, env)
            if c in _AMBIENT_TARGETS or (_is_checked_callable(c) and id(child) in called_ids):
                resolved_sub_ids.add(id(child.value))
        _mark_resolved_subscripts_in_scope(child, bindings, env, called_ids, resolved_sub_ids)


def _advance_component_scope(
    node: ast.AST,
    bindings: Mapping[str, str],
    root_vars: frozenset[str],
    mod_assigns: Mapping[str, list[ast.AST | None]],
    called_ids: set[int],
    resolved_sub_ids: set[int],
    state: tuple[Any, ...],
) -> tuple[Any, ...]:
    _, lex_vars, _, _, lex_env = state
    inner_vars = _scope_path_vars(node, bindings, lex_vars, root_vars, mod_assigns)
    inner_lex = lex_vars if isinstance(node, ast.ClassDef) else inner_vars
    assigns, globs = _collect_scope_env(node, mod_assigns)
    base = {k: v for k, v in lex_env.items() if k not in globs}
    cur_env = {**base, **{nm: mod_assigns[nm] for nm in globs if nm in mod_assigns}, **assigns}
    inner_lex_env = lex_env if isinstance(node, ast.ClassDef) else cur_env
    _mark_resolved_subscripts_in_scope(node, bindings, cur_env, called_ids, resolved_sub_ids)
    return (inner_vars, inner_lex, assigns, cur_env, inner_lex_env)


def _check_component_ast(
    path: str,
    tree: ast.Module,
    bindings: Mapping[str, str],
) -> list[tuple[int, dict[str, Any]]]:
    out = _check_component_imports(path, tree)
    mod_assigns = _collect_module_assigns(tree)
    called_ids, destructured_ids, resolved_sub_ids = _collect_ast_helper_ids(tree, bindings, mod_assigns)
    annotation_ids = _collect_annotation_ids(tree)
    cached_roots = getattr(bindings, "pathlib_instances", None)
    root_vars = (
        frozenset(cached_roots)
        if cached_roots is not None
        else _scope_path_vars(tree, bindings, frozenset(), mod_assigns=mod_assigns)
    )
    root_assigns, _ = _collect_scope_env(tree, mod_assigns)

    def _walk(node: ast.AST, state: tuple[Any, ...]) -> None:
        path_vars, _lex_vars, scope_assigns, env, _lex_env = state
        if isinstance(node, (ast.Name, ast.Attribute)) and isinstance(node.ctx, ast.Load):
            canon = _canonical(node, bindings)
            if canon in _AMBIENT_TARGETS:
                out.append(_error_finding(path, node.lineno, "ambient-context", f"ambient reference to {canon}", "take the path/env mapping as a required parameter"))
        elif isinstance(node, ast.Call):
            out.extend(_check_component_call(path, node, bindings, called_ids, path_vars, scope_assigns, annotation_ids))
        out.extend(_check_reflection_node(path, node, bindings, called_ids, resolved_sub_ids, env))
        out.extend(_check_collection_or_class_callables(path, node, bindings, destructured_ids, annotation_ids))
        out.extend(_check_function_decorators_and_defaults(path, node, bindings, path_vars))
        if isinstance(node, _SCOPE_NODES):
            inner_state = _advance_component_scope(node, bindings, root_vars, mod_assigns, called_ids, resolved_sub_ids, state)
            _walk_scope_children(node, state, inner_state, _walk)
        else:
            for child in ast.iter_child_nodes(node):
                _walk(child, state)

    _walk(tree, (root_vars, root_vars, root_assigns, root_assigns, root_assigns))
    return out


def _check_filename(path: str) -> list[tuple[int, dict[str, Any]]]:
    basename = PurePath(str(path).replace("\\", "/")).name
    if basename in ("__init__.py", "__main__.py") or _FILENAME_RE.match(basename):
        return []
    if "-" in basename:
        detail = f"module filename {basename!r} contains hyphens; must match ^[a-z_][a-z0-9_]*\\.py$"
    else:
        detail = f"module filename {basename!r} must match ^[a-z_][a-z0-9_]*\\.py$"
    return [_error_finding(path, 1, "filename", detail, "rename to snake_case without hyphens")]


def _extract_literal_str_seq(node: ast.AST | None) -> list[str] | None:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    items: list[str] = []
    for elt in node.elts:
        val = _literal_str_val(elt)
        if val is None:
            return None
        items.append(val)
    return items


def _classify_top_binding(
    name: str,
    func_names: set[str],
    non_func_names: set[str],
    alias_map: Mapping[str, str],
    rebound: set[str],
) -> str:
    visited: set[str] = set()
    cur = name
    while cur in alias_map and cur not in rebound and cur not in func_names and cur not in non_func_names:
        if cur in visited:
            return "unresolved"
        visited.add(cur)
        cur = alias_map[cur]
    if cur in rebound:
        return "unresolved"
    if cur in func_names and cur not in non_func_names:
        return "func"
    if cur in non_func_names and cur not in func_names:
        return "non_func"
    return "unresolved"


def _record_top_assign(
    stmt: ast.stmt,
    record_fn: Any,
    non_func_names: set[str],
    alias_map: dict[str, str],
    rebound: set[str],
) -> None:
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
        nm = stmt.targets[0].id
        if nm == "__all__":
            return
        record_fn(nm, stmt.lineno)
        if isinstance(stmt.value, ast.Name):
            if nm in alias_map and alias_map[nm] != stmt.value.id:
                rebound.add(nm)
            else:
                alias_map[nm] = stmt.value.id
        else:
            non_func_names.add(nm)
        return
    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
        nm = stmt.target.id
        if nm != "__all__":
            record_fn(nm, stmt.lineno)
            if isinstance(stmt.value, ast.Name):
                alias_map.setdefault(nm, stmt.value.id)
            else:
                non_func_names.add(nm)
        return
    targets = stmt.targets if isinstance(stmt, ast.Assign) else ([stmt.target] if isinstance(stmt, (ast.AnnAssign, ast.AugAssign)) else [])
    for tgt in targets:
        for nm, ln in _iter_target_names(tgt):
            if nm != "__all__":
                record_fn(nm, ln)
                non_func_names.add(nm)


def _resolve_export_list(
    path: str,
    all_nodes: list[tuple[int, ast.AST | None]],
    def_lines: Mapping[str, int],
    top_order: list[tuple[str, int]],
    func_names: set[str],
    non_func_names: set[str],
    alias_map: Mapping[str, str],
    rebound: set[str],
) -> tuple[list[tuple[str, int]], list[tuple[int, dict[str, Any]]]]:
    if not all_nodes:
        items = [
            (nm, ln)
            for nm, ln in top_order
            if _classify_top_binding(nm, func_names, non_func_names, alias_map, rebound) == "func"
        ]
        return items, []
    if len(all_nodes) > 1:
        ln = all_nodes[-1][0]
        return [], [_warn_finding(path, ln, "index-unverifiable", "__all__ is mutated or assigned multiple times", "define __all__ as a single literal list or tuple of strings")]
    all_line, all_val = all_nodes[0]
    names = _extract_literal_str_seq(all_val)
    if names is None:
        return [], [_warn_finding(path, all_line, "index-unverifiable", "__all__ is not a literal list/tuple of strings", "define __all__ as a literal list or tuple of strings")]
    items: list[tuple[str, int]] = []
    warnings: list[tuple[int, dict[str, Any]]] = []
    for name in names:
        kind = _classify_top_binding(name, func_names, non_func_names, alias_map, rebound)
        if kind == "func":
            items.append((name, def_lines.get(name, all_line)))
        elif kind == "unresolved":
            warnings.append(_warn_finding(
                path, def_lines.get(name, all_line), "index-unverifiable",
                f"__all__ entry {name!r} cannot be resolved to a top-level binding",
                "ensure every __all__ entry is defined at module top level",
            ))
    return items, warnings


def _discover_public_exports(
    path: str,
    tree: ast.Module,
) -> tuple[list[tuple[str, int]], list[tuple[int, dict[str, Any]]]]:
    all_nodes: list[tuple[int, ast.AST | None]] = []
    def_lines: dict[str, int] = {}
    top_order: list[tuple[str, int]] = []
    func_names: set[str] = set()
    non_func_names: set[str] = set()
    alias_map: dict[str, str] = {}
    rebound: set[str] = set()

    def _rec(nm: str, ln: int) -> None:
        if nm not in def_lines:
            def_lines[nm] = ln
            if not nm.startswith("_"):
                top_order.append((nm, ln))

    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _rec(stmt.name, stmt.lineno)
            func_names.add(stmt.name)
        elif isinstance(stmt, ast.ClassDef):
            _rec(stmt.name, stmt.lineno)
            non_func_names.add(stmt.name)
        elif hasattr(ast, "TypeAlias") and isinstance(stmt, ast.TypeAlias):
            _rec(stmt.name.id, stmt.lineno)
            non_func_names.add(stmt.name.id)
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in stmt.targets):
                all_nodes.append((stmt.lineno, stmt.value))
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id == "__all__":
                all_nodes.append((stmt.lineno, stmt.value))
            elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id == "__all__":
                all_nodes.append((stmt.lineno, None))
            _record_top_assign(stmt, _rec, non_func_names, alias_map, rebound)
    return _resolve_export_list(path, all_nodes, def_lines, top_order, func_names, non_func_names, alias_map, rebound)


def _check_component_index(
    path: str,
    mod_name: str,
    tree: ast.Module,
    index_text: str | None,
) -> list[tuple[int, dict[str, Any]]]:
    if index_text is None or mod_name == "__init__":
        return []
    public_items, warnings = _discover_public_exports(path, tree)
    out = list(warnings)
    for name, ln in public_items:
        needle = f"`{mod_name}.{name}("
        if needle not in index_text:
            out.append(_error_finding(
                path, ln, "index-missing",
                f"public symbol {mod_name}.{name} missing from COMPONENTS.md index (expected {needle!r})",
                f"add a line for {mod_name}.{name}(...) to the COMPONENTS.md index",
            ))
    return out


def _is_none_ast(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _is_str_list_ann(node: ast.AST | None, bindings: Mapping[str, str]) -> bool:
    if not isinstance(node, ast.Subscript):
        return False
    val_canon = _canonical(node.value, bindings)
    val_ok = val_canon in ("builtins.list", "typing.List") or (
        isinstance(node.value, ast.Name) and node.value.id in ("list", "List")
    ) or (
        _extract_attr_chain(node.value) == ["typing", "List"]
    )
    if not val_ok:
        return False
    slc = node.slice
    return _canonical(slc, bindings) == "builtins.str" or (isinstance(slc, ast.Name) and slc.id == "str")


def _is_argv_ann(node: ast.AST | None, bindings: Mapping[str, str]) -> bool:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return (
            (_is_str_list_ann(node.left, bindings) and _is_none_ast(node.right))
            or (_is_none_ast(node.left) and _is_str_list_ann(node.right, bindings))
        )
    if isinstance(node, ast.Subscript):
        val_canon = _canonical(node.value, bindings)
        opt_ok = val_canon == "typing.Optional" or (
            isinstance(node.value, ast.Name) and node.value.id == "Optional"
        ) or (
            _extract_attr_chain(node.value) == ["typing", "Optional"]
        )
        return opt_ok and _is_str_list_ann(node.slice, bindings)
    return False


def _is_int_ann(node: ast.AST | None, bindings: Mapping[str, str]) -> bool:
    if node is None:
        return False
    return _canonical(node, bindings) == "builtins.int" or (isinstance(node, ast.Name) and node.id == "int")


def _find_argv_param(args: ast.arguments) -> tuple[ast.arg, ast.AST | None] | None:
    pos_args = [*args.posonlyargs, *args.args]
    pos_defaults = [None] * (len(pos_args) - len(args.defaults)) + list(args.defaults)
    for param, default in zip(pos_args, pos_defaults):
        if param.arg == "argv":
            return param, default
    for param, default in zip(args.kwonlyargs, args.kw_defaults):
        if param.arg == "argv":
            return param, default
    return None


def _check_runner_main(
    path: str,
    tree: ast.Module,
    bindings: Mapping[str, str],
) -> list[tuple[int, dict[str, Any]]]:
    mains = [s for s in tree.body if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name == "main"]
    if not mains:
        return [_error_finding(path, 1, "runner-main", "missing top-level `def main(argv: list[str] | None = None) -> int`", _RUNNER_MAIN_FIX)]
    fn = mains[-1]
    ln, args = fn.lineno, fn.args
    out: list[tuple[int, dict[str, Any]]] = []
    if isinstance(fn, ast.AsyncFunctionDef):
        out.append(_error_finding(path, ln, "runner-main", "main must be `def main`, not `async def main`", _RUNNER_MAIN_FIX))
    if len(args.args) != 1 or args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg:
        out.append(_error_finding(path, ln, "runner-main", "main must take exactly one parameter argv", _RUNNER_MAIN_FIX))
    argv_info = _find_argv_param(args)
    if argv_info is None:
        out.append(_error_finding(path, ln, "runner-main", "missing parameter argv", _RUNNER_MAIN_FIX))
    else:
        param, default = argv_info
        if not _is_none_ast(default):
            out.append(_error_finding(path, ln, "runner-main", "main parameter `argv` must have default `None`", _RUNNER_MAIN_FIX))
        if param.annotation is None:
            out.append(_error_finding(path, ln, "runner-main", "main parameter `argv` missing type annotation `list[str] | None`", _RUNNER_MAIN_FIX))
        elif not _is_argv_ann(param.annotation, bindings):
            out.append(_error_finding(path, ln, "runner-main", "main parameter `argv` annotation must be `list[str] | None`", _RUNNER_MAIN_FIX))
    if fn.returns is None:
        out.append(_error_finding(path, ln, "runner-main", "main missing return annotation `-> int`", _RUNNER_MAIN_FIX))
    elif not _is_int_ann(fn.returns, bindings):
        out.append(_error_finding(path, ln, "runner-main", "main return annotation must be `int`", _RUNNER_MAIN_FIX))
    return out


def _is_dunder_main_test(test: ast.AST) -> bool:
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or len(test.comparators) != 1:
        return False
    if not isinstance(test.ops[0], ast.Eq):
        return False
    left, right = test.left, test.comparators[0]
    return (
        (isinstance(left, ast.Name) and left.id == "__name__" and _is_literal_str(right, "__main__"))
        or (_is_literal_str(left, "__main__") and isinstance(right, ast.Name) and right.id == "__name__")
    )


def _is_raise_system_exit_main(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.Raise) or stmt.exc is None or stmt.cause is not None:
        return False
    call = stmt.exc
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != "SystemExit":
        return False
    if len(call.args) != 1 or call.keywords:
        return False
    inner = call.args[0]
    return (
        isinstance(inner, ast.Call)
        and isinstance(inner.func, ast.Name)
        and inner.func.id == "main"
        and not inner.args
        and not inner.keywords
    )


def _check_runner_entrypoint(path: str, tree: ast.Module) -> list[tuple[int, dict[str, Any]]]:
    blocks = [s for s in tree.body if isinstance(s, ast.If) and _is_dunder_main_test(s.test)]
    if not blocks:
        return [_error_finding(path, 1, "runner-entrypoint", 'missing top-level `if __name__ == "__main__":` entrypoint block', _RUNNER_ENTRYPOINT_FIX)]
    if len(blocks) > 1:
        return [_error_finding(path, blocks[1].lineno, "runner-entrypoint", 'multiple `if __name__ == "__main__":` blocks found', _RUNNER_ENTRYPOINT_FIX)]
    blk = blocks[0]
    if blk.orelse:
        return [_error_finding(path, blk.lineno, "runner-entrypoint", '`if __name__ == "__main__":` block must not have an else/elif branch', _RUNNER_ENTRYPOINT_FIX)]
    if len(blk.body) != 1 or not _is_raise_system_exit_main(blk.body[0]):
        return [_error_finding(path, blk.lineno, "runner-entrypoint", '`if __name__ == "__main__":` body must be exactly `raise SystemExit(main())`', _RUNNER_ENTRYPOINT_FIX)]
    return []


def _excerpt_line(line: str) -> str:
    return line[:57] + "..." if len(line) > 60 else line


def _excerpt_repr(line: str) -> str:
    rep = repr(line)
    return rep[:57] + "..." if len(rep) > 60 else rep


# Exact first lines a vendored copy may carry; only the module name varies (it
# goes between prefix and suffix). Order: canonical, legacy, neutral, long form,
# then the two forms the plugin's refresh script writes (hermes_audit/ and
# componentize/ origins).
# No prefix match, case folding or whitespace trimming beyond the CR/LF terminator.
_AUTHOR_HEADER_SUFFIX = ".py (MIT licence, same as this plugin); do not edit"
_VENDORED_HEADER_FORMS = (
    ("# vendored from components/", ".py, do not edit"),
    # legacy header form of older vendored copies
    ("# vendored from " + "To" + "ols/hermes_audit/", ".py, do not edit"),
    ("# vendored from hermes_audit/", ".py, do not edit"),
    (
        "# vendored module (origin: hermes_audit/",
        ".py, from the author's audit toolkit; not published separately)."
        " Do not edit: change the origin and re-vendor.",
    ),
    ("# vendored copy of the author's hermes_audit/", _AUTHOR_HEADER_SUFFIX),
    ("# vendored copy of the author's componentize/", _AUTHOR_HEADER_SUFFIX),
)


def _check_vendored(
    path: str,
    mod_name: str,
    source: str,
    vendor_source: str,
) -> list[dict[str, Any]]:
    lined = _check_filename(path)
    src_lines = source.splitlines(keepends=True)
    accepted = tuple(prefix + mod_name + suffix for prefix, suffix in _VENDORED_HEADER_FORMS)
    canonical, legacy, neutral, long_form, author_audit, author_cz = accepted
    first_line = src_lines[0].rstrip("\r\n") if src_lines else ""
    if first_line not in accepted:
        lined.append(_error_finding(
            path, 1, "vendored-header",
            f"first line must be one of {canonical!r}, {neutral!r}, {author_audit!r}, {author_cz!r} "
            f"(or for existing copies {legacy!r}, {long_form!r}), got {_excerpt_line(first_line)!r}",
            f"set line 1 to `{canonical}`",
        ))
    body = "".join(src_lines[1:]) if src_lines else ""
    if body != vendor_source:
        b_lines = body.splitlines(keepends=True)
        v_lines = vendor_source.splitlines(keepends=True)
        diff_idx = 0
        max_len = max(len(b_lines), len(v_lines), 1)
        for idx in range(max_len):
            b_ln = b_lines[idx] if idx < len(b_lines) else "<EOF>"
            v_ln = v_lines[idx] if idx < len(v_lines) else "<EOF>"
            if b_ln != v_ln:
                diff_idx = idx
                break
        vendored_lineno = diff_idx + 2
        got_ex = _excerpt_repr(b_lines[diff_idx]) if diff_idx < len(b_lines) else "'<EOF>'"
        exp_ex = _excerpt_repr(v_lines[diff_idx]) if diff_idx < len(v_lines) else "'<EOF>'"
        lined.append(_error_finding(
            path, vendored_lineno, "vendored-drift",
            f"vendored copy drifts from source at line {vendored_lineno}: got {got_ex} vs source {exp_ex}",
            f"re-copy from the source module {mod_name}.py without local edits (§2)",
        ))
    return _dedupe_and_sort_findings(lined)


def _dedupe_and_sort_findings(lined_findings: list[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, int, str]] = set()
    unique: list[tuple[int, str, str, dict[str, Any]]] = []
    for lineno, finding in lined_findings:
        fid = str(finding["id"])
        detail = str(finding["detail"])
        key = (fid, lineno, detail)
        if key in seen:
            continue
        seen.add(key)
        unique.append((lineno, fid, detail, finding))
    unique.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in unique]


def _module_stem(path: str) -> str:
    return PurePath(str(path).replace("\\", "/")).stem


def _error_envelope(role: str, mod_name: str, error_msg: str) -> dict[str, Any]:
    cov = dict(_COVERAGE_BY_ROLE[role]) if role in _COVERAGE_BY_ROLE else {}
    return {
        "ok": False,
        "status": "error",
        "error": error_msg,
        "findings": [],
        "role": role,
        "module": mod_name,
        "coverage": cov,
    }


def _collect_module_findings(
    path: str,
    mod_name: str,
    role: str,
    tree: ast.Module,
    index_text: str | None,
) -> list[dict[str, Any]]:
    occurrences, checked_names, node_scopes = _discover_bindings(tree)
    bindings = _bindings_from_occurrences(occurrences, node_scopes)
    lined = _check_filename(path)
    lined.extend(_validate_checked_occurrences(path, occurrences, checked_names))
    lined.extend(_validate_star_imports(path, tree))
    if role == "component":
        bindings.pathlib_instances = _discover_pathlib_instances(tree, bindings)
        lined.extend(_check_component_ast(path, tree, bindings))
        lined.extend(_check_tree_exception_suppression(path, tree, bindings))
        lined.extend(_check_component_index(path, mod_name, tree, index_text))
        has_all = any(
            (isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in stmt.targets))
            or (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
                and stmt.target.id == "__all__" and stmt.value is not None)
            for stmt in tree.body
        )
        if not has_all:
            lined.append(_warn_finding(
                path, 1, "component-public-interface",
                "component does not declare its public interface with __all__",
                "define __all__ as a literal list or tuple of public export names",
            ))
    elif role in ("runner", "one_off"):
        lined.extend(_check_runner_main(path, tree, bindings))
        lined.extend(_check_runner_entrypoint(path, tree))
    return _dedupe_and_sort_findings(lined)


def audit_module(
    path: str,
    *,
    source: str,
    role: str,
    index_text: str | None = None,
    vendor_source: str | None = None,
) -> dict[str, Any]:
    """Audit a Python module source against the Architecture Standard."""
    path_str = str(path)
    mod_name = _module_stem(path_str)
    if role not in _VALID_ROLES:
        return _error_envelope(role, mod_name, f"invalid role {role!r}; expected one of {sorted(_VALID_ROLES)}")
    if role == "vendored":
        if vendor_source is None:
            return _error_envelope(role, mod_name, "vendored role requires vendor_source")
        findings = _check_vendored(path_str, mod_name, source, vendor_source)
    else:
        try:
            tree = ast.parse(source, filename=path_str)
            symtable.symtable(source, path_str, "exec")
        except SyntaxError as exc:
            lineno = exc.lineno if exc.lineno is not None else "?"
            return _error_envelope(role, mod_name, f"syntax: {exc.msg or str(exc)} line {lineno}")
        findings = _collect_module_findings(path_str, mod_name, role, tree, index_text)

    status = "fail" if any(f.get("severity") == "error" for f in findings) else "pass"
    return {
        "ok": status == "pass",
        "status": status,
        "error": None,
        "findings": findings,
        "role": role,
        "module": mod_name,
        "coverage": dict(_COVERAGE_BY_ROLE[role]),
    }
