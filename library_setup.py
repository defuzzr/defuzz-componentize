"""Create or inspect the component library, using the bundled project-kit engine.

New libraries keep components in `<root>/components/`. A folder that already
has `hermes_audit/COMPONENTS.md` and no `components/COMPONENTS.md` keeps using
`hermes_audit/`, matching how the bundled checker picks its index.

Nothing here reads `~`, the working directory or the environment: the runner
passes every path. Existing files are never overwritten.
"""
import errno
import json
import os
from pathlib import Path
import re
import stat

from ._kit_runtime import (
    ENGINE, _hermes_homes, containment_report, create_from_verified, expand_dir,
    resolve_home, show_text, verify_target,
)

ROOT = Path(__file__).resolve().parent
COMPONENT_DIR = 'components'
LEGACY_DIR = 'hermes_audit'
INDEX_NAME = 'COMPONENTS.md'
CONTEXT_FILE_MAX_CHARS = ENGINE.CONTEXT_FILE_MAX_CHARS
MAX_LIBRARY_ROOT_CHARS = 1024
MAX_DIR_ENTRIES = 256
MAX_INDEX_BYTES = 262_144
MAX_INDEX_LINES = 4_096
_INDEX_ENTRY_RE = re.compile(r'^\s*(?:[-*]\s+)?`([a-z_][a-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\(')


def _load_library_kit():
    kit_file = ROOT / 'vendor/kits/component-library/kit.json'
    try:
        kit = json.loads(kit_file.read_text(encoding='utf-8'))
        if not isinstance(kit, dict):
            return None, 'component-library: kit.json must be a JSON object'
        return kit, None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        detail = ' '.join(str(exc).splitlines()).strip()
        return None, f'component-library: cannot read kit.json ({exc.__class__.__name__}: {detail})'


def __getattr__(name):
    if name in ('KIT', 'BLOCK_BEGIN', 'BLOCK_END'):
        kit, err = _load_library_kit()
        if kit is None:
            raise RuntimeError(err)
        begin, end = ENGINE.block_markers(kit['id'], kit['version'])
        if name == 'KIT':
            return kit
        return begin if name == 'BLOCK_BEGIN' else end
    raise AttributeError(name)


def _is_file(path):
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def component_dir(root):
    """Return the component folder name for root, by the same rule the checker uses."""
    if _is_file(os.path.join(root, COMPONENT_DIR, INDEX_NAME)):
        return COMPONENT_DIR
    if _is_file(os.path.join(root, LEGACY_DIR, INDEX_NAME)):
        return LEGACY_DIR
    return COMPONENT_DIR


def has_index(root):
    return any(_is_file(os.path.join(root, name, INDEX_NAME)) for name in (COMPONENT_DIR, LEGACY_DIR))


def check_typed_folder(raw, *, cwd, home, env=None, hermes_homes=()):
    """Check a folder typed for `init` with the settings page's rule.

    Returns (normalized_absolute_path, None) or (None, plain message). The
    sentences are the page's own (settings_rule). A relative folder is taken
    from cwd; a ".." segment is refused, never normalized away. An empty value,
    which the page reads as "no folder set", is refused here: init needs one.
    """
    from . import settings_rule
    root, error = settings_rule.check_library_root(raw, home=home, env=env, hermes_homes=hermes_homes, cwd=cwd)
    if error is not None:
        return None, error
    if not root:
        return None, settings_rule.M_EMPTY
    return root, None


def _variables(root):
    name = component_dir(root)
    return {'components': name, 'vendor_root': name}


def _point_fixes_at_init(result, root):
    for finding in result['findings']:
        if finding['id'] == 'kit-missing':
            finding['fix'] = f'run: hermes componentize init {show_text(root)}'
    return result


def _error_envelope(root, error, kit='component-library'):
    return {'ok': False, 'status': 'error', 'error': error, 'findings': [], 'root': root, 'kit': kit, 'items': []}


def inspect_library(root, *, forbidden=()):
    """Report which library pieces exist. Read-only."""
    kit, err = _load_library_kit()
    if kit is None:
        return {'ok': False, 'status': 'error', 'error': err, 'findings': [], 'root': root, 'kit': 'component-library', 'items': []}
    if not isinstance(root, str):
        return _error_envelope(root, 'project folder must be an absolute normalized path, got ' + repr(root))
    home, home_err = resolve_home(forbidden[0] if forbidden else None)
    if home_err:
        return _error_envelope(root, home_err)
    forbidden = (home,)
    verified, safety = verify_target(root, home=home)
    if safety or verified is None:
        return _error_envelope(root, safety or 'project folder must be an absolute normalized path')
    operate = verified.path
    verified.close()
    homes = tuple(_hermes_homes(home, None))
    return _point_fixes_at_init(
        ENGINE.inspect_kit(kit, operate, variables=_variables(operate), forbidden=forbidden, hermes_homes=homes),
        operate,
    )


def init_library(root, *, forbidden=()):
    """Create the library layout under root. Idempotent; existing files are kept."""
    kit, err = _load_library_kit()
    if kit is None:
        return {'ok': False, 'status': 'error', 'error': err, 'findings': [], 'root': root, 'kit': 'component-library', 'items': []}
    if not isinstance(root, str):
        return _error_envelope(root, 'project folder must be an absolute normalized path, got ' + repr(root))
    home, home_err = resolve_home(forbidden[0] if forbidden else None)
    if home_err:
        return _error_envelope(root, home_err)
    forbidden = (home,)
    verified, safety = verify_target(root, home=home)
    if safety or verified is None:
        return _error_envelope(root, safety or 'project folder must be an absolute normalized path')
    operate = verified.path
    fd = None
    try:
        fd, prep_err = create_from_verified(verified)
        if prep_err is not None or fd is None:
            return _error_envelope(root, prep_err or 'could not prepare the target folder')
        held = os.fstat(fd)
        homes = tuple(_hermes_homes(home, None))
        result = ENGINE.apply_kit(kit, operate, variables=_variables(operate), forbidden=forbidden,
                                  hermes_homes=homes, root_fd=fd)
        escape = containment_report(fd, held.st_dev, held.st_ino, operate, result)
        if escape:
            result['error'] = escape
            result['ok'] = False
            result['status'] = 'error'
    finally:
        verified.close()
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
    if result['error'] is None and not os.path.lexists(os.path.join(operate, '.git')):
        result['findings'].append({
            'id': 'library-not-git', 'severity': 'info', 'detail': 'folder is not a git repository',
            'fix': f'optional: git -C {show_text(operate)} init (AGENTS.md then applies to every subfolder)'})
    result['root'] = operate
    return result


def _status_result(state, message, *, ok=False, count=None, index_rel=None):
    return {
        'ok': ok,
        'state': state,
        'count': count,
        'index_rel': index_rel,
        'message': message,
    }


def _in_protected_tree(path, *, forbidden=(), forbidden_subtrees=()):
    if ENGINE._is_forbidden_or_ancestor(path, tuple(forbidden)):
        return True
    for raw in forbidden_subtrees:
        if not isinstance(raw, str) or not raw:
            continue
        norm = os.path.normpath(raw)
        if not os.path.isabs(norm):
            continue
        prefix = norm.rstrip(os.path.sep) + os.path.sep
        if path == norm or path.startswith(prefix):
            return True
    return False


def _scan_dir_bounded(dir_path, *, max_entries=MAX_DIR_ENTRIES):
    entries = {}
    try:
        with os.scandir(dir_path) as scan:
            for index, entry in enumerate(scan, 1):
                if index > max_entries:
                    return None, 'too_many_entries'
                entries[entry.name] = entry
    except OSError:
        return None, 'unreadable'
    return entries, None


def _read_index_nofollow(index_path, *, max_bytes=MAX_INDEX_BYTES, max_lines=MAX_INDEX_LINES):
    flags = (
        os.O_RDONLY
        | getattr(os, 'O_NOFOLLOW', 0)
        | getattr(os, 'O_CLOEXEC', 0)
        | getattr(os, 'O_NONBLOCK', 0)
    )
    try:
        fd = os.open(index_path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return None, 'unsafe_path'
        if exc.errno == errno.ENOENT:
            return None, 'missing_index'
        return None, 'unreadable'
    buf = bytearray()
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, 'invalid_path'
        if st.st_size > max_bytes:
            return None, 'index_too_large'
        while len(buf) <= max_bytes:
            chunk = os.read(fd, min(65536, max_bytes + 1 - len(buf)))
            if not chunk:
                break
            buf.extend(chunk)
    except OSError:
        return None, 'unreadable'
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    if len(buf) > max_bytes:
        return None, 'index_too_large'
    try:
        text = bytes(buf).decode('utf-8')
    except UnicodeDecodeError:
        return None, 'unreadable'
    lines = text.splitlines()
    if len(lines) > max_lines:
        return None, 'index_too_large'
    return sum(1 for line in lines if _INDEX_ENTRY_RE.match(line)), None


def count_indexed_components(root, *, forbidden=(), forbidden_subtrees=()):
    """Bounded read-only inspection of COMPONENTS.md; opens no other file."""
    if root is None or (isinstance(root, str) and not root.strip()):
        return _status_result('not_set', 'No component library folder is configured.')
    if (
        not isinstance(root, str)
        or '\x00' in root
        or '\n' in root
        or '\r' in root
        or '\\' in root
        or len(root) > MAX_LIBRARY_ROOT_CHARS
        or not os.path.isabs(root)
    ):
        return _status_result('invalid_path', 'Configured library folder must be a normalized absolute directory path.')
    trimmed = root[:-1] if (len(root) > 1 and root.endswith(os.path.sep) and not root.endswith(os.path.sep * 2)) else root
    if os.path.normpath(trimmed) != trimmed:
        return _status_result('invalid_path', 'Configured library folder must be a normalized absolute directory path.')
    if _in_protected_tree(trimmed, forbidden=forbidden, forbidden_subtrees=forbidden_subtrees):
        return _status_result('unsafe_path', 'Configured library folder is in a protected location and was not inspected.')

    try:
        root_st = os.lstat(trimmed)
    except (FileNotFoundError, NotADirectoryError):
        return _status_result('missing', 'Configured library folder does not exist on disk.')
    except OSError:
        return _status_result('unreadable', 'Configured library folder could not be read.')

    if stat.S_ISLNK(root_st.st_mode):
        return _status_result('unsafe_path', 'Configured library folder is a symlink and was not followed.')
    if not stat.S_ISDIR(root_st.st_mode):
        return _status_result('invalid_path', 'Configured library path is not a directory.')

    try:
        real_root = os.path.realpath(trimmed)
        real_forbidden = tuple(os.path.realpath(p) for p in forbidden if isinstance(p, str) and p)
        real_subtrees = tuple(os.path.realpath(p) for p in forbidden_subtrees if isinstance(p, str) and p)
    except OSError:
        return _status_result('unreadable', 'Configured library folder could not be read.')
    if _in_protected_tree(real_root, forbidden=real_forbidden, forbidden_subtrees=real_subtrees):
        return _status_result('unsafe_path', 'Configured library folder is in a protected location and was not inspected.')

    top_entries, top_err = _scan_dir_bounded(trimmed)
    if top_err == 'too_many_entries':
        return _status_result('too_many_entries', f'Library folder exceeds the {MAX_DIR_ENTRIES}-entry inspection limit.')
    if top_err is not None or top_entries is None:
        return _status_result('unreadable', 'Configured library folder could not be read.')

    for cand in (COMPONENT_DIR, LEGACY_DIR):
        dir_entry = top_entries.get(cand)
        if dir_entry is None:
            continue
        try:
            if dir_entry.is_symlink():
                return _status_result('unsafe_path', 'Component folder inside the library is a symlink and was not followed.')
            if not dir_entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            return _status_result('unreadable', 'Component folder inside the library could not be read.')

        sub_entries, sub_err = _scan_dir_bounded(os.path.join(trimmed, cand))
        if sub_err == 'too_many_entries':
            return _status_result('too_many_entries', f'Component folder exceeds the {MAX_DIR_ENTRIES}-entry inspection limit.')
        if sub_err is not None or sub_entries is None:
            return _status_result('unreadable', 'Component folder inside the library could not be read.')

        idx_entry = sub_entries.get(INDEX_NAME)
        if idx_entry is None:
            continue
        index_rel = f'{cand}/{INDEX_NAME}'
        try:
            if idx_entry.is_symlink():
                return _status_result('unsafe_path', 'Component index is a symlink and was not followed.', index_rel=index_rel)
            idx_st = idx_entry.stat(follow_symlinks=False)
        except OSError:
            return _status_result('unreadable', 'Component index could not be read.', index_rel=index_rel)
        if not stat.S_ISREG(idx_st.st_mode):
            return _status_result('invalid_path', 'Component index is not a regular file.', index_rel=index_rel)
        if idx_st.st_size > MAX_INDEX_BYTES:
            return _status_result(
                'index_too_large',
                f'Component index exceeds the {MAX_INDEX_BYTES}-byte inspection limit.',
                index_rel=index_rel,
            )

        count, read_err = _read_index_nofollow(os.path.join(trimmed, cand, INDEX_NAME))
        if read_err == 'unsafe_path':
            return _status_result('unsafe_path', 'Component index is a symlink and was not followed.', index_rel=index_rel)
        if read_err == 'index_too_large':
            return _status_result('index_too_large', 'Component index exceeds the inspection size limit.', index_rel=index_rel)
        if read_err == 'invalid_path':
            return _status_result('invalid_path', 'Component index is not a regular file.', index_rel=index_rel)
        if read_err is not None or count is None:
            return _status_result('unreadable', 'Component index could not be read.', index_rel=index_rel)
        return _status_result('ready', 'Component library index is ready.', ok=True, count=count, index_rel=index_rel)

    return _status_result('missing_index', 'Library folder exists, but COMPONENTS.md was not found.')
