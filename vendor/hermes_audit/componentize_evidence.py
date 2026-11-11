# vendored copy of the author's hermes_audit/componentize_evidence.py (MIT licence, same as this plugin); do not edit
"""Componentize spec.json manifest validator and evidence/receipt evaluators (schema v1, 100% Python stdlib).

No CLI, printing, file I/O, or ambient environment reads inside this module;
public entrypoints: `validate_manifest`, `canonical_sha256`, `compare_capture`,
`evidence_digest`, and `verify_receipt`.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import math
import posixpath
import re
from typing import Any

__all__ = [
    "render_component_index_line",
    "validate_manifest",
    "canonical_sha256",
    "compare_capture",
    "evidence_digest",
    "verify_receipt",
]

_REQUIRED_TOP_KEYS = frozenset({
    "schema_version",
    "name",
    "kind",
    "attest",
    "files",
    "original",
    "golden",
    "tests",
})
_TOP_KEYS = _REQUIRED_TOP_KEYS | frozenset({"inputs", "hermes_plugins"})
_ATTEST_KEYS = frozenset({"kind_reason", "reuse"})
_FILE_KEYS = frozenset({"path", "role", "vendor_source", "purpose"})
_GOLDEN_RUN_KEYS = frozenset({"argv", "cwd", "env", "compare", "isolated"})
_TESTS_RUN_KEYS = frozenset({"argv", "cwd"})
_WAIVED_KEYS = frozenset({"waived"})
_VALID_KINDS = frozenset({"component", "one_off"})
_VALID_ROLES = frozenset({"component", "runner", "runner_module", "vendored", "one_off"})
_VALID_COMPARE = frozenset({"stdout", "stderr", "rc"})
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MIN_TEXT_LEN = 20
_REQUIRED_FILE_KEYS = frozenset({"path", "role"})


def _err_finding(fid: str, detail: str, fix: str) -> dict[str, Any]:
    return {"id": fid, "severity": "error", "detail": detail, "fix": fix}


def _finding(fid: str, json_path: str, msg: str, fix: str) -> dict[str, Any]:
    return _err_finding(fid, f"{json_path}: {msg}", fix)


def _is_strict_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def _check_dict_keys(
    obj: dict[str, Any],
    prefix: str,
    required: frozenset[str],
    allowed: frozenset[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in sorted(required):
        if key not in obj:
            loc = f"{prefix}.{key}" if prefix else key
            out.append(_finding("manifest-missing-key", loc, "missing required key", f"add {loc!r}"))
    for key in sorted(str(k) for k in obj.keys() if k not in allowed):
        loc = f"{prefix}.{key}" if prefix else key
        out.append(_finding("manifest-unknown-key", loc, "unknown key", f"remove {loc!r}"))
    return out


def _check_posix_abs_path(val: Any, json_path: str, *, require_py: bool) -> list[dict[str, Any]]:
    if not isinstance(val, str):
        return [_finding("manifest-invalid-type", json_path, "must be a string path", f"set {json_path} to an absolute POSIX path string")]
    problems: list[str] = []
    if not val.startswith("/"):
        problems.append("must be an absolute POSIX path starting with '/'")
    if "\x00" in val:
        problems.append("must not contain NUL bytes")
    if require_py and not val.endswith(".py"):
        problems.append("must end with '.py'")
    if problems:
        return [_finding(
            "manifest-invalid-path",
            json_path,
            "; ".join(problems),
            f"use a normalized absolute POSIX path{' ending in .py' if require_py else ''}",
        )]
    norm = posixpath.normpath(val)
    if norm.startswith("//"):
        norm = "/" + norm.lstrip("/")
    if norm != val:
        return [_finding(
            "manifest-path-not-normalized",
            json_path,
            f"path is not normalized (expected {norm!r})",
            f"use the normalized form: {norm}",
        )]
    return []


def _check_attest(val: Any) -> list[dict[str, Any]]:
    if not isinstance(val, dict):
        return [_finding("manifest-invalid-type", "attest", "must be a JSON object", "set attest to {kind_reason, reuse}")]
    out = _check_dict_keys(val, "attest", _ATTEST_KEYS, _ATTEST_KEYS)
    for key in sorted(_ATTEST_KEYS):
        if key not in val:
            continue
        loc = f"attest.{key}"
        item = val[key]
        if not isinstance(item, str):
            out.append(_finding("manifest-invalid-type", loc, "must be a string", f"set {loc} to a string >= 20 chars"))
        elif len(item.strip()) < _MIN_TEXT_LEN:
            out.append(_finding(
                "manifest-attest-empty",
                loc,
                f"must have at least {_MIN_TEXT_LEN} characters after trimming",
                f"write a substantive explanation (>= {_MIN_TEXT_LEN} chars) in {loc}",
            ))
    return out


def _check_single_file(
    entry: Any,
    idx: int,
    seen_paths: set[str],
) -> tuple[list[dict[str, Any]], str | None, str | None, str | None]:
    prefix = f"files[{idx}]"
    if not isinstance(entry, dict):
        return [_finding("manifest-invalid-type", prefix, "must be a JSON object", f"set {prefix} to {{path, role}}")], None, None, None
    out = _check_dict_keys(entry, prefix, _REQUIRED_FILE_KEYS, _FILE_KEYS)
    raw_path = entry.get("path")
    valid_path: str | None = None
    if "path" in entry:
        path_errs = _check_posix_abs_path(raw_path, f"{prefix}.path", require_py=True)
        out.extend(path_errs)
        if isinstance(raw_path, str):
            if raw_path in seen_paths:
                out.append(_finding("manifest-duplicate-path", f"{prefix}.path", f"duplicate file path {raw_path!r}", "list each file path at most once"))
            seen_paths.add(raw_path)
            if not path_errs:
                valid_path = raw_path
    raw_role = entry.get("role")
    valid_role: str | None = None
    if "role" in entry:
        if not isinstance(raw_role, str) or raw_role not in _VALID_ROLES:
            out.append(_finding("manifest-invalid-role", f"{prefix}.role", f"invalid role {raw_role!r}", f"use one of {sorted(_VALID_ROLES)}"))
        else:
            valid_role = raw_role
    vs_errs, valid_vs = _check_vendor_source(entry, prefix, valid_role)
    out.extend(vs_errs)
    return out, valid_path, valid_role, valid_vs


def _check_vendor_source(
    entry: dict[str, Any],
    prefix: str,
    valid_role: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    loc = f"{prefix}.vendor_source"
    has_vs = "vendor_source" in entry
    if valid_role == "vendored":
        if not has_vs:
            return [_finding("manifest-vendor-source", loc, "vendor_source is required when role is 'vendored'", f"set {loc} to the source component .py path")], None
        vs_val = entry["vendor_source"]
        errs = _check_posix_abs_path(vs_val, loc, require_py=True)
        return errs, vs_val if (not errs and isinstance(vs_val, str)) else None
    if has_vs:
        return [_finding("manifest-vendor-source", loc, "vendor_source is forbidden unless role is 'vendored'", f"remove {loc}")], None
    return [], None


def _check_files(val: Any) -> tuple[list[dict[str, Any]], list[tuple[int, str | None, str | None]], set[str]]:
    if not isinstance(val, list):
        return [_finding("manifest-invalid-type", "files", "must be a list of file objects", "set files to a non-empty list")], [], set()
    if not val:
        return [_finding("manifest-files-empty", "files", "files list must not be empty", "include at least one file entry")], [], set()
    out: list[dict[str, Any]] = []
    parsed: list[tuple[int, str | None, str | None]] = []
    seen_paths: set[str] = set()
    vendor_sources: set[str] = set()
    for idx, entry in enumerate(val):
        f_errs, f_path, f_role, f_vs = _check_single_file(entry, idx, seen_paths)
        out.extend(f_errs)
        parsed.append((idx, f_path, f_role))
        if f_vs is not None:
            vendor_sources.add(f_vs)
    return out, parsed, seen_paths | vendor_sources


def _check_inputs(
    manifest: dict[str, Any],
    kind: str | None,
    forbidden_paths: set[str],
) -> list[dict[str, Any]]:
    has_inputs = "inputs" in manifest
    if not has_inputs:
        if kind == "component":
            return [_finding("manifest-component-needs-inputs", "inputs", "kind 'component' requires a non-empty 'inputs' list", "list test files and fixtures the tests/golden commands read")]
        return []
    val = manifest["inputs"]
    if not isinstance(val, list):
        return [_finding("manifest-invalid-type", "inputs", "must be a list of path strings", "set inputs to a list of absolute POSIX path strings")]
    if not val and kind == "component":
        return [_finding("manifest-component-needs-inputs", "inputs", "kind 'component' requires a non-empty 'inputs' list", "list test files and fixtures the tests/golden commands read")]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, item in enumerate(val):
        loc = f"inputs[{idx}]"
        out.extend(_check_posix_abs_path(item, loc, require_py=False))
        if isinstance(item, str):
            if item in seen or item in forbidden_paths:
                out.append(_finding("manifest-duplicate-path", loc, f"duplicate or colliding input path {item!r}", "list each input path at most once and keep inputs distinct from files/vendor_source/original"))
            seen.add(item)
    return out


def _check_argv(val: Any, json_path: str) -> list[dict[str, Any]]:
    if not isinstance(val, list) or not val:
        return [_finding("manifest-invalid-argv", json_path, "must be a non-empty list of strings", f"set {json_path} to a non-empty command list")]
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(val):
        if not isinstance(item, str):
            out.append(_finding("manifest-invalid-argv", f"{json_path}[{idx}]", "argv element must be a string", f"replace {json_path}[{idx}] with a string"))
        elif "\x00" in item:
            out.append(_finding("manifest-invalid-argv", f"{json_path}[{idx}]", "argv element must not contain NUL bytes", f"remove NUL bytes from {json_path}[{idx}]"))
    return out


def _check_waived_text(val: Any, json_path: str) -> list[dict[str, Any]]:
    if not isinstance(val, str):
        return [_finding("manifest-invalid-type", json_path, "must be a string", f"set {json_path} to a reason string >= {_MIN_TEXT_LEN} chars")]
    if len(val.strip()) < _MIN_TEXT_LEN:
        return [_finding(
            "manifest-waived-empty",
            json_path,
            f"waiver reason must have at least {_MIN_TEXT_LEN} characters after trimming",
            f"provide a justification >= {_MIN_TEXT_LEN} chars in {json_path}",
        )]
    return []


def _check_golden_compare_and_env(obj: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if "env" in obj:
        env_val = obj["env"]
        if not isinstance(env_val, dict):
            out.append(_finding("manifest-invalid-type", "golden.env", "must be a dict[str, str]", "set golden.env to a JSON object of string values"))
        else:
            for k in sorted(env_val.keys(), key=str):
                if not isinstance(k, str) or not isinstance(env_val[k], str):
                    out.append(_finding("manifest-invalid-env", f"golden.env.{k}", "env key and value must be strings", f"convert golden.env.{k} to string"))
    if "compare" in obj:
        cmp_val = obj["compare"]
        if not isinstance(cmp_val, list) or not cmp_val:
            out.append(_finding("manifest-invalid-compare", "golden.compare", "must be a non-empty subset of ['stdout', 'stderr', 'rc']", "set golden.compare to e.g. ['stdout', 'stderr', 'rc']"))
        else:
            seen: set[str] = set()
            for idx, item in enumerate(cmp_val):
                loc = f"golden.compare[{idx}]"
                if not isinstance(item, str) or item not in _VALID_COMPARE:
                    out.append(_finding("manifest-invalid-compare", loc, f"invalid compare stream {item!r}", "use only 'stdout', 'stderr', or 'rc'"))
                elif item in seen:
                    out.append(_finding("manifest-invalid-compare", loc, f"duplicate compare stream {item!r}", "remove duplicate entries in golden.compare"))
                else:
                    seen.add(item)
    return out


def _check_waived_or_run_section(
    val: Any,
    section: str,
    run_keys: frozenset[str],
    run_label: str,
    mixed_id: str,
) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(val, dict):
        return [_finding("manifest-invalid-type", section, "must be a JSON object", f"set {section} to {run_label} or {{waived}}")], False
    if not val:
        return [_finding("manifest-missing-key", section, f"{section} object must not be empty", f"specify {run_label} or {{waived}}")], False
    has_waived = "waived" in val
    has_run = bool(set(val.keys()) & run_keys)
    out: list[dict[str, Any]] = []
    if has_waived and has_run:
        out.append(_finding(mixed_id, section, "cannot mix 'waived' with active run keys", f"use either {{waived}} or {run_label}"))
        out.extend(_check_dict_keys(val, section, frozenset(), run_keys | _WAIVED_KEYS))
    elif has_waived:
        out.extend(_check_dict_keys(val, section, _WAIVED_KEYS, _WAIVED_KEYS))
    elif not has_run:
        out.extend(_check_dict_keys(val, section, frozenset(), frozenset()))
        out.append(_finding("manifest-missing-key", section, f"{section} must specify {run_label} or {{waived}}", f"specify {run_label} or {{waived}}"))
        return out, False
    else:
        out.extend(_check_dict_keys(val, section, run_keys, run_keys))
    if has_waived:
        out.extend(_check_waived_text(val["waived"], f"{section}.waived"))
    if "argv" in val:
        out.extend(_check_argv(val["argv"], f"{section}.argv"))
    if "cwd" in val:
        out.extend(_check_posix_abs_path(val["cwd"], f"{section}.cwd", require_py=False))
    return out, has_waived


def _check_golden(val: Any) -> list[dict[str, Any]]:
    out, _ = _check_waived_or_run_section(
        val, "golden", _GOLDEN_RUN_KEYS, "{argv,cwd,env,compare,isolated}", "manifest-golden-mixed"
    )
    if isinstance(val, dict):
        out.extend(_check_golden_compare_and_env(val))
        if "isolated" in val and val["isolated"] is not True:
            out.append(_finding("manifest-golden-not-isolated", "golden.isolated", "isolated must be literal true", "set golden.isolated to true"))
    return out


def _check_tests(val: Any) -> tuple[list[dict[str, Any]], bool]:
    return _check_waived_or_run_section(
        val, "tests", _TESTS_RUN_KEYS, "{argv, cwd}", "manifest-tests-mixed"
    )


def _check_cross_rules(
    kind: str | None,
    parsed_files: list[tuple[int, str | None, str | None]],
    original_path: str | None,
    tests_waived: bool,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if kind == "component":
        roles = [r for _, _, r in parsed_files if r is not None]
        if parsed_files and ("component" not in roles or "runner" not in roles):
            out.append(_finding(
                "manifest-component-roles",
                "files",
                "kind 'component' requires at least one 'component' file and at least one 'runner' file",
                "include both a component module and a thin runner in files",
            ))
        for idx, _, r in parsed_files:
            if r == "one_off":
                out.append(_finding("manifest-component-roles", f"files[{idx}].role", "kind 'component' forbids 'one_off' file role", "change role to 'component', 'runner', or 'runner_module'"))
        if tests_waived:
            out.append(_finding("manifest-component-needs-tests", "tests.waived", "kind 'component' cannot waive tests", "provide {argv, cwd} under tests"))
    elif kind == "one_off" and parsed_files:
        if len(parsed_files) != 1:
            out.append(_finding("manifest-one-off-files", "files", "kind 'one_off' requires exactly one file entry", "keep a single one_off file in files"))
        for idx, _, r in parsed_files:
            if r is not None and r != "one_off":
                out.append(_finding("manifest-one-off-files", f"files[{idx}].role", f"kind 'one_off' requires file role 'one_off', got {r!r}", "set role to 'one_off'"))
    if kind != "one_off" and original_path is not None:
        if any(p == original_path for _, p, _ in parsed_files if p is not None):
            out.append(_finding("manifest-original-collision", "original", "original path must not equal any files[].path unless kind is 'one_off'", "keep original distinct from component/runner output paths"))
    return out


def _check_top_scalars(
    manifest: dict[str, Any],
) -> tuple[list[dict[str, Any]], int | None, str | None, str | None, str | None]:
    out: list[dict[str, Any]] = []
    schema_version: int | None = None
    if "schema_version" in manifest:
        sv = manifest["schema_version"]
        schema_version = sv if _is_strict_int(sv) else None
        if schema_version not in (1, 2):
            out.append(_finding("manifest-schema-version", "schema_version", f"expected integer 1 or 2, got {sv!r}", "set schema_version to 1 or 2"))
    name: str | None = None
    if "name" in manifest:
        nm = manifest["name"]
        name = nm if isinstance(nm, str) else None
        if name is None or not _NAME_RE.fullmatch(name):
            out.append(_finding("manifest-invalid-name", "name", f"must match {_NAME_RE.pattern}", "use 1..64 lowercase alphanumeric, '_' or '-' chars starting with alphanumeric"))
    kind: str | None = None
    if "kind" in manifest:
        kd = manifest["kind"]
        if isinstance(kd, str) and kd in _VALID_KINDS:
            kind = kd
        else:
            out.append(_finding("manifest-invalid-kind", "kind", f"expected 'component' or 'one_off', got {kd!r}", "set kind to 'component' or 'one_off'"))
    orig_path: str | None = None
    if "original" in manifest:
        orig_errs = _check_posix_abs_path(manifest["original"], "original", require_py=False)
        out.extend(orig_errs)
        if not orig_errs and isinstance(manifest["original"], str):
            orig_path = manifest["original"]
    return out, schema_version, kind, name, orig_path


def _dedupe_and_sort(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for f in findings:
        key = (str(f["detail"]), str(f["id"]), str(f["severity"]), str(f["fix"]))
        if key not in seen:
            seen.add(key)
            unique.append(f)
    unique.sort(key=lambda item: (str(item["detail"]), str(item["id"])))
    return unique


def render_component_index_line(entry: dict[str, Any], *, public_api: str, returns: str) -> str:
    """Render one COMPONENTS.md line from a validated file entry; never write it."""
    purpose = entry.get("purpose") if entry.get("role") == "component" else None
    description = f" — Purpose: {' '.join(purpose.split())}" if isinstance(purpose, str) and purpose.strip() else ""
    return f"- `{public_api}`{description} — returns `{returns}`."


def _check_hermes_plugins(value: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return [_finding("manifest-hermes-plugins", "hermes_plugins", "must be a list", "use a list of plugin records")]
    seen: set[str] = set()
    for i, entry in enumerate(value):
        loc = f"hermes_plugins[{i}]"
        if not isinstance(entry, dict):
            out.append(_finding("manifest-hermes-plugins", loc, "must be an object", "provide path and python"))
            continue
        out.extend(_check_dict_keys(entry, loc, frozenset({'path','python'}), frozenset({'path','python','env','budget_tokens','timeout'})))
        for key in ('path','python'):
            if key in entry:
                out.extend(_check_posix_abs_path(entry[key], f'{loc}.{key}', require_py=False))
        path = entry.get('path')
        if isinstance(path, str):
            if path in seen:
                out.append(_finding('manifest-hermes-plugins',loc,'duplicate plugin path','list each path once'))
            seen.add(path)
        env = entry.get('env', {})
        if not isinstance(env, dict) or any(not isinstance(k,str) or not k or '=' in k or '\x00' in k or not isinstance(v,str) or '\x00' in v for k,v in env.items()):
            out.append(_finding('manifest-hermes-plugins',loc,'env must be a string map','use non-secret strings'))
        budget = entry.get('budget_tokens', 2000)
        if not _is_strict_int(budget) or budget <= 0:
            out.append(_finding('manifest-hermes-plugins',loc,'budget_tokens must be a positive integer, not bool','use a positive integer'))
        timeout = entry.get('timeout', 60)
        if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or timeout <= 0 or not math.isfinite(timeout):
            out.append(_finding('manifest-hermes-plugins',loc,'timeout must be positive and finite','use positive seconds'))
    return out


def validate_manifest(manifest: Any) -> dict[str, Any]:
    """Validate a componentize spec.json manifest (schema v1 or v2)."""
    if not isinstance(manifest, dict):
        return {
            "ok": False, "status": "error", "error": f"manifest must be a dict, got {type(manifest).__name__}",
            "findings": [], "schema_version": None, "kind": None, "name": None,
        }
    findings = _check_dict_keys(manifest, "", _REQUIRED_TOP_KEYS, _TOP_KEYS)
    scalar_errs, schema_version, kind, name, orig_path = _check_top_scalars(manifest)
    findings.extend(scalar_errs)
    if "hermes_plugins" in manifest:
        findings.extend(_check_hermes_plugins(manifest["hermes_plugins"]))
    if "attest" in manifest:
        findings.extend(_check_attest(manifest["attest"]))
    parsed_files: list[tuple[int, str | None, str | None]] = []
    file_and_vs_paths: set[str] = set()
    if "files" in manifest:
        file_errs, parsed_files, file_and_vs_paths = _check_files(manifest["files"])
        findings.extend(file_errs)
        if schema_version == 2:
            for idx, _, role in parsed_files:
                if role == "component":
                    purpose = manifest["files"][idx].get("purpose")
                    if not isinstance(purpose, str) or len(purpose.strip()) < _MIN_TEXT_LEN:
                        findings.append(_finding(
                            "manifest-component-purpose", f"files[{idx}].purpose",
                            "schema v2 components require a purpose of at least 20 characters after trimming",
                            "describe the component's responsibility in purpose (>= 20 chars)",
                        ))
    forbidden = file_and_vs_paths | ({manifest["original"]} if isinstance(manifest.get("original"), str) else set())
    findings.extend(_check_inputs(manifest, kind, forbidden))
    if "golden" in manifest:
        findings.extend(_check_golden(manifest["golden"]))
    tests_waived = False
    if "tests" in manifest:
        test_errs, tests_waived = _check_tests(manifest["tests"])
        findings.extend(test_errs)
    findings.extend(_check_cross_rules(kind, parsed_files, orig_path, tests_waived))
    sorted_findings = _dedupe_and_sort(findings)
    status = "fail" if sorted_findings else "pass"
    return {
        "ok": status == "pass", "status": status, "error": None, "findings": sorted_findings,
        "schema_version": schema_version, "kind": kind, "name": name,
    }


_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
CURRENT_KEYS: tuple[str, ...] = (
    "manifest_sha",
    "manifest_gen",
    "baseline_gen",
    "baseline_shas",
    "file_hashes",
    "checker_digest",
    "runtime",
    "attestations_sha",
    "amend_log_sha",
)
# The 13 fields covered by evidence_digest, then the two receipt-only fields.
_DIGEST_KEYS: tuple[str, ...] = ("schema_version", "name", *CURRENT_KEYS, "check_result_sha", "check_status")
RECEIPT_KEYS: tuple[str, ...] = (*_DIGEST_KEYS, "created_at", "evidence_digest")
_RECEIPT_KEYS = frozenset(RECEIPT_KEYS)
_CURRENT_KEYS = frozenset(CURRENT_KEYS)
_RECEIPT_HEX_KEYS = frozenset({
    "manifest_sha",
    "checker_digest",
    "attestations_sha",
    "check_result_sha",
    "evidence_digest",
})
_CURRENT_HEX_KEYS = frozenset({"manifest_sha", "checker_digest", "attestations_sha"})
_OPT_HEX_KEYS = frozenset({"amend_log_sha"})
_GEN_KEYS = frozenset({"manifest_gen", "baseline_gen"})
_HEX_MAP_KEYS = frozenset({"baseline_shas", "file_hashes"})
_RECEIPT_STR_KEYS = frozenset({"name", "runtime", "check_status", "created_at"})
_CURRENT_STR_KEYS = frozenset({"runtime"})
_STALE_SCALAR_KEYS = tuple(k for k in CURRENT_KEYS if k != "file_hashes")


def canonical_sha256(value: Any) -> str:
    """Return SHA-256 hex digest of canonical JSON encoding of value (pure helper, not an envelope)."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _compare_error(msg: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "error",
        "error": msg,
        "findings": [],
        "matched": False,
        "streams": {},
    }


def _validate_compare_inputs(baseline: Any, actual: Any, compare: Any) -> str | None:
    if not isinstance(compare, list) or not compare:
        return "compare must be a non-empty list of stream names"
    seen: set[str] = set()
    for item in compare:
        if not isinstance(item, str) or item not in _VALID_COMPARE:
            return f"invalid compare stream {item!r}"
        if item in seen:
            return f"duplicate compare stream {item!r}"
        seen.add(item)
    if not isinstance(baseline, dict) or not isinstance(actual, dict):
        return "baseline and actual must be dicts"
    for label, obj in (("baseline", baseline), ("actual", actual)):
        for stream in compare:
            if stream not in obj:
                return f"{label} missing compared stream {stream!r}"
            val = obj[stream]
            if stream == "rc":
                if not _is_strict_int(val):
                    return f"{label}['rc'] must be an int (not bool)"
            elif not isinstance(val, str):
                return f"{label}[{stream!r}] must be a str"
    return None


def _matched_stream_info() -> dict[str, Any]:
    return {"matched": True, "first_diff_line": None, "expected": None, "actual": None, "diff_head": []}


def _diff_text_stream(
    stream: str,
    b_text: str,
    a_text: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if b_text == a_text:
        return _matched_stream_info(), None
    b_lines = b_text.splitlines(keepends=True)
    a_lines = a_text.splitlines(keepends=True)
    min_len = min(len(b_lines), len(a_lines))
    diff_idx = min_len
    for idx in range(min_len):
        if b_lines[idx] != a_lines[idx]:
            diff_idx = idx
            break
    line_no = diff_idx + 1
    exp_line = b_lines[diff_idx] if diff_idx < len(b_lines) else ""
    act_line = a_lines[diff_idx] if diff_idx < len(a_lines) else ""
    diff_iter = difflib.unified_diff(b_lines, a_lines, "baseline", "actual", n=1)
    diff_head = [ln.rstrip("\r\n") for _, ln in zip(range(20), diff_iter)]
    info = {
        "matched": False,
        "first_diff_line": line_no,
        "expected": repr(exp_line)[:60],
        "actual": repr(act_line)[:60],
        "diff_head": diff_head,
    }
    return info, _err_finding(
        "capture-mismatch",
        f"{stream} differs at line {line_no}",
        f"inspect {stream} diff around line {line_no} and align output with baseline",
    )


def _diff_rc_stream(b_rc: int, a_rc: int) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if b_rc == a_rc:
        return _matched_stream_info(), None
    info = {
        "matched": False,
        "first_diff_line": None,
        "expected": str(b_rc),
        "actual": str(a_rc),
        "diff_head": [],
    }
    return info, _err_finding(
        "capture-mismatch", f"rc differs: expected {b_rc} got {a_rc}", f"ensure exit code matches baseline ({b_rc})",
    )


def compare_capture(baseline: Any, actual: Any, *, compare: list[str]) -> dict[str, Any]:
    """Compare captured stdout/stderr/rc against a baseline across selected streams."""
    err = _validate_compare_inputs(baseline, actual, compare)
    if err is not None:
        return _compare_error(err)
    streams: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, Any]] = []
    for stream in compare:
        if stream == "rc":
            info, finding = _diff_rc_stream(baseline["rc"], actual["rc"])
        else:
            info, finding = _diff_text_stream(stream, baseline[stream], actual[stream])
        streams[stream] = info
        if finding is not None:
            findings.append(finding)
    matched = not findings
    status = "pass" if matched else "fail"
    return {
        "ok": matched,
        "status": status,
        "error": None,
        "findings": findings,
        "matched": matched,
        "streams": streams,
    }


def evidence_digest(
    *,
    name: str,
    schema_version: int,
    manifest_sha: str,
    manifest_gen: int,
    baseline_gen: int,
    baseline_shas: dict[str, str],
    file_hashes: dict[str, str],
    checker_digest: str,
    runtime: str,
    attestations_sha: str,
    amend_log_sha: str | None,
    check_result_sha: str,
    check_status: str,
) -> str:
    """Compute canonical SHA-256 digest over the 13 evidence fields."""
    return canonical_sha256({
        "name": name,
        "schema_version": schema_version,
        "manifest_sha": manifest_sha,
        "manifest_gen": manifest_gen,
        "baseline_gen": baseline_gen,
        "baseline_shas": baseline_shas,
        "file_hashes": file_hashes,
        "checker_digest": checker_digest,
        "runtime": runtime,
        "attestations_sha": attestations_sha,
        "amend_log_sha": amend_log_sha,
        "check_result_sha": check_result_sha,
        "check_status": check_status,
    })


def _is_hex64(val: Any) -> bool:
    return isinstance(val, str) and bool(_HEX64_RE.fullmatch(val))


def _is_hex64_map(val: Any) -> bool:
    if not isinstance(val, dict):
        return False
    return all(isinstance(k, str) and _is_hex64(v) for k, v in val.items())


def _validate_receipt_dict(
    obj: Any,
    label: str,
    expected_keys: frozenset[str],
    hex_keys: frozenset[str],
    str_keys: frozenset[str],
) -> str | None:
    if not isinstance(obj, dict):
        return f"{label} must be a dict, got {type(obj).__name__}"
    missing = sorted(expected_keys - set(obj.keys()))
    if missing:
        return f"{label} missing required keys: {missing}"
    unknown = sorted(str(k) for k in obj.keys() if k not in expected_keys)
    if unknown:
        return f"{label} has unknown keys: {unknown}"
    if "schema_version" in expected_keys and not _is_strict_int(obj["schema_version"]):
        return f"{label}['schema_version'] must be an int (not bool)"
    for k in sorted(hex_keys):
        if not _is_hex64(obj[k]):
            return f"{label}[{k!r}] must be a 64-char lowercase hex string"
    for k in sorted(_OPT_HEX_KEYS):
        if obj[k] is not None and not _is_hex64(obj[k]):
            return f"{label}[{k!r}] must be a 64-char lowercase hex string or None"
    for k in sorted(_GEN_KEYS):
        if not _is_strict_int(obj[k]) or obj[k] <= 0:
            return f"{label}[{k!r}] must be a positive int (not bool)"
    for k in sorted(_HEX_MAP_KEYS):
        if not _is_hex64_map(obj[k]):
            return f"{label}[{k!r}] must map str keys to 64-char lowercase hex strings"
    for k in sorted(str_keys):
        if not isinstance(obj[k], str):
            return f"{label}[{k!r}] must be a str"
    return None


def _diff_receipt_vs_current(
    receipt: dict[str, Any],
    current: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    findings: list[dict[str, Any]] = []
    stale: set[str] = set()
    stale_fix = "re-run `componentize finish <name>` then `componentize status <name>`"
    for key in _STALE_SCALAR_KEYS:
        if receipt[key] != current[key]:
            stale.add(key)
            findings.append(_err_finding(
                "receipt-stale",
                f"{key} changed",
                stale_fix,
            ))
    r_files: dict[str, str] = receipt["file_hashes"]
    c_files: dict[str, str] = current["file_hashes"]
    for path in sorted(set(r_files.keys()) | set(c_files.keys())):
        if path not in c_files:
            stale.add("file_hashes")
            findings.append(_err_finding("receipt-stale", f"file removed: {path}", stale_fix))
        elif path not in r_files:
            stale.add("file_hashes")
            findings.append(_err_finding("receipt-stale", f"file added: {path}", stale_fix))
        elif r_files[path] != c_files[path]:
            stale.add("file_hashes")
            findings.append(_err_finding("receipt-stale", f"file changed: {path}", stale_fix))
    return findings, sorted(stale)


def verify_receipt(receipt: Any, *, current: Any) -> dict[str, Any]:
    """Verify a componentize receipt against current state and evidence digest."""
    r_err = _validate_receipt_dict(receipt, "receipt", _RECEIPT_KEYS, _RECEIPT_HEX_KEYS, _RECEIPT_STR_KEYS)
    c_err = _validate_receipt_dict(current, "current", _CURRENT_KEYS, _CURRENT_HEX_KEYS, _CURRENT_STR_KEYS)
    err = r_err or c_err
    if err is not None:
        r_name = receipt.get("name") if isinstance(receipt, dict) and isinstance(receipt.get("name"), str) else None
        return {"ok": False, "status": "error", "error": err, "findings": [], "name": r_name, "stale": []}
    findings: list[dict[str, Any]] = []
    if receipt["schema_version"] != 1:
        findings.append(_err_finding("receipt-schema", f"expected schema_version 1, got {receipt['schema_version']!r}", "re-create receipt with schema_version 1"))
    if receipt["check_status"] != "pass":
        findings.append(_err_finding("receipt-not-passing", f"receipt check_status is {receipt['check_status']!r}, expected 'pass'", "fix failing checks and regenerate receipt"))
    expected_digest = evidence_digest(**{k: receipt[k] for k in _DIGEST_KEYS})
    if expected_digest != receipt["evidence_digest"]:
        findings.append(_err_finding("receipt-tampered", "evidence_digest does not match receipt fields", "re-run `componentize finish <name>` then `componentize status <name>`"))
    stale_findings, stale_keys = _diff_receipt_vs_current(receipt, current)
    findings.extend(stale_findings)
    findings.sort(key=lambda item: (str(item["id"]), str(item["detail"])))
    ok = not findings
    return {"ok": ok, "status": "pass" if ok else "fail", "error": None, "findings": findings, "name": receipt["name"], "stale": stale_keys}
