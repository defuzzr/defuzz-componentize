# vendored copy of the author's componentize/_plugins.py (MIT licence, same as this plugin); do not edit
"""Runner-owned plugin inventory and bounded capture context (not a component)."""
import os
from pathlib import Path
import tempfile

from componentize import _io
from hermes_audit import componentize_evidence, tool_schema_cost

_EXCLUDED = {'.git', '.venv', 'venv', '.worktrees', 'node_modules'}


def _finding(detail):
    return {'id': 'plugin-inventory', 'severity': 'error', 'detail': 'plugin inventory: ' + detail,
            'fix': 'Declare every plugin.yaml and Python module in files or inputs; use regular files, not symlinks.'}


def _binary_warning(path, root):
    if path.suffix not in ('.pyc', '.so', '.pyd'):
        return None
    source = (path.parent.parent/(path.name.split('.')[0] + '.py')
              if path.parent.name == '__pycache__' else path.with_suffix('.py'))
    if path.suffix == '.pyc' and source.is_file():
        return None
    return {'id':'plugin-binary-inventory', 'severity':'warn', 'plugin_path':str(root),
            'detail':f'Unhashed executable plugin member: {path}',
            'fix':'Review native extensions and sourceless bytecode; capture is not a sandbox.'}


def _binary_warnings(root):
    """Standalone reports lack an integrity snapshot; scan without following links."""
    root = Path(root)
    if any(part.is_symlink() for part in [root, *root.parents]):
        return []
    findings = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        dirs[:] = sorted(d for d in dirs if d not in _EXCLUDED and not (parent/d).is_symlink())
        for name in sorted(files):
            path = parent/name
            if name not in _EXCLUDED and not path.is_symlink():
                warning = _binary_warning(path, root)
                if warning:
                    findings.append(warning)
    return findings


def inventory(manifest, *, include_warnings=False):
    """Enumerate without following symlinks; keep existing no-follow hash policy."""
    rows, findings = [], []
    declared = {entry['path'] for entry in manifest['files']} | set(manifest.get('inputs', []))
    for plugin in manifest.get('hermes_plugins', []):
        root = Path(plugin['path'])
        try:
            if any(part.is_symlink() for part in [root, *root.parents]):
                findings.append(_finding(f'symlink root refused: {root}'))
                continue
            if not root.exists():
                # Missing plugin is an unknown reading, not invented inventory.
                rows.append([str(root), None, None])
                continue
            if not root.is_dir():
                findings.append(_finding(f'not a directory: {root}'))
                continue
            pending = [root]
            while pending:
                directory = pending.pop()
                for path in sorted(directory.iterdir()):
                    if path.name in _EXCLUDED:
                        continue
                    if path.is_symlink():
                        findings.append(_finding(f'symlink refused: {path}'))
                        continue
                    if path.is_dir():
                        pending.append(path)
                    elif path.suffix in ('.pyc', '.so', '.pyd'):
                        warning = _binary_warning(path, root) if include_warnings else None
                        if warning:
                            findings.append(warning)
                    elif path.suffix == '.py' or path == root/'plugin.yaml':
                        st = path.stat()
                        rows.append([str(root), path.relative_to(root).as_posix(), st.st_size])
                        if str(path) not in declared:
                            findings.append(_finding(f'undeclared member: {path}'))
        except OSError as exc:
            findings.append(_finding(f'unreadable {root}: {exc}'))
    rows.sort(key=lambda row: (row[0], row[1] or ''))
    return componentize_evidence.canonical_sha256(rows), findings


def bounded_run(argv, *, cwd, env, timeout, max_output):
    """Protocol cap is UTF-8 bytes per stream, mapped to existing executor."""
    return _io.run_argv(argv, cwd=cwd, env=env, timeout=timeout, max_output_bytes=max_output)


def capture_path(python):
    """Deterministic PATH: interpreter dir first, fixed system dirs; never the caller's PATH."""
    entries = [os.path.dirname(os.path.abspath(python)) if os.path.dirname(python) else '', '/usr/local/bin', '/usr/bin', '/bin']
    return os.pathsep.join(dict.fromkeys(e for e in entries if e))


def capture(spec, *, binary_warnings=None):
    """Safety envelope is reserved, never inherited or overridden by spec env."""
    with tempfile.TemporaryDirectory(prefix='tool-schema-home-') as home:
        env = {**spec.get('env', {}), 'HOME': home, 'PATH': capture_path(spec['python']), 'LC_ALL': 'C', 'PYTHONDONTWRITEBYTECODE': '1'}
        result = tool_schema_cost.capture_plugin(plugin_dir=spec['path'], python=spec['python'], env=env,
            budget_tokens=spec.get('budget_tokens', 2000), timeout=spec.get('timeout', 60), run=bounded_run)
    if result['status'] == 'error':
        result.update(ok=True, status='pass', error=None)
    # Registered schemas need not inflate last-check state: keep measured evidence.
    # capture_plugin owns validation; guard only projection against contract drift.
    try:
        result['registered_names'] = [tool['function']['name'] for tool in result['tools']]
    except (KeyError, TypeError):
        result.update(capture='unknown', reason='bad protocol fields', tokens=None, groups=[], registered_names=[])
    if binary_warnings is None:
        binary_warnings = _binary_warnings(spec['path'])
    result.setdefault('findings', []).extend(binary_warnings)
    result.pop('tools', None)
    return result


def format_report(report):
    reading = 'unknown' if report['tokens'] is None else f"{report['tokens']} tokens"
    return f"tool schemas: {report['name'] or report['path']} {reading} (budget {report['budget_tokens']}) [{report['path']}]"
