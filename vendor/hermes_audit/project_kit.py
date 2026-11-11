# vendored copy of the author's hermes_audit/project_kit.py (MIT licence, same as this plugin); do not edit
"""Scaffold a project folder from a declarative kit. Standard library only.

A kit is plain data (see `kits/README.md`): folders with a one-line purpose,
starter files, agent rules, and ignore/LFS lines. Applying a kit creates what
is missing and never overwrites a file. The only edits to existing files are
one managed block in `AGENTS.md` and appended lines in `.gitignore` /
`.gitattributes`. No kit text is executed and no model is involved.
"""
import errno
import json
import os
import re
import secrets
import stat

__all__ = ['validate_kit', 'resolve_variables', 'block_markers', 'render_agents_block', 'merge_block',
           'declared_dirs', 'placement_rules', 'kit_tree', 'inspect_kit', 'apply_kit', 'forbidden_roots']

SCHEMA_VERSION = 1
MAX_BLOCK_CHARS = 8000
CONTEXT_FILE_MAX_CHARS = 20000
BLOCK_PREFIX = '<!-- kit:'
_ID_RE = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
_KIND_RE = re.compile(r'^[a-z0-9_-]{1,24}$')
_EXT_RE = re.compile(r'^\.[a-z0-9]{1,12}$')
_VAR_RE = re.compile(r'\{\{([a-z_]+)\}\}')
_KIT_KEYS = {'schema_version', 'id', 'version', 'name', 'summary', 'tags', 'variables', 'dirs', 'files',
             'placement', 'agents', 'gitignore', 'gitattributes', 'availability'}
_REQUIRED = ('schema_version', 'id', 'version', 'name', 'summary', 'dirs', 'agents')
_DIR_KEYS = {'path', 'summary', 'why', 'listed'}
_FILE_KEYS = {'path', 'lines', 'json'}
_PLACEMENT_KEYS = {'kind', 'match_ext', 'dest_dir', 'lfs', 'reason'}
_AGENTS_KEYS = {'purpose', 'rules'}
_APPEND_FILES = (('gitignore', '.gitignore'), ('gitattributes', '.gitattributes'))
# Always refused as project folders: these folders and anything inside them ...
_SYSTEM_SUBTREE_ROOTS = ('/etc', '/usr', '/bin', '/sbin', '/boot', '/dev', '/proc', '/sys')
# ... and these folders themselves (their children, e.g. /var/tmp/x or /opt/project, stay allowed),
# plus every top-level folder whose name starts with 'lib' (/lib, /lib64, /libexec, ...).
_SYSTEM_EXACT_ROOTS = ('/var', '/opt')
# Every create, write and read below goes through an open folder handle and never
# follows a link: a link (or a non-folder) met on the way is refused, not followed.
_O_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)
_O_DIRECTORY = getattr(os, 'O_DIRECTORY', 0)
_O_CLOEXEC = getattr(os, 'O_CLOEXEC', 0)
_O_NONBLOCK = getattr(os, 'O_NONBLOCK', 0)
_DIR_FLAGS = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_NONBLOCK | _O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK | _O_CLOEXEC
_DIR_FD_CALLS = ('open', 'mkdir', 'stat', 'link', 'unlink', 'rename')
_NOT_A_DIR_ERRNOS = (errno.ELOOP, errno.ENOTDIR, getattr(errno, 'EMLINK', errno.ELOOP))
_NO_DIR_FD = 'this system cannot create files inside an open folder safely, so the kit was not applied'
_PARENT_BLOCKED = 'a parent folder is a symlink or a file'


def _finding(fid, severity, detail, fix):
    return {'id': fid, 'severity': severity, 'detail': detail, 'fix': fix}


def _envelope(findings, error=None, **extra):
    failed = any(f['severity'] == 'error' for f in findings)
    status = 'error' if error else ('fail' if failed else 'pass')
    return {'ok': status == 'pass', 'status': status, 'error': error, 'findings': findings, **extra}


def _bad_path(path):
    """Return why a kit-relative path is unsafe, or None."""
    if not isinstance(path, str) or not path:
        return 'must be a non-empty string'
    if path.startswith('/') or '\\' in path or '\x00' in path or '\n' in path or '\r' in path:
        return 'must be relative, with forward slashes'
    parts = path.split('/')
    if any(part in ('', '.', '..') for part in parts):
        return 'must not contain empty, "." or ".." segments'
    if parts[0] in ('.git', '.componentize'):
        return f'must not write inside {parts[0]}/'
    return None


def _is_line_list(value):
    return isinstance(value, list) and all(isinstance(line, str) and '\n' not in line and '\r' not in line for line in value)


def _one_line(value):
    return isinstance(value, str) and value.strip() and '\n' not in value and '\r' not in value


def _default_reason(kit):
    return f"declared by the {kit.get('id', '')} kit"


def _check_placement(kit, dir_paths, bad, check_text):
    placement = kit.get('placement', [])
    if not isinstance(placement, list):
        bad('placement', 'must be a list')
        return
    seen_kinds, seen_exts = set(), set()
    for index, entry in enumerate(placement):
        where = f'placement[{index}]'
        if not isinstance(entry, dict) or set(entry) - _PLACEMENT_KEYS \
                or any(k not in entry for k in ('kind', 'match_ext', 'dest_dir')):
            bad(where, 'must be {kind, match_ext, dest_dir, lfs?, reason?}')
            continue
        kind_val = entry['kind']
        if not isinstance(kind_val, str) or not _KIND_RE.fullmatch(kind_val):
            bad(where, 'kind must match ^[a-z0-9_-]{1,24}$')
        elif kind_val in seen_kinds:
            bad(where, f'duplicate kind {kind_val!r}')
        else:
            seen_kinds.add(kind_val)
        exts = entry['match_ext']
        if not isinstance(exts, list) or not exts or any(not isinstance(e, str) or not _EXT_RE.fullmatch(e) for e in exts):
            bad(where, 'match_ext must be a non-empty list of extensions matching ^\\.[a-z0-9]{1,12}$')
        else:
            for ext in exts:
                if ext in seen_exts:
                    bad(where, f'extension {ext!r} appears in more than one rule')
                seen_exts.add(ext)
        if not isinstance(entry['dest_dir'], str) or entry['dest_dir'] not in dir_paths:
            bad(where, f"dest_dir {entry['dest_dir']!r} must be a declared dir path")
        if type(entry.get('lfs', False)) is not bool:
            bad(where, 'lfs must be true or false')
        if 'reason' in entry and (not _one_line(entry['reason']) or len(entry['reason']) > 100):
            bad(where, 'reason must be one non-empty line of at most 100 characters')
        check_text(where, str(entry.get('reason', '')))


def validate_kit(kit):
    """Check a kit against schema v1. Returns {ok,status,error,findings}."""
    if not isinstance(kit, dict):
        return _envelope([], 'kit must be a JSON object')
    out = []

    def bad(where, detail, fix='see kits/README.md'):
        out.append(_finding('kit-invalid', 'error', f'{where}: {detail}', fix))

    for key in sorted(set(kit) - _KIT_KEYS):
        bad(key, 'unknown key', 'remove it or fix the spelling')
    for key in _REQUIRED:
        if key not in kit:
            bad(key, 'missing required key')
    if out:
        return _envelope(out)
    if kit.get('availability', 'available') not in ('available', 'coming_soon'):
        bad('availability', 'must be available or coming_soon')
    if kit['schema_version'] != SCHEMA_VERSION:
        bad('schema_version', f'must be {SCHEMA_VERSION}')
    if not isinstance(kit['id'], str) or not _ID_RE.fullmatch(kit['id']):
        bad('id', 'must be lowercase kebab-case, for example game-assets')
    if type(kit['version']) is not int or kit['version'] < 1:
        bad('version', 'must be a positive integer; raise it when the AGENTS.md block text changes')
    for key in ('name', 'summary'):
        if not _one_line(kit[key]):
            bad(key, 'must be one non-empty line')
    if not _is_line_list(kit.get('tags', [])):
        bad('tags', 'must be a list of strings')
    variables = kit.get('variables', {})
    if not isinstance(variables, dict) or any(
            not re.fullmatch(r'[a-z_]+', str(name)) or not isinstance(spec, dict)
            or set(spec) - {'default', 'description'} or not _one_line(spec.get('default'))
            for name, spec in variables.items()):
        bad('variables', 'must map lowercase names to {default, description}')
        variables = {}
    known = set(variables) | {'project'}
    seen = set()

    def check_text(where, text):
        for name in _VAR_RE.findall(text):
            if name not in known:
                bad(where, f'unknown variable {{{{{name}}}}}', 'declare it under variables or remove it')

    def check_path(where, path, kind):
        reason = _bad_path(_VAR_RE.sub('x', path) if isinstance(path, str) else path)
        if reason:
            bad(where, f'path {path!r} {reason}')
            return
        check_text(where, path)
        if (path, kind) in seen or (path, 'dir' if kind == 'file' else 'file') in seen:
            bad(where, f'path {path!r} is declared twice')
        seen.add((path, kind))

    dirs = kit['dirs']
    if not isinstance(dirs, list) or not dirs:
        bad('dirs', 'must be a non-empty list')
        dirs = []
    dir_paths = set()
    for index, entry in enumerate(dirs):
        where = f'dirs[{index}]'
        if not isinstance(entry, dict) or set(entry) - _DIR_KEYS or 'path' not in entry or 'summary' not in entry:
            bad(where, 'must be {path, summary, why?, listed?}')
            continue
        check_path(where, entry['path'], 'dir')
        if isinstance(entry['path'], str):
            dir_paths.add(entry['path'])
        if not _one_line(entry['summary']) or len(entry['summary']) > 80:
            bad(where, 'summary must be one line of at most 80 characters')
        why_val = entry.get('why', _default_reason(kit))
        if not _one_line(why_val) or len(why_val) > 100:
            bad(where, 'why must be one non-empty line of at most 100 characters')
        if type(entry.get('listed', True)) is not bool:
            bad(where, 'listed must be true or false')
        check_text(where, str(entry.get('summary', '')) + str(entry.get('why', '')))
    files = kit.get('files', [])
    if not isinstance(files, list):
        bad('files', 'must be a list')
        files = []
    for index, entry in enumerate(files):
        where = f'files[{index}]'
        if not isinstance(entry, dict) or set(entry) - _FILE_KEYS or 'path' not in entry \
                or ('lines' in entry) == ('json' in entry):
            bad(where, 'must be {path, lines} or {path, json}')
            continue
        check_path(where, entry['path'], 'file')
        if entry['path'] in ('AGENTS.md', '.gitignore', '.gitattributes'):
            bad(where, f'{entry["path"]} is managed by the agents/gitignore/gitattributes keys')
        if 'lines' in entry:
            if not _is_line_list(entry['lines']):
                bad(where, 'lines must be a list of single-line strings')
            else:
                check_text(where, '\n'.join(entry['lines']))
    _check_placement(kit, dir_paths, bad, check_text)
    agents = kit['agents']
    if not isinstance(agents, dict) or set(agents) - _AGENTS_KEYS or not _is_line_list(agents.get('purpose')) \
            or not agents.get('purpose') or not _is_line_list(agents.get('rules', [])):
        bad('agents', 'must be {purpose: [lines], rules?: [lines]} with at least one purpose line')
    else:
        text = '\n'.join(agents['purpose'] + agents.get('rules', []))
        check_text('agents', text)
        if BLOCK_PREFIX in text:
            bad('agents', 'must not contain kit marker comments')
    for key, _name in _APPEND_FILES:
        if not _is_line_list(kit.get(key, [])) or any(not line.strip() for line in kit.get(key, [])):
            bad(key, 'must be a list of non-empty single-line strings')
    if not out:
        values, err = resolve_variables(kit, 'x')
        if err or values is None:
            bad('variables', err or 'invalid variable defaults')
        else:
            block = render_agents_block(kit, values)
            if len(block) > MAX_BLOCK_CHARS:
                bad('agents', f'rendered AGENTS.md block is {len(block)} characters; the limit is {MAX_BLOCK_CHARS}',
                    'shorten rules or set listed=false on deep folders; put detail in a README file instead')
    return _envelope(out)


def resolve_variables(kit, root, variables=None):
    """Return (values, error). `project` defaults to the root folder name."""
    raw_vars = kit.get('variables', {}) if isinstance(kit, dict) else {}
    values = {name: spec['default'] for name, spec in raw_vars.items()
              if isinstance(spec, dict) and 'default' in spec} if isinstance(raw_vars, dict) else {}
    values['project'] = os.path.basename(root) or 'project'
    for name, value in (variables or {}).items():
        if name not in values:
            return None, f'unknown variable {name!r}; this kit accepts {sorted(values)}'
        values[name] = value
    for name, value in values.items():
        if not _one_line(value) or '\\' in value or '{{' in value or '<!--' in value or '-->' in value:
            return None, f'variable {name!r} must be one non-empty line without backslashes or comment markers, got {value!r}'
    if isinstance(kit, dict):
        for section, key in (('dirs', 'path'), ('files', 'path'), ('placement', 'dest_dir')):
            entries = kit.get(section)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                raw_path = entry.get(key) if isinstance(entry, dict) else None
                if isinstance(raw_path, str):
                    filled = _VAR_RE.sub(lambda m: values.get(m.group(1), m.group(0)), raw_path)
                    reason = _bad_path(filled)
                    if reason:
                        return None, f'path {filled!r} {reason}'
    return values, None


def _fill(text, values):
    return _VAR_RE.sub(lambda match: values[match.group(1)], text)


def block_markers(kit_id, version):
    return (f'{BLOCK_PREFIX}{kit_id} v{version} begin (managed block: edit outside it) -->',
            f'{BLOCK_PREFIX}{kit_id} end -->')


def render_agents_block(kit, values):
    """Return the managed AGENTS.md block text for a validated kit."""
    begin, end = block_markers(kit['id'], kit['version'])
    lines = [begin, f"## {_fill(kit['name'], values)}", '']
    lines += [_fill(line, values) for line in kit['agents']['purpose']]
    lines += ['', '### Layout', '']
    for entry in kit['dirs']:
        if entry.get('listed', True):
            lines.append(f"- `{_fill(entry['path'], values)}/`: {_fill(entry['summary'], values)}")
    lines.append('- Anything that fits no folder above: ask where it goes; do not invent a new top-level folder.')
    rules = kit['agents'].get('rules', [])
    if rules:
        lines += ['', '### Rules', ''] + [_fill(line, values) for line in rules]
    return '\n'.join(lines + [end]) + '\n'


def merge_block(existing, block, kit_id):
    """Return (text, state): created, appended, updated, current or malformed. Other kits' blocks are untouched."""
    return _merge_agents_raw(existing, block, kit_id, '\n')


def declared_dirs(kit, values):
    """Folders as [{dir, summary, why}], the shape a placement tool needs to route new files."""
    default_why = _default_reason(kit)
    return [{'dir': _fill(entry['path'], values), 'summary': _fill(entry['summary'], values),
             'why': _fill(entry.get('why', default_why), values)[:100]} for entry in kit['dirs']]


def placement_rules(kit, values):
    """Extension routing rules as [{kind, match_ext, dest_dir, lfs, reason}] with variables filled."""
    default_reason = _default_reason(kit)
    return [{'kind': entry['kind'], 'match_ext': list(entry['match_ext']),
             'dest_dir': _fill(entry['dest_dir'], values), 'lfs': bool(entry.get('lfs', False)),
             'reason': _fill(entry.get('reason', default_reason), values)} for entry in kit.get('placement', [])]


def kit_tree(kit, values):
    """Sorted relative paths the kit creates: folders with trailing '/', files, AGENTS.md, and ignore/LFS files."""
    paths = [f'{rel}/' if want == 'dir' else rel for rel, want, _content in _plan(kit, values)]
    paths.append('AGENTS.md')
    for key, name in _APPEND_FILES:
        if kit.get(key):
            paths.append(name)
    return sorted(paths)


def _probe_kind(path):
    """Return 'missing', 'dir', 'file', 'symlink' or 'other' without following links."""
    try:
        mode = os.lstat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return 'missing'
    if stat.S_ISLNK(mode):
        return 'symlink'
    if stat.S_ISDIR(mode):
        return 'dir'
    return 'file' if stat.S_ISREG(mode) else 'other'


def _kind_of(mode):
    if stat.S_ISLNK(mode):
        return 'symlink'
    if stat.S_ISDIR(mode):
        return 'dir'
    return 'file' if stat.S_ISREG(mode) else 'other'


def _dir_fd_ready():
    """True when this system can do every step relative to a folder handle without following links."""
    names = {getattr(fn, '__name__', '') for fn in os.supports_dir_fd}
    no_follow = {getattr(fn, '__name__', '') for fn in os.supports_follow_symlinks}
    return bool(_O_NOFOLLOW and _O_DIRECTORY) and all(name in names for name in _DIR_FD_CALLS) \
        and {'stat', 'link'} <= no_follow


def _probe_at(dir_fd, name):
    """Like _probe_kind, for `name` inside the open folder `dir_fd`."""
    try:
        mode = os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return 'missing'
    return _kind_of(mode)


def _open_dir_at(dir_fd, name):
    """Return (fd, None) for a real folder, or (None, kind) when `name` is a link, a file or missing."""
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=dir_fd), None
    except FileNotFoundError:
        return None, 'missing'
    except OSError as exc:
        if exc.errno not in _NOT_A_DIR_ERRNOS:
            raise
    kind = _probe_at(dir_fd, name)
    return None, ('other' if kind == 'dir' else kind)


def _parent_at(root_fd, rel):
    """Open the folder holding rel by walking from root_fd. Returns (fd, leaf, None) or (None, leaf, kind)."""
    parts = rel.split('/')
    cur = os.dup(root_fd)
    try:
        for name in parts[:-1]:
            nxt, kind = _open_dir_at(cur, name)
            if nxt is None:
                os.close(cur)
                cur = None
                return None, parts[-1], kind
            os.close(cur)
            cur = nxt
        fd, cur = cur, None
        return fd, parts[-1], None
    finally:
        if cur is not None:
            os.close(cur)


def _kind_at_rel(root_fd, rel):
    """What rel is under root_fd, without following a link at any step (a linked parent reports 'symlink')."""
    parent, leaf, blocked = _parent_at(root_fd, rel)
    if parent is None:
        return 'symlink' if blocked == 'symlink' else 'missing'
    try:
        return _probe_at(parent, leaf)
    finally:
        os.close(parent)


def _read_raw_at(dir_fd, name):
    """Return (text, 'file'), or (None, kind) when name is not a regular file. Never follows a link or blocks."""
    try:
        fd = os.open(name, _READ_FLAGS, dir_fd=dir_fd)
    except FileNotFoundError:
        return None, 'missing'
    except OSError as exc:
        if exc.errno not in _NOT_A_DIR_ERRNOS:
            raise
        return None, _probe_at(dir_fd, name)
    try:
        kind = _kind_of(os.fstat(fd).st_mode)
    except BaseException:
        os.close(fd)
        raise
    if kind != 'file':
        os.close(fd)
        return None, kind
    with open(fd, encoding='utf-8', newline='') as handle:
        return handle.read(), 'file'


def _first_eol(raw_text):
    if not raw_text:
        return '\n'
    idx = raw_text.find('\n')
    if idx > 0 and raw_text[idx - 1] == '\r':
        return '\r\n'
    return '\n'


def _open_tmp(dir_fd, mode=0o666):
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_NOFOLLOW | _O_CLOEXEC
    for _ in range(15):
        tmp = f'.kit-tmp-{secrets.token_hex(8)}'
        try:
            return os.open(tmp, flags, mode, dir_fd=dir_fd), tmp
        except FileExistsError:
            continue
    tmp = f'.kit-tmp-{secrets.token_hex(8)}'
    return os.open(tmp, flags, mode, dir_fd=dir_fd), tmp


def _unlink_tmp(dir_fd, tmp):
    try:
        os.unlink(tmp, dir_fd=dir_fd)
    except FileNotFoundError:
        pass


def _write_atomic(dir_fd, name, text):
    """Replace the regular file `name` through a temporary sibling. Returns False (writes nothing) if it is not one."""
    try:
        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        info = None
    if info is not None and not stat.S_ISREG(info.st_mode):
        return False
    mode = None if info is None else stat.S_IMODE(info.st_mode)
    # A rewrite stays private until it carries the destination's own mode.
    fd, tmp = _open_tmp(dir_fd, 0o666 if mode is None else 0o600)
    replaced = False
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
            handle.write(text)
            if mode is not None:
                handle.flush()
                os.fchmod(handle.fileno(), mode)
        os.replace(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        replaced = True
    finally:
        if not replaced:
            _unlink_tmp(dir_fd, tmp)
    return True


def _create_exclusive(dir_fd, name, text):
    """Create a new file without replacing an existing destination. Returns True if created."""
    fd, tmp = _open_tmp(dir_fd)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
            handle.write(text)
        try:
            os.link(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
            return True
        except FileExistsError:
            return False
    finally:
        _unlink_tmp(dir_fd, tmp)


def _os_error_line(path, exc):
    detail = ' '.join(str(exc).splitlines()).strip()
    if str(path) not in detail:
        detail = f'{detail} ({path})' if detail else str(path)
    return f'{exc.__class__.__name__}: {detail}'


def _is_forbidden_or_ancestor(root, forbidden):
    if root == os.path.sep:
        return True
    prefix = root + os.path.sep
    for entry in forbidden:
        norm = os.path.normpath(entry) if isinstance(entry, str) and entry else ''
        if norm and (root == norm or norm.startswith(prefix)):
            return True
    return False


def forbidden_roots(home, hermes_home=None):
    """Keyword arguments for apply_kit/inspect_kit that refuse the home, its ancestors and its Hermes homes.

    Pure: no environment, no ~ expansion, no filesystem. home and hermes_home are used
    only when they are non-empty absolute strings (the caller expands ~ and relative
    values first); anything else is dropped. Returns {forbidden, hermes_homes}.
    """
    forbidden, hermes_homes = (), []
    if _is_abs_str(home):
        home = os.path.normpath(home)
        forbidden = (home,)
        hermes_homes.append(os.path.normpath(os.path.join(home, '.hermes')))
    if _is_abs_str(hermes_home):
        norm = os.path.normpath(hermes_home)
        if norm not in hermes_homes:
            hermes_homes.append(norm)
    return {'forbidden': forbidden, 'hermes_homes': tuple(hermes_homes)}


def _is_abs_str(value):
    return isinstance(value, str) and bool(value) and os.path.isabs(value)


def _is_same_or_inside(path, base):
    if base == os.path.sep:
        return True
    return path == base or path.startswith(base + os.path.sep)


def _is_lib_root(path):
    return os.path.dirname(path) == os.path.sep and os.path.basename(path).startswith('lib')


def _normalize_hermes_homes(hermes_homes):
    """Normalized entries, or an error: a bad safety entry must not silently disable protection."""
    if isinstance(hermes_homes, (str, bytes)) or not isinstance(hermes_homes, (tuple, list)):
        return None, f'hermes_homes must be a tuple or list of absolute paths, got {hermes_homes!r}'
    for entry in hermes_homes:
        if not _is_abs_str(entry):
            return None, f'hermes_homes entries must be absolute paths, got {entry!r}'
    return tuple(os.path.normpath(entry) for entry in hermes_homes), None


def _protected_reason(root, hermes_homes, resolve=os.path.realpath):
    """Why an absolute normalized root is a Hermes home or a system folder, or None.

    Both the lexical root and its resolved form (resolve = non-strict realpath: the
    existing prefix is resolved, a missing suffix kept, nothing created) are compared
    with each entry and its resolved form, so links into or out of a protected folder
    do not get around the rule. Comparisons are string based: alternate casing on
    case-insensitive filesystems, bind mounts and links retargeted after the check
    are not covered.
    """
    real_root = resolve(root)
    for entry in hermes_homes:
        real_entry = resolve(entry)
        if (_is_same_or_inside(root, entry) or _is_same_or_inside(real_root, real_entry)
                or _is_same_or_inside(root, real_entry) or _is_same_or_inside(real_root, entry)):
            return f'refusing {root!r}: pick a dedicated folder outside Hermes home ({entry})'
    system = f'refusing {root!r}: pick a dedicated folder, not a system directory'
    for sys_root in _SYSTEM_SUBTREE_ROOTS:
        real_sys = resolve(sys_root)
        if (_is_same_or_inside(root, sys_root) or _is_same_or_inside(real_root, sys_root)
                or (real_sys != os.path.sep and _is_same_or_inside(real_root, real_sys))):
            return system
    for exact_root in _SYSTEM_EXACT_ROOTS:
        real_exact = resolve(exact_root)
        if root == exact_root or real_root == exact_root or (real_exact != os.path.sep and real_root == real_exact):
            return system
    if _is_lib_root(root) or _is_lib_root(real_root):
        return system
    return None


def _check_root(root, forbidden, hermes_homes=(), resolved=None):
    """Why root is refused, or None. When `resolved` is a list, the resolved root the check decided on is appended."""
    homes, error = _normalize_hermes_homes(hermes_homes)
    if error:
        return error
    if not isinstance(root, str) or not os.path.isabs(root) or os.path.normpath(root) != root:
        return f'project folder must be an absolute normalized path, got {root!r}'
    if _is_forbidden_or_ancestor(root, forbidden):
        return f'refusing {root!r}: pick a dedicated folder, not a home or filesystem root'
    try:
        # Python 3.11's realpath raises OSError (EINVAL) when a link is swapped during the call.
        real_root = os.path.realpath(root)
        if resolved is not None:
            resolved.append(real_root)
        reason = _protected_reason(root, homes, resolve=lambda path: real_root if path == root else os.path.realpath(path))
    except OSError as exc:
        return _os_error_line(root, exc)
    if reason:
        return reason
    try:
        kind = _probe_kind(root)
    except OSError as exc:
        return _os_error_line(root, exc)
    if kind not in ('missing', 'dir'):
        return f'{root!r} exists and is a {kind}, not a directory'
    cur = os.path.dirname(root)
    while cur and cur != os.path.sep:
        try:
            pmode = os.stat(cur).st_mode
        except FileNotFoundError:
            nxt = os.path.dirname(cur)
            if nxt == cur:
                break
            cur = nxt
            continue
        except OSError as exc:
            return _os_error_line(root, exc)
        if not stat.S_ISDIR(pmode):
            return f'NotADirectoryError: parent of {root!r} ({cur!r}) is not a directory'
        break
    return None


def _plan(kit, values):
    """Ordered (relative path, kind, content) rows: every parent folder, then files."""
    rows, seen = [], set()

    def add_dir(rel):
        parts = rel.split('/')
        for depth in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:depth])
            if prefix not in seen:
                seen.add(prefix)
                rows.append((prefix, 'dir', None))

    for entry in kit['dirs']:
        add_dir(_fill(entry['path'], values))
    for entry in kit.get('files', []):
        rel = _fill(entry['path'], values)
        if '/' in rel:
            add_dir(rel.rsplit('/', 1)[0])
        if 'json' in entry:
            content = json.dumps(entry['json'], indent=2) + '\n'
        else:
            content = ''.join(_fill(line, values) + '\n' for line in entry['lines'])
        rows.append((rel, 'file', content))
    return rows


def _prepare(kit, root, variables, forbidden, hermes_homes, resolved=None):
    checked = validate_kit(kit)
    if not checked['ok']:
        return None, None, _envelope(checked['findings'], checked['error'] or 'kit is invalid', root=root, items=[])
    if kit.get('availability') == 'coming_soon':
        return None, None, _envelope(
            [], f"{kit['id']} is coming soon and cannot be applied yet.", root=root, items=[])
    error = _check_root(root, tuple(forbidden), hermes_homes, resolved)
    values = None
    if not error:
        values, error = resolve_variables(kit, root, variables)
    if not error and not _dir_fd_ready():
        error = _NO_DIR_FD
    if error:
        return None, None, _envelope([], error, root=root, items=[])
    rows = _plan(kit, values)
    for rel, _want, _content in rows:
        reason = _bad_path(rel)
        if reason:
            return None, None, _envelope([], f'path {rel!r} {reason} after variable substitution', root=root, items=[])
    return values, rows, None


def _walk_refusal(root):
    return f'refusing {root!r}: a folder on the way to it is a symbolic link or not a folder, so nothing was written there'


def _open_root(root, real_root, create):
    """Open the resolved root by walking from '/' without following links.

    Missing folders are made one at a time when `create`. Returns (fd, None),
    (None, None) when the root is missing and not created, or (None, error).
    """
    cur = os.open(os.path.sep, _DIR_FLAGS)
    try:
        for name in [part for part in real_root.split(os.path.sep) if part]:
            nxt, kind = _open_dir_at(cur, name)
            if nxt is None and kind == 'missing' and create:
                try:
                    os.mkdir(name, dir_fd=cur)
                except FileExistsError:
                    pass
                nxt, kind = _open_dir_at(cur, name)
            if nxt is None:
                return None, (None if kind == 'missing' and not create else _walk_refusal(root))
            os.close(cur)
            cur = nxt
        fd, cur = cur, None
        return fd, None
    finally:
        if cur is not None:
            os.close(cur)


def _append_state(root_fd, name, wanted):
    kind = 'missing' if root_fd is None else _probe_at(root_fd, name)
    if kind != 'file':
        return kind, None, wanted, []
    try:
        text, kind = _read_raw_at(root_fd, name)
    except (OSError, UnicodeDecodeError) as exc:
        return f'unreadable ({exc.__class__.__name__})', None, wanted, []
    if kind != 'file':
        return kind, None, wanted, []
    have = {line.strip() for line in text.splitlines()}
    negated = [line for line in wanted if name == '.gitignore' and f'!{line.strip()}' in have]
    missing = [line for line in wanted if line.strip() not in have and line not in negated]
    return 'file', text, missing, negated


def _warn_negated(findings, name, negated):
    for line in negated:
        findings.append(_finding('ignore-line-negated', 'warn',
                                 f'{name} contains !{line}, so {line!r} was not added',
                                 f'remove !{line} from {name} if you want {line} ignored'))


def inspect_kit(kit, root, *, variables=None, forbidden=(), hermes_homes=()):
    """Report what a kit expects and what is present. Read-only.

    Refuses `/`, each `forbidden` entry and its ancestors, each `hermes_homes` entry and
    anything inside it, and system folders (always). Reads through an open handle of
    the checked folder and never follows a link. Returns
    {ok,status,error,findings,root,kit,items,declared_dirs,placement_rules}.
    """
    resolved = []
    values, rows, failure = _prepare(kit, root, variables, forbidden, hermes_homes, resolved)
    if failure or rows is None or values is None:
        return failure
    findings, items = [], []

    def miss(rel, detail):
        findings.append(_finding('kit-missing', 'error', f'{rel}: {detail}', 'apply the kit again; it only adds what is missing'))

    root_fd = None
    try:
        root_fd, refusal = _open_root(root, resolved[0], create=False)
        if refusal:
            return _envelope([], refusal, root=root, items=[])
        for rel, want, _content in rows:
            kind = 'missing' if root_fd is None else _kind_at_rel(root_fd, rel)
            items.append({'path': rel, 'state': 'present' if kind == want else kind})
            if kind != want:
                miss(rel, f'expected {want}, found {kind}')
        state = 'missing' if root_fd is None else _probe_at(root_fd, 'AGENTS.md')
        if state == 'file':
            try:
                text, state = _read_raw_at(root_fd, 'AGENTS.md')
                if state == 'file':
                    state = merge_block(text.replace('\r\n', '\n'), render_agents_block(kit, values), kit['id'])[1]
            except (OSError, UnicodeDecodeError) as exc:
                state = f'unreadable ({exc.__class__.__name__})'
            state = {'appended': 'no managed block', 'updated': 'managed block out of date'}.get(state, state)
        items.append({'path': 'AGENTS.md', 'state': state})
        if state != 'current':
            miss('AGENTS.md', state)
        for key, name in _APPEND_FILES:
            wanted = kit.get(key, [])
            if not wanted:
                continue
            kind, _text, missing, negated = _append_state(root_fd, name, wanted)
            _warn_negated(findings, name, negated)
            state = 'present' if kind == 'file' and not missing else (f'{len(missing)} line(s) missing' if kind == 'file' else kind)
            items.append({'path': name, 'state': state})
            if state != 'present':
                miss(name, state)
    except OSError as exc:
        return _envelope(findings, _os_error_line(root, exc), root=root, kit=kit['id'], items=items)
    finally:
        if root_fd is not None:
            os.close(root_fd)
    return _envelope(findings, root=root, kit=kit['id'], items=items,
                     declared_dirs=declared_dirs(kit, values), placement_rules=placement_rules(kit, values))


def _create_missing(parent_fd, name, rel, want, content, items, conflict):
    if want == 'dir':
        try:
            os.mkdir(name, dir_fd=parent_fd)
            created = True
        except FileExistsError:
            created = False
    else:
        created = _create_exclusive(parent_fd, name, content)
    if created:
        items.append({'path': rel, 'state': 'created'})
    else:
        now_kind = _probe_at(parent_fd, name)
        if now_kind == want:
            items.append({'path': rel, 'state': 'kept'})
        else:
            conflict(rel, f'expected {want}, found {now_kind}')


def _apply_row(root_fd, rel, want, content, items, conflict):
    parent, leaf, blocked = _parent_at(root_fd, rel)
    if parent is None:
        return conflict(rel, _PARENT_BLOCKED if blocked != 'missing' else f'expected {want}, found missing parent')
    try:
        kind = _probe_at(parent, leaf)
        if kind == want:
            items.append({'path': rel, 'state': 'kept'})
        elif kind != 'missing':
            conflict(rel, f'expected {want}, found {kind}')
        else:
            _create_missing(parent, leaf, rel, want, content, items, conflict)
    finally:
        os.close(parent)
    return None


def apply_kit(kit, root, *, variables=None, forbidden=(), hermes_homes=(), root_fd=None):
    """Create what the kit declares under root. Idempotent.

    Same target refusals as inspect_kit, checked before anything is written. Every
    folder and file is made relative to one open handle of the root and no link is
    followed: a link or a non-folder on the way is reported as a conflict. A caller
    that already holds a checked handle passes it as `root_fd` (it stays open and
    the root path is then never opened). Returns
    {ok,status,error,findings,root,kit,items,declared_dirs,placement_rules}.
    """
    resolved = []
    values, rows, failure = _prepare(kit, root, variables, forbidden, hermes_homes, resolved)
    if failure or rows is None or values is None:
        return failure
    findings, items = [], []

    def conflict(rel, detail):
        findings.append(_finding('kit-conflict', 'error', f'{rel}: {detail}',
                                 f'move {os.path.join(root, rel)} aside, then apply again'))
        items.append({'path': rel, 'state': 'conflict'})

    held = None
    try:
        if root_fd is None:
            held, refusal = _open_root(root, resolved[0], create=True)
            if refusal or held is None:
                return _envelope([], refusal or _walk_refusal(root), root=root, items=[])
            fd = held
        else:
            if type(root_fd) is not int or not stat.S_ISDIR(os.fstat(root_fd).st_mode):
                return _envelope([], f'refusing {root!r}: root_fd is not an open folder', root=root, items=[])
            fd = root_fd
        for rel, want, content in rows:
            _apply_row(fd, rel, want, content, items, conflict)
        _apply_agents(kit, fd, root, values, findings, items, conflict)
        for key, name in _APPEND_FILES:
            if kit.get(key):
                _apply_append(fd, name, kit[key], findings, items, conflict)
    except OSError as exc:
        return _envelope(findings, _os_error_line(root, exc), root=root, kit=kit['id'], items=items)
    finally:
        if held is not None:
            os.close(held)
    return _envelope(findings, root=root, kit=kit['id'], items=items,
                     declared_dirs=declared_dirs(kit, values), placement_rules=placement_rules(kit, values))


def _merge_agents_raw(raw_existing, block, kit_id, eol):
    block_eol = block.replace('\n', eol) if eol == '\r\n' else block
    if raw_existing is None:
        return block_eol, 'created'
    begin, end = block_markers(kit_id, '')
    begin_prefix = begin.partition(' begin')[0]
    begins, ends = raw_existing.count(begin_prefix), raw_existing.count(end)
    if begins == 0 and ends == 0:
        if not raw_existing or raw_existing.endswith(('\r\n\r\n', '\n\n', '\r\n\n', '\n\r\n')):
            gap = ''
        elif raw_existing.endswith('\n'):
            gap = eol
        else:
            gap = eol + eol
        return raw_existing + gap + block_eol, 'appended'
    start, stop = raw_existing.find(begin_prefix), raw_existing.find(end)
    if begins != 1 or ends != 1 or stop < start:
        return raw_existing, 'malformed'
    stop += len(end)
    if raw_existing[stop:stop + 2] == '\r\n':
        stop += 2
    elif raw_existing[stop:stop + 1] == '\n':
        stop += 1
    merged = raw_existing[:start] + block_eol + raw_existing[stop:]
    return merged, ('current' if merged == raw_existing else 'updated')


def _apply_agents(kit, root_fd, root, values, findings, items, conflict):
    path = os.path.join(root, 'AGENTS.md')
    kind = _probe_at(root_fd, 'AGENTS.md')
    block = render_agents_block(kit, values)
    initial = f"# {values['project']}\n\n" + block
    if kind == 'missing' and _create_exclusive(root_fd, 'AGENTS.md', initial):
        merged, state = initial, 'created'
    else:
        if kind == 'missing':
            kind = _probe_at(root_fd, 'AGENTS.md')
        if kind != 'file':
            return conflict('AGENTS.md', f'expected file, found {kind}')
        try:
            raw_existing, kind = _read_raw_at(root_fd, 'AGENTS.md')
        except UnicodeDecodeError:
            return conflict('AGENTS.md', 'not UTF-8 text')
        if kind != 'file':
            return conflict('AGENTS.md', f'expected file, found {kind}')
        merged, state = _merge_agents_raw(raw_existing, block, kit['id'], _first_eol(raw_existing))
        if state == 'malformed':
            findings.append(_finding('agents-block-malformed', 'error',
                                     f"AGENTS.md has a damaged kit:{kit['id']} block (markers missing, repeated or out of order)",
                                     f"delete the kit:{kit['id']} marker lines in {path}, then apply again"))
            items.append({'path': 'AGENTS.md', 'state': 'conflict'})
            return None
        if state != 'current' and not _write_atomic(root_fd, 'AGENTS.md', merged):
            return conflict('AGENTS.md', f"expected file, found {_probe_at(root_fd, 'AGENTS.md')}")
    norm_merged = merged.replace('\r\n', '\n')
    if len(norm_merged) > CONTEXT_FILE_MAX_CHARS:
        findings.append(_finding('agents-too-long', 'warn',
                                 f'AGENTS.md is {len(norm_merged)} characters; agents truncate context files over {CONTEXT_FILE_MAX_CHARS}',
                                 'shorten the text outside the managed block'))
    items.append({'path': 'AGENTS.md', 'state': 'kept' if state == 'current' else state})
    return None


def _apply_append(root_fd, name, wanted, findings, items, conflict):
    kind, text, missing, negated = _append_state(root_fd, name, wanted)
    if kind not in ('missing', 'file'):
        return conflict(name, f'expected file, found {kind}')
    _warn_negated(findings, name, negated)
    if not missing:
        items.append({'path': name, 'state': 'kept'})
        return None
    if kind == 'missing':
        if _create_exclusive(root_fd, name, '\n'.join(missing) + '\n'):
            items.append({'path': name, 'state': 'created'})
            return None
        kind, text, missing, negated = _append_state(root_fd, name, wanted)
        if kind != 'file':
            return conflict(name, f'expected file, found {kind}')
        _warn_negated(findings, name, negated)
        if not missing:
            items.append({'path': name, 'state': 'kept'})
            return None
    text = text or ''
    eol = _first_eol(text)
    lead = '' if not text or text.endswith('\n') else eol
    if not _write_atomic(root_fd, name, text + lead + eol.join(missing) + eol):
        return conflict(name, f'expected file, found {_probe_at(root_fd, name)}')
    items.append({'path': name, 'state': 'appended'})
    return None
