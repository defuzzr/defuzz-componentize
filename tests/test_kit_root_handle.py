"""Kit apply and library setup hand the engine the folder handle they verified.

Caller-level tests: `kit apply` goes through kit_commands.run and library setup
through init_library. Every folder lives under the per-test temp root the shared
conftest pins. A folder that is swapped after the caller's check must not
redirect any write: the files land in the folder the caller verified (proved by
device and inode, even after the path names another folder), or the run
refuses with one plain line. Nothing is ever written in the outside folder.
"""
import ctypes
import ctypes.util
import errno
import hashlib
import importlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NAME = 'componentize_plugin_root_handle'
KIT = 'component-library'
RUNS = 300
FD_RUNS = 500


def _load():
    spec = importlib.util.spec_from_file_location(NAME, ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
    assert spec is not None and spec.loader is not None
    for key in [k for k in sys.modules if k == NAME or k.startswith(NAME + '.')]:
        del sys.modules[key]
    module = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = module
    spec.loader.exec_module(module)
    runtime = importlib.import_module(NAME + '._kit_runtime')
    kit = importlib.import_module(NAME + '.kit_commands')
    lib = importlib.import_module(NAME + '.library_setup')
    return runtime, kit, lib


def _fd_dir():
    for path in ('/proc/self/fd', '/dev/fd'):
        if os.path.isdir(path):
            return path
    return None


def _open_fds():
    return len(os.listdir(_fd_dir()))


def _manifest(root):
    """Every entry under root: kind, link target or content hash. Never follows links."""
    rows = []
    root = str(root)
    for dirpath, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted(dirs + files):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            if os.path.islink(path):
                rows.append((rel, 'link', os.readlink(path)))
            elif os.path.isdir(path):
                rows.append((rel, 'dir', None))
            else:
                rows.append((rel, 'file', hashlib.sha256(Path(path).read_bytes()).hexdigest()))
    return rows


def _identity(path):
    info = os.stat(path, follow_symlinks=False)
    return info.st_dev, info.st_ino


def _one_line(text):
    return isinstance(text, str) and text.strip() != '' and '\n' not in text and 'Traceback' not in text


def _exchange_supported(probe_dir):
    """True when this system can swap two names atomically (Linux renameat2, macOS renameatx_np)."""
    a, b = os.path.join(probe_dir, '.xa'), os.path.join(probe_dir, '.xb')
    os.mkdir(a)
    os.symlink('nowhere', b)
    try:
        libc = ctypes.CDLL(ctypes.util.find_library('c') or None, use_errno=True)
        for name in ('renameat2', 'renameatx_np'):
            fn = getattr(libc, name, None)
            if fn is None:
                continue
            fn.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
            AT_FDCWD = -100 if sys.platform.startswith('linux') else -2
            if fn(AT_FDCWD, a.encode(), AT_FDCWD, b.encode(), 2) == 0:
                return True
        return False
    except (OSError, AttributeError, TypeError):
        return False
    finally:
        for path in (a, b):
            if os.path.islink(path):
                os.unlink(path)
            elif os.path.isdir(path):
                os.rmdir(path)


# Swaps a real folder and a link to the outside folder, atomically, until the stop
# file appears; a reader always sees one or the other, never a gap.
_FLIPPER = textwrap.dedent('''
    import ctypes, ctypes.util, os, sys
    real, link, stop = (p.encode() for p in sys.argv[1:4])
    libc = ctypes.CDLL(ctypes.util.find_library('c') or None, use_errno=True)
    fn = getattr(libc, 'renameat2', None) or getattr(libc, 'renameatx_np')
    fn.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    AT = -100 if sys.platform.startswith('linux') else -2
    n = 0
    sys.stdout.write('ready\\n'); sys.stdout.flush()
    while not os.path.exists(stop):
        if fn(AT, real, AT, link, 2) == 0:
            n += 1
    if n % 2:
        fn(AT, real, AT, link, 2)
        n += 1
    sys.stdout.write(str(n) + '\\n')
''')


class _Flipper:
    def __init__(self, folder, outside, scratch):
        self.real = os.path.join(folder, 'components')
        self.link = os.path.join(folder, '.flip-link')
        os.symlink(str(outside), self.link)
        self.stop = os.path.join(scratch, 'stop-' + os.urandom(4).hex())
        self.proc = subprocess.Popen([sys.executable, '-c', _FLIPPER, self.real, self.link, self.stop],
                                     stdout=subprocess.PIPE, text=True)
        assert self.proc.stdout.readline().strip() == 'ready'

    def finish(self):
        Path(self.stop).touch()
        out, _ = self.proc.communicate(timeout=60)
        assert self.proc.returncode == 0
        flips = int(out.strip().splitlines()[-1])
        assert flips % 2 == 0
        assert os.path.isdir(self.real) and not os.path.islink(self.real)
        assert os.path.islink(self.link)
        os.unlink(self.link)
        return flips


@pytest.fixture
def rig(tmp_path):
    runtime, kit, lib = _load()
    base = tmp_path.resolve()
    home = Path(os.environ['HOME'])
    outside = base / 'outside'
    outside.mkdir()
    (outside / 'victim.txt').write_bytes(b'outside folder: must never change\n')
    (outside / 'tests').mkdir()
    (outside / 'tests' / 'keep.txt').write_bytes(b'still here\n')
    return {'runtime': runtime, 'kit': kit, 'lib': lib, 'engine': runtime.ENGINE,
            'base': base, 'home': home, 'outside': outside}


def _kit_apply(rig, target):
    res = rig['kit'].run(['apply', KIT, str(target), '--json'], cwd=str(rig['base']), home=str(rig['home']))
    return res, json.loads(res['stdout'])


def _lib_init(rig, target):
    return rig['lib'].init_library(str(target), forbidden=(str(rig['home']),))


def _record_engine(monkeypatch, rig, before=None, fail=None):
    """Wrap ENGINE.apply_kit: record the handle the caller passes, optionally swap first or fail."""
    engine = rig['engine']
    original = engine.apply_kit
    seen = []

    def wrapper(kit, root, **kwargs):
        fd = kwargs.get('root_fd')
        seen.append({'root': root, 'fd': fd, 'id': None if fd is None else (os.fstat(fd).st_dev, os.fstat(fd).st_ino)})
        if before is not None:
            before()
        if fail is not None:
            raise fail
        return original(kit, root, **kwargs)

    monkeypatch.setattr(engine, 'apply_kit', wrapper)
    return seen


def _swap_parent(parent, *, to):
    """Move parent aside and put `to` in its place: a real impostor folder or a link to outside."""
    aside = Path(str(parent) + '-verified')

    def swap():
        os.rename(parent, aside)
        if isinstance(to, Path):
            os.symlink(to, parent)
        else:
            (parent / 'proj').mkdir(parents=True)
    return aside, swap


@pytest.mark.parametrize('caller', ['kit-apply', 'library-setup'])
@pytest.mark.parametrize('replacement', ['impostor-folder', 'link-to-outside'])
def test_parent_swapped_after_verification_writes_only_into_the_verified_folder(rig, monkeypatch, caller, replacement):
    parent = rig['base'] / 'parent'
    target = parent / 'proj'
    target.mkdir(parents=True)
    verified_id = _identity(target)
    outside_before = _manifest(rig['outside'])
    aside, swap = _swap_parent(parent, to=rig['outside'] if replacement == 'link-to-outside' else 'impostor')
    seen = _record_engine(monkeypatch, rig, before=swap)

    if caller == 'kit-apply':
        res, data = _kit_apply(rig, target)
        error, code = data.get('error'), res['exit']
    else:
        data = _lib_init(rig, target)
        error, code = data.get('error'), (0 if data.get('ok') else 2)

    assert len(seen) == 1
    # Nothing went where the path points now.
    assert _manifest(rig['outside']) == outside_before
    if replacement == 'impostor-folder':
        assert _manifest(parent) == [('proj', 'dir', None)]
    # Proof by identity: the files are in the folder that was checked, now at `aside`.
    assert _identity(aside / 'proj') == verified_id
    assert (aside / 'proj' / 'AGENTS.md').is_file()
    assert (aside / 'proj' / 'components' / 'COMPONENTS.md').is_file()
    # The caller passed the handle it verified, not a path it re-opened later.
    assert seen[0]['fd'] is not None, 'the caller did not pass root_fd'
    assert seen[0]['id'] == verified_id
    # The path no longer names the checked folder, so the run refuses with one line.
    assert code == 2
    assert _one_line(error), error
    assert 'not confirmed' in error and 'identity changed' in error


@pytest.mark.parametrize('caller', ['kit-apply', 'library-setup'])
def test_handle_is_the_verified_folder_and_is_closed_on_every_path(rig, monkeypatch, caller):
    if _fd_dir() is None:
        pytest.skip('no descriptor listing on this system')
    target = rig['base'] / 'fd-proj'
    first = _kit_apply(rig, target)[0] if caller == 'kit-apply' else _lib_init(rig, target)
    if caller == 'kit-apply':
        assert first['exit'] == 0, first
    else:
        assert first['ok'], first
    verified_id = _identity(target)
    real_apply = rig['engine'].apply_kit
    before = _open_fds()
    outcomes = {'pass': 0, 'oserror': 0, 'raised': 0}
    for n in range(FD_RUNS):
        mode = ('pass', 'oserror', 'raised')[n % 3]
        fail = {'pass': None, 'oserror': OSError(errno.EIO, 'disk said no'), 'raised': RuntimeError('boom')}[mode]
        seen = _record_engine(monkeypatch, rig, fail=fail)
        try:
            if caller == 'kit-apply':
                res, data = _kit_apply(rig, target)
                if mode == 'pass':
                    assert res['exit'] == 0, res
                elif mode == 'oserror':
                    assert res['exit'] == 2 and _one_line(data['error']), res
                else:
                    pytest.fail('a RuntimeError from the engine was swallowed')
            else:
                data = _lib_init(rig, target)
                if mode == 'pass':
                    assert data['ok'], data
                else:
                    pytest.fail('an engine error was swallowed by library setup')
        except (OSError, RuntimeError) as exc:
            assert mode != 'pass' and exc is fail
        outcomes[mode] += 1
        assert seen and seen[0]['fd'] is not None and seen[0]['id'] == verified_id
        monkeypatch.setattr(rig['engine'], 'apply_kit', real_apply)
    after = _open_fds()
    assert sum(outcomes.values()) == FD_RUNS
    assert after == before, (before, after)


def _empty_by_handle(fd):
    """Remove everything inside the folder behind fd, by handle only (the flipper renames it)."""
    for name in os.listdir(fd):
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            sub = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                _empty_by_handle(sub)
            finally:
                os.close(sub)
            os.rmdir(name, dir_fd=fd)
        else:
            os.unlink(name, dir_fd=fd)


@pytest.mark.parametrize('mode', ['re-apply', 'create-each-run'])
@pytest.mark.parametrize('caller', ['kit-apply', 'library-setup'])
def test_swap_loop_inside_target_never_writes_outside(rig, caller, mode):
    """A folder inside the target flips between a real folder and a link to outside.

    In 're-apply' every file already exists; in 'create-each-run' the real folder is
    emptied (by handle) before each run, so each run must create its files again.
    """
    base = rig['base']
    if not _exchange_supported(str(base)):
        pytest.skip('no atomic name swap on this system')
    target = base / 'loop-proj'

    def apply():
        if caller == 'kit-apply':
            res, data = _kit_apply(rig, target)
            return res['exit'], data
        data = _lib_init(rig, target)
        return (0 if data['ok'] and data['status'] == 'pass' else 1), data

    code, data = apply()
    assert code == 0, data
    verified_id = _identity(target)
    outside_before = _manifest(rig['outside'])
    inside_before = _manifest(target)
    real_fd = os.open(target / 'components', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    flipper = _Flipper(str(target), rig['outside'], str(base))
    results = {'pass': 0, 'refused': 0}
    try:
        for _ in range(RUNS):
            if mode == 'create-each-run':
                _empty_by_handle(real_fd)
            code, data = apply()
            states = {item['state'] for item in data.get('items') or []}
            if code == 0:
                assert data['status'] == 'pass' and data['error'] is None, data
                if mode == 're-apply':
                    assert states <= {'kept'}, data
                else:
                    # Completed inside the verified folder: the files are behind the real handle.
                    assert 'created' in states, data
                    os.stat('COMPONENTS.md', dir_fd=real_fd, follow_symlinks=False)
                    os.stat('tests', dir_fd=real_fd, follow_symlinks=False)
                results['pass'] += 1
                continue
            results['refused'] += 1
            assert data['status'] in ('fail', 'error'), data
            if data.get('error'):
                assert _one_line(data['error']), data['error']
            for finding in data.get('findings') or []:
                assert _one_line(finding['detail']), finding
            conflicts = [f for f in data.get('findings') or [] if f['id'] == 'kit-conflict']
            assert data.get('error') or conflicts, data
            # Each refusal names a path at or below the flipped folder.
            assert all(f['detail'].startswith('components') for f in conflicts), conflicts
            if mode == 're-apply':
                assert not states & {'created', 'updated', 'appended'}, data
            assert _manifest(rig['outside']) == outside_before
    finally:
        flips = flipper.finish()
        os.close(real_fd)
    assert flips > 0
    assert results['refused'] > 0, (results, flips)
    assert _manifest(rig['outside']) == outside_before
    assert _identity(target) == verified_id
    if mode == 're-apply':
        assert _manifest(target) == inside_before
    code, data = apply()
    assert code == 0, data
    assert _manifest(target) == inside_before
