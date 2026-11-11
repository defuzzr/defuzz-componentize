"""Shared vendored kit engine loader and directory tilde expansion."""
import errno
import importlib.util
import os
import pwd
import re
import stat
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent

_ENV_REF = re.compile(r'\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)')
_CHILD_ENV_KEYS = ('PATH', 'HOME', 'HERMES_HOME', 'TMPDIR', 'TEMP', 'TMP')
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
_CREATED_STATES = ('created', 'appended', 'updated')


def _engine():
    spec = importlib.util.spec_from_file_location('_componentize_project_kit', ROOT / 'vendor/hermes_audit/project_kit.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENGINE = _engine()
LIBRARY_ROOT_KEY = 'plugins.entries.defuzz-componentize.settings.library_root'
_SYSTEM_SUBTREE_ROOTS = ('/etc', '/usr', '/bin', '/sbin', '/boot', '/dev', '/proc', '/sys')
_SYSTEM_EXACT_ROOTS = ('/var', '/opt')


def _is_same_or_inside(path, base):
    if base == os.path.sep:
        return True
    return path == base or path.startswith(base + os.path.sep)


def _is_lib_root(path):
    return os.path.dirname(path) == os.path.sep and os.path.basename(path).startswith('lib')


def _is_top_level(path):
    return os.path.dirname(path) == os.path.sep


def _expand_env(raw, env_map):
    """Expand $VAR / ${VAR} from env_map. Missing names are left for the caller to drop."""
    def repl(match):
        key = match.group(1) or match.group(2)
        if key not in env_map:
            raise KeyError(key)
        return env_map[key]
    return _ENV_REF.sub(repl, raw)


def _stable_hermes_home(raw, home, env_map):
    """An absolute Hermes home, or None.

    Hermes (`hermes_constants.hermes_root_for_environment`) returns None for a
    relative HERMES_HOME: the answer depends on the process cwd, which is not
    part of the environment. Do not abspath a relative value against the
    current folder. ``~`` and ``~/...`` expand against ``home``. An unresolvable
    ``~user`` stays non-absolute and is ignored. The caller always also protects
    ``<home>/.hermes``, including when this returns None.
    """
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        raw = _expand_env(raw, env_map)
    except KeyError:
        return None
    home_ok = isinstance(home, str) and bool(home) and os.path.isabs(home)
    if raw == '~' and home_ok:
        path = home
    elif raw.startswith('~/') and home_ok:
        path = os.path.join(home, raw[2:])
    elif raw.startswith('~'):
        path = os.path.expanduser(raw)
    elif os.path.isabs(raw):
        path = raw
    else:
        return None
    if not isinstance(path, str) or not os.path.isabs(path):
        return None
    return os.path.normpath(path)


def _hermes_homes(home, env):
    out = []
    if isinstance(home, str) and home and os.path.isabs(home):
        out.append(os.path.normpath(os.path.join(home, '.hermes')))
    env_map = os.environ if env is None else env
    if not hasattr(env_map, 'get'):
        return out
    norm_hh = _stable_hermes_home(env_map.get('HERMES_HOME', ''), home, env_map)
    if norm_hh and norm_hh not in out:
        out.append(norm_hh)
    return out


def resolve_home(home=None):
    """Absolute existing home folder, or a refusal.

    A caller-supplied absolute existing path wins. Otherwise ``HOME`` if that
    value is an absolute existing path, otherwise the account home from the
    password database. If neither is an absolute existing path, writing
    commands must refuse: an empty or relative ``HOME`` must not turn
    protection off, and the check must not run unprotected.
    """
    if isinstance(home, str) and os.path.isabs(home) and os.path.isdir(home):
        return os.path.normpath(home), None
    env_home = os.environ.get('HOME')
    if isinstance(env_home, str) and os.path.isabs(env_home) and os.path.isdir(env_home):
        return os.path.normpath(env_home), None
    pw_dir = None
    try:
        pw_dir = pwd.getpwuid(os.getuid()).pw_dir
    except (KeyError, OSError, AttributeError):
        pw_dir = None
    if isinstance(pw_dir, str) and os.path.isabs(pw_dir) and os.path.isdir(pw_dir):
        return os.path.normpath(pw_dir), None
    return None, 'cannot determine your home folder, so the safety check cannot run'


_SHOW_LIMIT = 240
_BIDI = frozenset(chr(code) for code in (0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069))


def show_text(value, limit=_SHOW_LIMIT, escape_backslash=True):
    """Render a path or name so controls and bidi marks are visible escapes.

    Control characters become ``\\x1b`` / ``\\n``. Bidi overrides and other
    non-printable code points become ``\\u202e`` style escapes. Very long
    values are truncated. ``limit=0`` does not truncate.
    """
    if not isinstance(value, str):
        value = str(value)
    out = []
    for ch in value:
        code = ord(ch)
        if ch == '\\' and escape_backslash:
            out.append('\\\\')
        elif ch == '\\':
            out.append(ch)
        elif ch == '\n':
            out.append('\\n')
        elif ch == '\r':
            out.append('\\r')
        elif ch == '\t':
            out.append('\\t')
        elif code < 32 or code == 127:
            out.append('\\x%02x' % code)
        elif ch in _BIDI or unicodedata.category(ch).startswith('C'):
            out.append(('\\u%04x' % code) if code <= 0xFFFF else ('\\U%08x' % code))
        else:
            out.append(ch)
    rendered = ''.join(out)
    if limit and len(rendered) > limit:
        return rendered[:limit] + '...'
    return rendered


def resolved_target(path):
    """realpath of the deepest existing ancestor, plus the not-yet-existing remainder.

    ``os.path.realpath`` does that and creates nothing. A final symlink that
    already exists is followed; callers that must not follow it check ``lstat``
    first.
    """
    return os.path.realpath(os.path.normpath(path))


def _is_symlink(path):
    try:
        return stat.S_ISLNK(os.lstat(path).st_mode)
    except OSError:
        return False


def _refuse(lexical, resolved, kind, entry=None):
    if kind == 'home':
        tail = 'pick a dedicated folder, not a home or filesystem root'
    elif kind == 'hermes':
        tail = f'pick a dedicated folder outside Hermes home ({show_text(entry)})'
    else:
        tail = 'pick a dedicated folder, not a system directory'
    shown = show_text(lexical)
    if resolved != lexical:
        return f'refusing {shown}: resolves to {show_text(resolved)}; {tail}'
    return f'refusing {shown}: {tail}'


def _home_or_ancestor(path, home_norm):
    if path == os.path.sep or path == home_norm:
        return True
    return home_norm.startswith(path + os.path.sep)


def _inside(path, base):
    return path == base or path.startswith(base + os.path.sep)


def _decide(lexical, resolved, home, env):
    """Refusal for one already-resolved target, or None. Creates nothing."""
    for norm_hh in _hermes_homes(home, env):
        try:
            real_hh = os.path.realpath(norm_hh)
        except OSError:
            real_hh = norm_hh
        if (
            _is_same_or_inside(lexical, norm_hh)
            or _is_same_or_inside(resolved, real_hh)
            or _is_same_or_inside(lexical, real_hh)
            or _is_same_or_inside(resolved, norm_hh)
        ):
            return _refuse(lexical, resolved, 'hermes', norm_hh)
    for sys_root in _SYSTEM_SUBTREE_ROOTS:
        try:
            real_sys = os.path.realpath(sys_root)
        except OSError:
            real_sys = sys_root
        if (
            _is_same_or_inside(lexical, sys_root)
            or _is_same_or_inside(resolved, sys_root)
            or (real_sys != os.path.sep and (
                _is_same_or_inside(resolved, real_sys) or _is_same_or_inside(lexical, real_sys)
            ))
        ):
            return _refuse(lexical, resolved, 'system')
    for exact_root in _SYSTEM_EXACT_ROOTS:
        try:
            real_exact = os.path.realpath(exact_root)
        except OSError:
            real_exact = exact_root
        if (
            lexical == exact_root
            or resolved == exact_root
            or lexical == real_exact
            or (real_exact != os.path.sep and resolved == real_exact)
        ):
            return _refuse(lexical, resolved, 'system')
    if _is_lib_root(lexical) or _is_lib_root(resolved):
        return _refuse(lexical, resolved, 'system')
    home_norm = os.path.normpath(home) if isinstance(home, str) and home and os.path.isabs(home) else None
    home_real = os.path.realpath(home_norm) if home_norm else None
    if lexical == os.path.sep or resolved == os.path.sep:
        return _refuse(lexical, resolved, 'home')
    if home_norm and (
        _home_or_ancestor(lexical, home_norm) or _home_or_ancestor(resolved, home_real)
    ):
        return _refuse(lexical, resolved, 'home')
    # An ancestor symlink can make a path that does not name the home resolve
    # into it. A folder the user actually named inside the home (for example
    # ~/component-library) stays allowed; the home directory itself does not.
    if home_real and _inside(resolved, home_real) and not (home_norm and _inside(lexical, home_norm)):
        return _refuse(lexical, resolved, 'home')
    if _is_top_level(lexical) or _is_top_level(resolved):
        return _refuse(lexical, resolved, 'system')
    return None


def check_safe_target(root, *, home=None, env=None):
    """Return a single-line refusal, or None.

    Decides on the lexical path and on the resolved path (realpath of the
    deepest existing ancestor, plus any missing suffix). Refuses if either form
    is the user's home directory itself or an ancestor of it, a symlink redirect
    into that home, inside or equal to a Hermes home, a system directory from
    the existing tables, the filesystem root, or a top-level directory.
    ``<home>/.hermes`` is always protected. A relative, empty, or unresolvable
    ``HERMES_HOME`` is not resolved against the current folder.
    """
    if not isinstance(root, str) or not os.path.isabs(root):
        return f'project folder must be an absolute normalized path, got {show_text(root)}'
    home, home_err = resolve_home(home)
    if home_err:
        return home_err
    lexical = os.path.normpath(root)
    try:
        resolved = resolved_target(lexical)
    except OSError as exc:
        detail = ' '.join(str(exc).splitlines()).strip()
        return f'{exc.__class__.__name__}: {detail}' if detail else exc.__class__.__name__
    except ValueError as exc:
        detail = ' '.join(str(exc).splitlines()).strip()
        return f'{exc.__class__.__name__}: {detail}' if detail else exc.__class__.__name__
    return _decide(lexical, resolved, home, env)


class VerifiedTarget:
    """One checked resolution of a user path. The caller closes ``fd``.

    ``path`` is the absolute path the check decided on. ``fd`` is an open
    directory of the deepest existing ancestor of that path. ``missing`` is the
    suffix that did not exist. Later steps must use this object and must not
    resolve the user's string again.
    """

    __slots__ = ('path', 'lexical', 'fd', 'missing', 'dev', 'ino')

    def __init__(self, path, lexical, fd, missing, dev, ino):
        self.path = path
        self.lexical = lexical
        self.fd = fd
        self.missing = tuple(missing)
        self.dev = dev
        self.ino = ino

    def close(self):
        _close(self.fd)
        self.fd = None


def _open_existing_prefix(resolved):
    """Open the deepest existing directory of an already-resolved absolute path.

    Walks from ``/`` with ``O_DIRECTORY|O_NOFOLLOW`` and creates nothing.
    Returns ``(fd, missing, None)`` or ``(None, (), error)``.
    """
    parts = [part for part in resolved.split(os.path.sep) if part]
    fd = None
    try:
        fd = os.open(os.path.sep, _DIR_FLAGS)
        for index, name in enumerate(parts):
            try:
                info = os.lstat(name, dir_fd=fd)
            except FileNotFoundError:
                return fd, tuple(parts[index:]), None
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                _close(fd)
                return None, (), f'refusing {show_text(resolved)}: a directory component is a symlink'
            try:
                next_fd = os.open(name, _DIR_FLAGS, dir_fd=fd)
            except OSError as exc:
                _close(fd)
                if exc.errno == errno.ELOOP:
                    return None, (), f'refusing {show_text(resolved)}: a directory component is a symlink'
                if exc.errno in _SWAP_ERRNOS:
                    return None, (), _changed_line(resolved)
                return None, (), _os_line(resolved, exc)
            _close(fd)
            fd = next_fd
        return fd, (), None
    except OSError as exc:
        _close(fd)
        return None, (), _os_line(resolved, exc)


def _prefix_path(resolved, missing):
    prefix = resolved
    for _name in missing:
        prefix = os.path.dirname(prefix)
    return prefix or os.path.sep


def verify_target(root, *, home=None, env=None):
    """Resolve ``root`` once, check it, and hold the ancestor directory open.

    Returns ``(VerifiedTarget, None)`` or ``(None, error)``. Creates nothing.
    On error no file descriptor is left open.
    """
    if not isinstance(root, str) or not os.path.isabs(root):
        return None, f'project folder must be an absolute normalized path, got {show_text(root)}'
    home, home_err = resolve_home(home)
    if home_err:
        return None, home_err
    lexical = os.path.normpath(root)
    final_link = _is_symlink(lexical)
    try:
        resolved = resolved_target(lexical)
    except OSError as exc:
        return None, _os_line(lexical, exc)
    except ValueError as exc:
        detail = ' '.join(str(exc).splitlines()).strip()
        return None, (f'{exc.__class__.__name__}: {detail}' if detail else exc.__class__.__name__)
    refusal = _decide(lexical, resolved, home, env)
    if refusal:
        return None, refusal
    if final_link:
        return None, f'{show_text(lexical)} exists and is a symlink, not a directory'
    fd, missing, open_err = _open_existing_prefix(resolved)
    if open_err or fd is None:
        return None, open_err or f'refusing {show_text(lexical)}: a directory component is a symlink'
    prefix = _prefix_path(resolved, missing)
    try:
        held = os.fstat(fd)
    except OSError as exc:
        _close(fd)
        return None, _os_line(lexical, exc)
    try:
        on_path = os.stat(prefix)
    except OSError:
        # The held folder no longer answers at its path: a swap.
        _close(fd)
        return None, _changed_line(lexical)
    if (held.st_dev, held.st_ino) != (on_path.st_dev, on_path.st_ino):
        _close(fd)
        return None, _changed_line(lexical)
    return VerifiedTarget(resolved, lexical, fd, missing, held.st_dev, held.st_ino), None


def _os_line(path, exc):
    detail = show_text(' '.join(str(exc).splitlines()).strip())
    shown = show_text(path)
    if shown not in detail:
        detail = f'{detail} ({shown})' if detail else shown
    return f'{exc.__class__.__name__}: {detail}'


def _close(fd):
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass


def before_secure_create(path):
    """Test seam between the safety check and directory creation. Default no-op."""
    return None


def ensure_real_directory(path):
    """Create ``path`` without following symlinks.

    Walks from ``/`` and creates each missing segment with ``mkdir`` relative to
    the parent directory fd, then opens it with ``O_DIRECTORY|O_NOFOLLOW``.
    Returns ``(fd, None)`` or ``(None, error)``. The caller closes ``fd``.
    Nothing is created when a segment is a symlink.
    """
    before_secure_create(path)
    path = os.path.normpath(path)
    if not os.path.isabs(path) or path == os.path.sep:
        return None, f'refusing {show_text(path)}: pick a dedicated folder, not a home or filesystem root'
    fd = None
    try:
        fd = os.open(os.path.sep, _DIR_FLAGS)
        for name in path.split(os.path.sep)[1:]:
            if name in ('', '.', '..'):
                _close(fd)
                return None, f'refusing {show_text(path)}: a directory component is a symlink'
            try:
                info = os.lstat(name, dir_fd=fd)
            except FileNotFoundError:
                info = None
            except OSError as exc:
                _close(fd)
                return None, _os_line(path, exc)
            if info is not None and stat.S_ISLNK(info.st_mode):
                _close(fd)
                return None, f'refusing {show_text(path)}: a directory component is a symlink'
            if info is not None and not stat.S_ISDIR(info.st_mode):
                _close(fd)
                return None, f'{show_text(path)} exists and is not a directory'
            if info is None:
                try:
                    os.mkdir(name, dir_fd=fd)
                except FileExistsError:
                    try:
                        info = os.lstat(name, dir_fd=fd)
                    except OSError as exc:
                        _close(fd)
                        return None, _os_line(path, exc)
                    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                        _close(fd)
                        return None, f'refusing {show_text(path)}: a directory component is a symlink'
                except OSError as exc:
                    _close(fd)
                    return None, _os_line(path, exc)
            try:
                next_fd = os.open(name, _DIR_FLAGS, dir_fd=fd)
            except OSError as exc:
                _close(fd)
                if exc.errno == errno.ELOOP:
                    return None, f'refusing {show_text(path)}: a directory component is a symlink'
                return None, _os_line(path, exc)
            _close(fd)
            fd = next_fd
        return fd, None
    except OSError as exc:
        _close(fd)
        return None, _os_line(path, exc)


_SWAP_ERRNOS = (errno.ENOENT, errno.ENOTDIR, errno.ELOOP)


def _changed_line(path):
    return f'refusing {show_text(path)}: the folder changed while it was being checked'


def _rename_flags_call():
    """A no-replace rename between two names in one folder, or None when unavailable.

    Linux has ``renameat2(..., RENAME_NOREPLACE)`` and macOS has
    ``renameatx_np(..., RENAME_EXCL)``. Python's ``os.rename`` replaces an empty
    folder at the new name, which would remove a folder this call did not make.
    """
    try:
        import ctypes
        import ctypes.util
        libc = ctypes.CDLL(ctypes.util.find_library('c') or None, use_errno=True)
    except (ImportError, OSError, TypeError):
        return None
    for name, flag in (('renameat2', 1), ('renameatx_np', 4)):
        fn = getattr(libc, name, None)
        if fn is None:
            continue
        fn.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        fn.restype = ctypes.c_int

        def call(dir_fd, src, dst, _fn=fn, _flag=flag):
            if _fn(dir_fd, os.fsencode(src), dir_fd, os.fsencode(dst), _flag) != 0:
                err = ctypes.get_errno()
                raise OSError(err, os.strerror(err))
        return call
    return None


_RENAME_NOREPLACE = _rename_flags_call()


def _rename_noreplace(dir_fd, src, dst):
    """Rename ``src`` to ``dst`` inside ``dir_fd``; fail when ``dst`` exists."""
    if _RENAME_NOREPLACE is not None:
        try:
            return _RENAME_NOREPLACE(dir_fd, src, dst)
        except OSError as exc:
            if exc.errno not in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
                raise
    # Fallback: check, then rename. A folder made at ``dst`` in between could
    # be replaced; this path is used only where the system has no such call.
    try:
        os.lstat(dst, dir_fd=dir_fd)
    except FileNotFoundError:
        os.rename(src, dst, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        return None
    raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST))


def _private_name():
    return '.componentize-' + os.urandom(12).hex()


def _still_exists(parent_fd, name, own_fd, dev, ino):
    """True while the folder behind ``own_fd`` is still in the folder it was made in.

    The link count alone cannot tell: Linux reports 0 for a removed folder that is
    still held open, macOS (APFS) keeps reporting 2. So the folder counts as still
    there only while ``parent_fd`` has an entry, looked up without following
    links, with the same device and inode: first at ``name``, then under any
    other name in that folder (a folder renamed in place is still there). A
    removed folder, one recreated under the same name, and one moved out of that
    folder are gone. A link count of 0 is always gone. When the answer cannot be
    read, the folder is counted as still there.
    """
    def here(entry):
        try:
            info = os.stat(entry, dir_fd=parent_fd, follow_symlinks=False)
        except (FileNotFoundError, NotADirectoryError):
            return False
        return _same(info, dev, ino)

    try:
        if os.fstat(own_fd).st_nlink == 0:
            return False
        if here(name):
            return True
        return any(here(entry) for entry in os.listdir(parent_fd) if entry != name)
    except OSError:
        return True


def _same(info, dev, ino):
    return info is not None and (info.st_dev, info.st_ino) == (dev, ino) and stat.S_ISDIR(info.st_mode)


def _lstat_or_none(name, dir_fd):
    try:
        return os.lstat(name, dir_fd=dir_fd)
    except OSError:
        return None


def _remove_one(parent_fd, name, own_fd, dev, ino):
    """Remove this call's folder if it is still empty. True when it no longer exists.

    The folder is known by ``own_fd``, the handle opened when this call made
    it. It is first moved to a fresh private name, checked there, and only then
    removed, so a folder another program puts at ``name`` meanwhile is never
    the one removed. A folder that is not this call's is put back (never over
    an existing entry) and left alone.
    """
    try:
        if not _still_exists(parent_fd, name, own_fd, dev, ino):
            return True
        if os.listdir(own_fd):
            return False
        if not _same(_lstat_or_none(name, parent_fd), dev, ino):
            return not _still_exists(parent_fd, name, own_fd, dev, ino)
        private = _private_name()
        _rename_noreplace(parent_fd, name, private)
        if not _same(_lstat_or_none(private, parent_fd), dev, ino):
            try:
                _rename_noreplace(parent_fd, private, name)
            except OSError:
                pass
            return not _still_exists(parent_fd, name, own_fd, dev, ino)
        try:
            os.rmdir(private, dir_fd=parent_fd)
        except OSError:
            try:
                _rename_noreplace(parent_fd, private, name)
            except OSError:
                pass
    except OSError:
        pass
    return not _still_exists(parent_fd, name, own_fd, dev, ino)


def _remove_created(created):
    """Best-effort removal of folders this call created, deepest first.

    ``created`` holds ``[parent_fd, name, own_fd, dev, ino]`` entries, where
    ``own_fd`` is the handle opened on the folder right after this call made it
    under a fresh private name. Removal never goes by path, is never recursive,
    and only takes an empty folder that is still the one behind ``own_fd``.
    Returns how many of this call's folders still exist afterwards. Closes
    every descriptor.

    Limit: another program running as the same user that learns a private
    name and swaps a folder in at that exact moment can still make one removal
    hit its empty folder; the count then still reports this call's folder as
    left while it is in the folder it was made in, because it is looked up there
    by the device and inode of ``own_fd``, not by name.
    """
    left = 0
    for parent_fd, name, own_fd, dev, ino in reversed(created):
        if not _remove_one(parent_fd, name, own_fd, dev, ino):
            left += 1
        _close(own_fd)
        _close(parent_fd)
    return left


def _with_left(message, left):
    if not left:
        return message
    noun = 'folder it created was' if left == 1 else 'folders it created were'
    return f'{message}; {left} {noun} left in place because they were not empty or could not be removed'


def create_from_verified(verified):
    """Create missing segments from the verified ancestor fd.

    Walks with ``mkdir`` and ``open(O_DIRECTORY|O_NOFOLLOW)`` relative to that
    fd, so a swapped path string cannot redirect creation. Each missing folder
    is made under a fresh private name, opened there, and then renamed to its
    real name without replacing anything; the walk continues through the handle
    opened at creation. After creation, ``fstat`` of the held directory must
    match ``os.stat`` of the verified path. Returns ``(final_fd, None)`` or
    ``(None, error)``. Does not close ``verified.fd``. On error no descriptor is
    returned, and the empty folders this call created are removed (best effort;
    the message says how many of them still exist).
    """
    before_secure_create(verified.path)
    fd = None
    created = []  # [parent_fd, name, own_fd, dev, ino] for each folder this call made
    unopened = {'n': 0}  # folders this call made but could not open

    def fail(message):
        _close(fd)
        return None, _with_left(message, _remove_created(created) + unopened['n'])

    try:
        fd = os.dup(verified.fd)
        for name in verified.missing:
            if name in ('', '.', '..'):
                return fail(f'refusing {show_text(verified.path)}: a directory component is a symlink')
            try:
                info = os.lstat(name, dir_fd=fd)
            except FileNotFoundError:
                info = None
            except OSError as exc:
                return fail(_changed_line(verified.path) if exc.errno in _SWAP_ERRNOS else _os_line(verified.path, exc))
            if info is not None and stat.S_ISLNK(info.st_mode):
                return fail(f'refusing {show_text(verified.path)}: a directory component is a symlink')
            if info is not None and not stat.S_ISDIR(info.st_mode):
                return fail(f'{show_text(verified.path)} exists and is not a directory')
            if info is not None:
                try:
                    next_fd = os.open(name, _DIR_FLAGS, dir_fd=fd)
                except OSError as exc:
                    if exc.errno == errno.ELOOP:
                        return fail(f'refusing {show_text(verified.path)}: a directory component is a symlink')
                    return fail(_changed_line(verified.path) if exc.errno in _SWAP_ERRNOS else _os_line(verified.path, exc))
                _close(fd)
                fd = next_fd
                continue
            private = _private_name()
            try:
                os.mkdir(private, dir_fd=fd)
            except OSError as exc:
                return fail(_changed_line(verified.path) if exc.errno in _SWAP_ERRNOS + (errno.EEXIST,) else _os_line(verified.path, exc))
            try:
                own = os.open(private, _DIR_FLAGS, dir_fd=fd)
            except OSError:
                unopened['n'] += 1
                return fail(_changed_line(verified.path))
            mine = os.fstat(own)
            entry = [fd, private, own, mine.st_dev, mine.st_ino]
            created.append(entry)
            parent, fd = fd, None  # the entry owns the parent fd now
            try:
                _rename_noreplace(parent, private, name)
            except OSError as exc:
                if exc.errno in _SWAP_ERRNOS + (errno.EEXIST, errno.ENOTEMPTY):
                    return fail(_changed_line(verified.path))
                return fail(_os_line(verified.path, exc))
            entry[1] = name
            fd = os.dup(own)
        held = os.fstat(fd)
        try:
            on_path = os.stat(verified.path)
        except OSError:
            return fail(_changed_line(verified.path))
        if (held.st_dev, held.st_ino) != (on_path.st_dev, on_path.st_ino):
            return fail(_changed_line(verified.path))
        for parent_fd, _name, own_fd, _dev, _ino in created:
            _close(own_fd)
            _close(parent_fd)
        return fd, None
    except OSError as exc:
        return fail(_os_line(verified.path, exc))


def _opens_from_root(root_fd, rel):
    """True when ``rel`` can be opened from ``root_fd`` without following symlinks."""
    parts = rel.split('/')
    if not parts or any(part in ('', '.', '..') for part in parts):
        return False
    fd = None
    try:
        fd = os.dup(root_fd)
        for name in parts[:-1]:
            info = os.lstat(name, dir_fd=fd)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                return False
            next_fd = os.open(name, _DIR_FLAGS, dir_fd=fd)
            _close(fd)
            fd = next_fd
        info = os.lstat(parts[-1], dir_fd=fd)
        return not stat.S_ISLNK(info.st_mode)
    except OSError:
        return False
    finally:
        _close(fd)


def containment_report(root_fd, root_dev, root_ino, root_path, result):
    """Report engine writes that cannot be opened from the verified directory.

    Walks each path the engine reported as written, from ``root_fd``, and never
    by path string. Lists only those relative paths. Does not list files this
    check did not open, including files a swap may have left elsewhere.
    """
    reported = []
    for item in (result or {}).get('items') or []:
        rel = item.get('path') if isinstance(item, dict) else None
        if isinstance(rel, str) and item.get('state') in _CREATED_STATES:
            reported.append(rel)
    if not reported:
        return None
    # The caller took (root_dev, root_ino) from the held handle. Compare it with
    # what the path names now, without following a final symlink.
    try:
        now = os.stat(root_path, follow_symlinks=False)
        moved = (now.st_dev, now.st_ino) != (root_dev, root_ino)
    except OSError:
        moved = True
    unconfirmed = [rel for rel in reported if not _opens_from_root(root_fd, rel)]
    checked = (
        f'checked {len(reported)} path(s) the engine reported as written, by walking from '
        f'the verified directory (device {root_dev}, inode {root_ino})'
    )
    if unconfirmed:
        shown = ', '.join(show_text(rel) for rel in unconfirmed)
        checked += f'; {len(unconfirmed)} could not be opened there: {shown}'
    if moved:
        return (
            f'kit write not confirmed inside {show_text(root_path)}: the verified directory '
            f'identity changed (was device {root_dev}, inode {root_ino}); {checked}. '
            f'This check does not list files it did not open.'
        )
    if not unconfirmed:
        return None
    return (
        f'kit write not confirmed inside {show_text(root_path)}: {checked}. '
        f'This check does not list files it did not open.'
    )


def reduced_child_env(env):
    """Environment for the outer bridge child: no parent secrets.

    Keeps ``PATH``, ``HOME``, ``HERMES_HOME``, and the temp-dir variables. Drops
    everything else. Resolve HERMES_HOME against the full parent environment
    first, so dropping a referenced variable cannot weaken state protection.
    Empty values are omitted so they do not override the child's default.
    """
    src = os.environ if env is None else env
    kept = {}
    if not hasattr(src, 'get'):
        return kept
    raw = src.get('HERMES_HOME')
    if isinstance(raw, str) and raw.strip():
        try:
            expanded = _expand_env(raw.strip(), src)
            if expanded == '~' or expanded.startswith('~/'):
                home = src.get('HOME')
                if not isinstance(home, str) or not os.path.isabs(home):
                    home = pwd.getpwuid(os.getuid()).pw_dir
                if not home or not os.path.isabs(home):
                    raise ValueError('home is not absolute')
                expanded = home + expanded[1:]
            elif expanded.startswith('~'):
                user, sep, rest = expanded[1:].partition('/')
                expanded = os.path.join(pwd.getpwnam(user).pw_dir, rest) if sep else pwd.getpwnam(user).pw_dir
            # The child expands again: unresolved/nested references must not
            # become a different protected path in its reduced environment.
            if not expanded or expanded.startswith('~') or _ENV_REF.search(expanded) or '\x00' in expanded:
                raise ValueError('unresolved home')
            resolved = os.path.abspath(expanded)
            if not os.path.isabs(resolved):
                raise ValueError('home is not absolute')
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise ValueError('cannot resolve HERMES_HOME to an absolute path') from exc
        kept['HERMES_HOME'] = resolved
    for key in _CHILD_ENV_KEYS:
        if key == 'HERMES_HOME':
            continue
        value = src.get(key)
        if isinstance(value, str) and value.strip():
            kept[key] = value
    return kept


def expand_dir(raw_dir, *, cwd=None, home, setting=LIBRARY_ROOT_KEY, typed=False):
    """Expand ~ and ~/... against home; reject ~user paths.

    A relative path is accepted only when the user typed it on the command line
    (typed=True): it is then relative to the folder they ran the command in. A
    relative path that comes from saved settings is refused, because it would
    mean a different folder depending on where the command happens to run.
    """
    if not isinstance(raw_dir, str):
        return None, f'{setting} must be an absolute path or ~/...'
    if raw_dir.startswith('~'):
        if raw_dir == '~':
            if not home or not os.path.isabs(home):
                return None, 'cannot expand ~ without home'
            expanded = home
        elif raw_dir.startswith('~/'):
            if not home or not os.path.isabs(home):
                return None, 'cannot expand ~ without home'
            expanded = os.path.join(home, raw_dir[2:])
        else:
            return None, f'unsupported ~user path {show_text(raw_dir)}; use ~ or ~/...'
    elif os.path.isabs(raw_dir):
        expanded = raw_dir
    elif typed and cwd and os.path.isabs(cwd):
        expanded = os.path.join(cwd, raw_dir)
    else:
        return None, f'relative path {show_text(raw_dir)} is not allowed for {setting}; use an absolute path or ~/...'
    return os.path.normpath(expanded), None


def exit_code(status):
    return {'pass': 0, 'fail': 1, 'error': 2}[status]


def format_report(title, result):
    lines = [f"{show_text(title, limit=0)}: {result['status'].upper()}"]
    lines += [f"  {item['state']:<10} {show_text(item['path'])}" for item in result.get('items', [])]
    if result['error']:
        lines.append(f"error: {show_text(result['error'], limit=0)}")
    lines += [f"{show_text(f['id'])} [{show_text(f['severity'])}] {show_text(f['detail'], limit=0)} — fix: {show_text(f['fix'], limit=0)}" for f in result['findings']]
    if result.get('saved'):
        lines.append('saved as the default component library for this Hermes profile')
    return '\n'.join(lines) + '\n'
