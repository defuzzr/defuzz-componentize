# vendored copy of the author's hermes_audit/tool_schema_cost.py (MIT licence, same as this plugin); do not edit
"""Advisory cost of declared schemas in a default recording context.

Not wire size or billing. Tool Search can defer this cost until tool_describe.
Each module is standalone and requires explicit paths/environment.
"""
import hashlib
import json
import re
import subprocess
from pathlib import Path

__all__ = [
    'parse_plugin_manifest',
    'capture_plugin',
    'find_repeated_subschemas',
    'estimate_tool_tokens',
]


def _yaml_scalar(text):
    text = text.strip()
    if text.startswith('"'):
        value = json.loads(text)
        if not isinstance(value, str) or not value:
            raise ValueError('expected string')
        return value
    if text.startswith("'"):
        if not re.fullmatch(r"'(?:[^']|'')+'", text):
            raise ValueError('unsupported quoted scalar')
        return text[1:-1].replace("''", "'")
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_./-]*', text) or text.lower() in ('null', 'true', 'false'):
        raise ValueError('unsupported scalar')
    return text


def _inline_list(text):
    if not (text.startswith('[') and text.endswith(']')):
        raise ValueError('expected inline list')
    inner = text[1:-1].strip()
    if not inner:
        return []
    # Split commas only outside quoted scalars; reject unrecognized residue.
    tokens = re.findall(r'''"(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^,]+''', inner)
    values = [_yaml_scalar(t) for t in tokens]
    if inner.count(',') != len(tokens)-1:
        raise ValueError('ambiguous list')
    return values


def parse_plugin_manifest(text):
    """Read only a deliberately narrow YAML subset; unknown is not zero tools."""
    values = {}
    active = None
    block_list = False
    try:
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            if line[:1].isspace() or line.startswith('- '):
                if active == 'provides_tools':
                    item = line.strip()
                    if not block_list or not item.startswith('- '):
                        raise ValueError('unsupported block list')
                    values[active].append(_yaml_scalar(item[2:]))
                elif active == 'name':
                    raise ValueError('multiline name unsupported')
                continue
            active = None
            key, sep, value = line.partition(':')
            if key not in ('name', 'provides_tools'):
                continue
            if not sep or key in values:
                raise ValueError('duplicate or missing field')
            active = key
            value = value.strip()
            if key == 'name':
                values[key] = _yaml_scalar(value)
            else:
                block_list = not value
                values[key] = _inline_list(value) if value else []
        if block_list and not values.get('provides_tools'):
            raise ValueError('null list is not an empty inventory')
        if set(values) != {'name', 'provides_tools'}:
            raise ValueError('missing name or provides_tools')
        return _result(known=True, **values)
    except (ValueError, TypeError) as exc:
        result = _result(known=False, name=None, provides_tools=None)
        result['findings'] = [_finding('tool-schema-capture-unknown', f'Unsupported manifest: {exc}')]
        return result


# Child is an isolated uncategorized directory-plugin recorder, not a sandbox.
# Registration handles, category discovery and credentialed filtering are not emulated.
_CAPTURE = r'''
import contextlib, importlib.util, json, os, pathlib, sys, types
root, name, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
home = pathlib.Path(os.environ['HOME']).resolve()
sys.pycache_prefix = str(home / 'pycache')
sys.__stdout__.reconfigure(encoding='utf-8')
tools = []
reason = None
state = 'unknown'
refused = []
class Sink:
    def __init__(self): self.size = 0; self.overflow = False
    def write(self, text):
        self.size += len(text.encode('utf-8', errors='replace'))
        if self.size > limit:
            self.overflow = True
            raise RuntimeError('diagnostic-overflow')
        return len(text)
    def flush(self): pass
sink = Sink()
def guard(event, args):
    deny = event.startswith(('socket.', 'os.exec', 'os.spawn', 'os.posix_spawn')) or event in ('subprocess.Popen', 'os.system', 'os.fork', 'os.forkpty')
    paths = []
    if event == 'open':
        path, mode, flags = args
        if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (isinstance(flags, int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):
            paths = [path]
    if event in ('os.remove', 'os.rename', 'os.mkdir', 'os.rmdir', 'os.link', 'os.symlink', 'os.chmod', 'os.truncate', 'os.mkfifo', 'os.mknod', 'os.chown', 'os.utime'):
        paths = list(args[:2]) if event in ('os.rename', 'os.link', 'os.symlink') else list(args[:1])
    for path in paths:
        if not isinstance(path, (str, bytes, os.PathLike)) or not pathlib.Path(os.fsdecode(path)).resolve().is_relative_to(home):
            deny = True
    if deny:
        refused.append(event)
        raise PermissionError('guard-refusal: ' + event)
sys.addaudithook(guard)
# CPython does not emit audit events for these two filesystem APIs.
def guarded_special_file(event, function):
    def call(path, *args, **kwargs):
        guard(event, (path,))
        return function(path, *args, **kwargs)
    return call
for operation in ('mkfifo', 'mknod'):
    if hasattr(os, operation):
        setattr(os, operation, guarded_special_file('os.' + operation, getattr(os, operation)))
class Context:
    def __init__(self):
        self.unsupported_access = []
        self.schema_bytes = 0
        self.schema_overflow = False
    def register_tool(self, name, toolset, schema, handler, check_fn=None, requires_env=None, is_async=False, description="", emoji="", override=False):
        if self.schema_overflow:
            raise ValueError('schema-overflow')
        if not isinstance(name, str) or not isinstance(schema, dict):
            raise ValueError('malformed-schema')
        # Encode before retention to reject non-JSON and cyclic schemas.
        wrapped = {'type': 'function', 'function': {**schema, 'name': name}}
        encoded = json.dumps(wrapped, ensure_ascii=False, allow_nan=False)
        size = len(encoded.encode('utf-8'))
        if self.schema_bytes + size > limit:
            self.schema_overflow = True
            raise ValueError('schema-overflow')
        tools.append(json.loads(encoded))
        self.schema_bytes += size
        return None
    def get_config(self, key, default=None): return default
    def __getattr__(self, key):
        if key in ('register_skill', 'register_command', 'register_cli_command', 'register_hook', 'register_memory_provider', 'register_context_reference', 'register_provider', 'register_inference_provider', 'register_tts_provider', 'register_stt_provider'):
            return lambda *a, **k: None
        if key not in self.unsupported_access:
            self.unsupported_access.append(key)
        raise AttributeError('unsupported-context: ' + key)
ctx = Context()
try:
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        namespace = types.ModuleType('hermes_plugins'); namespace.__path__ = []
        sys.modules['hermes_plugins'] = namespace
        slug = name.replace('/', '__').replace('-', '_')
        fullname = 'hermes_plugins.' + slug
        spec = importlib.util.spec_from_file_location(fullname, pathlib.Path(root)/'__init__.py', submodule_search_locations=[root])
        module = importlib.util.module_from_spec(spec)
        sys.modules[fullname] = module
        spec.loader.exec_module(module)
        module.register(ctx)
        state = 'complete'
# This child is the plugin failure boundary: arbitrary ordinary plugin errors
# must retain the protocol. SystemExit is also a plugin failure; other
# BaseExceptions (notably KeyboardInterrupt) propagate, not a partial capture.
except (Exception, SystemExit) as exc:
    state = 'partial' if tools else 'unknown'
    reason = type(exc).__name__ + ': ' + str(exc)[:500]
if ctx.unsupported_access:
    state = 'unknown'
    reason = 'unsupported-context: ' + ', '.join(ctx.unsupported_access)
if refused or sink.overflow:
    state = 'unknown'
    reason = 'guard-refusal' if refused else 'diagnostic-overflow'
if ctx.schema_overflow:
    state, reason, tools = 'unknown', 'schema-overflow', []
payload = json.dumps({'capture': state, 'reason': reason, 'tools': tools}, ensure_ascii=False, allow_nan=False)
if len(('TOOL_SCHEMA_CAPTURE:' + payload + '\n').encode('utf-8')) > limit:
    payload = json.dumps({'capture': 'unknown', 'reason': 'schema-overflow', 'tools': []})
sys.__stdout__.write('TOOL_SCHEMA_CAPTURE:' + payload + '\n')
'''


def _run(argv, *, cwd, env, timeout, max_output):
    """Standalone fallback: truncates AFTER allocation; no process-tree guarantee."""
    try:
        proc = subprocess.run(argv, cwd=cwd, env=env, timeout=timeout, capture_output=True, text=True, errors='replace', check=False)
    except subprocess.TimeoutExpired:
        return dict(rc=None, stdout='', stderr='', timed_out=True, truncated=False)
    stdout = proc.stdout.encode('utf-8')
    stderr = proc.stderr.encode('utf-8')
    return dict(rc=proc.returncode, stdout=stdout[:max_output].decode('utf-8', errors='replace'), stderr=stderr[:max_output].decode('utf-8', errors='replace'), timed_out=False, truncated=len(stdout)+len(stderr)>max_output)


def _unknown(result, reason):
    result['reason'] = reason
    result['findings'].extend([_finding('tool-schema-cost', f"{result['path']}: tokens: null ({reason})"), _finding('tool-schema-capture-unknown', reason)])
    return result


def capture_plugin(*, plugin_dir, python, env, budget_tokens=2000, timeout=60, max_output=4*1024*1024, run=_run):
    """Import a trusted plugin in a child; inject bounded execution in runners."""
    result = _result(path=str(plugin_dir), name=None, budget_tokens=budget_tokens, capture='unknown', reason=None, tokens=None, tools=[], groups=[])
    try:
        manifest = parse_plugin_manifest((Path(plugin_dir)/'plugin.yaml').read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError) as exc:
        return _unknown(result, f'manifest unreadable: {exc}')
    result['name'] = manifest['name']
    if not manifest['known']:
        return _unknown(result, 'unsupported manifest')
    if not (Path(plugin_dir)/'__init__.py').is_file():
        return _unknown(result, 'missing __init__.py')
    try:
        output = run([python, '-I', '-B', '-c', _CAPTURE, str(plugin_dir), manifest['name'], str(max_output)], cwd=str(plugin_dir), env=dict(env), timeout=timeout, max_output=max_output)
    except OSError as exc:
        result.update(ok=False, status='error', error=str(exc))
        return _unknown(result, 'cannot execute interpreter')
    if output['timed_out'] or output['truncated'] or output['rc'] != 0:
        return _unknown(result, 'timeout' if output['timed_out'] else 'overflow' if output['truncated'] else 'nonzero child')
    lines = output['stdout'].splitlines()
    if len(lines) != 1 or not lines[0].startswith('TOOL_SCHEMA_CAPTURE:'):
        return _unknown(result, 'bad protocol')
    try:
        payload = json.loads(lines[0].split(':', 1)[1])
    except (ValueError, RecursionError):
        return _unknown(result, 'bad protocol JSON')
    if not isinstance(payload, dict) or payload.get('capture') not in ('complete', 'partial', 'unknown') or not isinstance(payload.get('tools'), list):
        return _unknown(result, 'bad protocol fields')
    for tool in payload['tools']:
        if (not isinstance(tool, dict) or tool.get('type') != 'function'
                or not isinstance(tool.get('function'), dict)
                or not isinstance(tool['function'].get('name'), str)
                or not isinstance(tool['function'].get('parameters', {}), dict)
                or not isinstance(tool['function'].get('description', ''), str)):
            return _unknown(result, 'bad protocol fields')
    result['capture'] = payload['capture']
    result['tools'] = payload['tools']
    if payload['capture'] != 'complete':
        return _unknown(result, str(payload.get('reason') or 'incomplete capture'))
    _, limited = _bounded_nodes(payload['tools'], 50_000, 64)
    if limited:
        result['capture'] = 'unknown'
        return _unknown(result, 'schema traversal limits')
    try:
        estimate = estimate_tool_tokens(payload['tools'])
        repeated = find_repeated_subschemas(payload['tools'])
    except (ValueError, TypeError, RecursionError):
        result['capture'] = 'unknown'
        return _unknown(result, 'malformed schemas')
    result.update(tokens=estimate['tokens'], per_tool=estimate['tools'], groups=repeated['groups'])
    result['findings'].append(_finding('tool-schema-cost', f"{result['path']} ({result['name']}): {result['tokens']} tokens; per-tool {estimate['tools']}"))
    result['findings'].extend(repeated['findings'])
    if result['tokens'] > budget_tokens:
        result['findings'].append(_finding('tool-schema-over-budget', f"{result['tokens']} tokens exceeds advisory budget {budget_tokens}", 'warn'))
    names = [t['name'] for t in estimate['tools']]
    if sorted(names) != sorted(manifest['provides_tools']) or len(set(names)) != len(names):
        result['findings'].append(_finding('tool-schema-provides-mismatch', 'Captured names differ from provides_tools or contain duplicates.', 'warn'))
    return result


def _finding(id, detail, severity='info', fix='Review the reading; no budget is an acceptance gate.'):
    return dict(id=id, severity=severity, detail=detail, fix=fix)


def _bounded_nodes(root, max_nodes, max_depth):
    """Preflight before recursive serialization, including skipped value keywords."""
    stack = [(root, (), 0)]
    nodes = []
    while stack:
        value, path, depth = stack.pop()
        if len(nodes) >= max_nodes or depth > max_depth:
            return nodes, True
        nodes.append((value, path))
        if isinstance(value, dict):
            stack.extend((v, path + (k,), depth + 1) for k, v in reversed(list(value.items())))
        elif isinstance(value, list):
            stack.extend((v, path + (i,), depth + 1) for i, v in reversed(list(enumerate(value))))
    return nodes, False


def _escape(value):
    return str(value).replace('~', '~0').replace('/', '~1')


_SCHEMA_ENCODER = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _canonical_nodes(nodes):
    """Bottom-up (character length, Merkle digest, canonical UTF-8 rope).

    Fixed-size child digests and length-prefixed key bytes make the typed
    Merkle encoding unambiguous. Ropes share children, never copy subtrees.
    Canonical JSON scalars retain json.dumps' exact escaping/number behavior.
    """
    summaries = {}
    encode = _SCHEMA_ENCODER.encode
    keys = {}
    for value, _ in reversed(nodes):
        if id(value) in summaries:
            continue
        if not isinstance(value, (dict, list)):
            # Legacy traversal skips tuple children. Fingerprinting each tuple
            # with a fresh budget can repeatedly scan a shared large subtree.
            # Replay candidate encoding instead, preserving JSON array semantics.
            if isinstance(value, tuple):
                raise ValueError('tuple requires legacy candidate replay')
            text = encode(value)
            raw = text.encode('utf-8')
            summaries[id(value)] = (len(text), hashlib.sha256(b'S' + raw).digest(), raw)
            continue
        is_dict = isinstance(value, dict)
        digest = hashlib.sha256(b'D' if is_dict else b'L')
        parts = [b'{' if is_dict else b'[']
        chars = 2
        items = sorted(value.items()) if is_dict else enumerate(value)
        for n, (key, child) in enumerate(items):
            if n:
                parts.append(b',')
                chars += 1
            if is_dict:
                # Let JSON normalize non-string keys just as the old encoder did.
                text = keys.get(key) if isinstance(key, str) else None
                if text is None:
                    text = encode(key) if isinstance(key, str) else encode({key: None})[1:-6]
                    if isinstance(key, str):
                        keys[key] = text
                raw = text.encode('utf-8')
                digest.update(len(raw).to_bytes(8, 'big'))
                digest.update(raw)
                parts.extend((raw, b':'))
                chars += len(text) + 1
            size, child_digest, rope = summaries[id(child)]
            chars += size
            digest.update(child_digest)
            parts.append(rope)
        parts.append(b'}' if is_dict else b']')
        summaries[id(value)] = (chars, digest.digest(), tuple(parts))
    return summaries


def _canonical_digest(rope):
    """Legacy SHA-256 tie-breaker, streamed without flattening the rope."""
    digest = hashlib.sha256()
    stack = [rope]
    while stack:
        part = stack.pop()
        if isinstance(part, bytes):
            digest.update(part)
        else:
            stack.extend(reversed(part))
    return digest.digest()


def _ordered_candidates(groups, ropes):
    """Only repeated, equal-size candidates need the legacy JSON digest."""
    by_size = {}
    for key, occurrences in groups.items():
        if len(occurrences) >= 2:
            by_size.setdefault(key[0], []).append((key, occurrences))
    for size in sorted(by_size, reverse=True):
        candidates = by_size[size]
        if len(candidates) > 1:
            candidates.sort(key=lambda item: _canonical_digest(ropes[item[0]]))
        yield from candidates


def find_repeated_subschemas(tools, *, min_chars=500, max_nodes=50_000, max_depth=64, max_examples=5):
    """Report occurrence-level, nonoverlapping duplication candidates."""
    groups = {}
    ropes = {}
    remaining = max_nodes
    partial = False
    batches = []
    deep = False
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            continue
        src = tool.get('function', tool)
        if not isinstance(src, dict):
            continue
        nodes, limited = _bounded_nodes(src.get('parameters', {}), remaining, max_depth)
        remaining -= len(nodes)
        if limited:
            partial = True
            continue
        batches.append((index, src, nodes))
        deep = deep or any(len(path) > 16 for _, path in nodes)
    # C's encoder wins for shallow/wide schemas; ropes avoid repeated large
    # subtree encoding on deep inputs. Use one mode across tools so equal
    # schemas always have the same fingerprint.
    summaries = {}
    if deep:
        try:
            for _, _, nodes in batches:
                summaries.update(_canonical_nodes(nodes))
        except (TypeError, ValueError, RecursionError):
            # Eager fingerprinting must not expose errors from values the
            # legacy candidate walk never serialized. Replay that walk below;
            # it also preserves strict UTF-8 errors only above min_chars.
            deep = False
    encode = _SCHEMA_ENCODER.encode
    for index, src, nodes in batches:
        for value, path in nodes:
            if not isinstance(value, dict) or any(k in ('examples', 'default', 'enum', 'const') and (n == 0 or path[n-1] not in ('properties', 'patternProperties', '$defs', 'definitions', 'dependentSchemas')) for n, k in enumerate(path)):
                continue
            if deep:
                chars, digest, rope = summaries[id(value)]
            else:
                text = encode(value)
                chars = len(text)
                if chars < min_chars:
                    continue
                rope = text.encode('utf-8')
                digest = hashlib.sha256(rope).digest()
            if chars >= min_chars:
                fingerprint = (chars, digest)
                ropes.setdefault(fingerprint, rope)
                groups.setdefault(fingerprint, []).append((index, src.get('name', ''), path))
    kept = {}
    reported = []
    for key, occurrences in _ordered_candidates(groups, ropes):
        available = [(i, name, p) for i, name, p in occurrences
                     if not any(p[:depth] in kept.get(i, ()) for depth in range(len(p) + 1))]
        if len(available) < 2:
            continue
        for i, _, p in available:
            kept.setdefault(i, set()).add(p)
        examples = [[_escape(name), *map(_escape, p)] for _, name, p in available[:max_examples]]
        patterns = []
        for _, name, p in available:
            pattern = [_escape(name), *('*' if isinstance(k, int) else _escape(k) for k in p)]
            if pattern not in patterns and len(patterns) < max_examples:
                patterns.append(pattern)
        reported.append(dict(chars=key[0], count=len(available), wasted_chars=key[0]*(len(available)-1), examples=examples, patterns=patterns))
    result = _result(groups=reported, partial=partial)
    if partial:
        result['findings'].append(_finding('tool-schema-duplication-partial', 'Traversal limits reached; duplication reading is partial.'))
    for group in reported:
        result['findings'].append(_finding('tool-schema-repeated-subschema', f"Duplication candidate: {group['chars']} chars x {group['count']}", 'warn', 'If copies sit under one parent (e.g. branches of a oneOf), define the field once in parent properties and leave {} stubs in branches; verify old/new validation equivalence before relying on it.'))
    return result


def _result(**values):
    return dict(ok=True, status="pass", error=None, findings=[], **values)


def estimate_tool_tokens(tools):
    """Hermes major-field chars/4 estimate; total is rounded only once."""
    rows = []
    chars = 0
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        src = tool.get('function') if isinstance(tool.get('function'), dict) else tool
        n = sum(len(v) for v in (src.get('name') or '', src.get('description') or '') if isinstance(v, str))
        n += len(json.dumps(src.get('parameters') or {}, ensure_ascii=False, separators=(',', ':')))
        rows.append({'name': src.get('name', ''), 'chars': n, 'tokens': (n + 3) // 4})
        chars += n
    return _result(chars=chars, tokens=(chars + 3) // 4, tools=rows)
