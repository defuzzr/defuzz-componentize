"""Path-safety matrix for kit apply/check and library init.

Safety decisions use both the lexical path and the resolved path. Every case
runs under a throwaway root inside the temp folder the shared conftest fixture
pins for the test. One audit hook is installed once per process, but it is
inert unless a guard is active: setUp activates it for that test's root and
cleanup clears it, so other test modules are never affected. While active, a
write whose resolved path leaves the allowed set raises PermissionError. System-folder cases call the checker with path
strings only; those strings are never created.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = 'componentize_plugin_path_safety'

# Allowed write prefixes while a guard is active; empty means the hook is inert.
_ALLOW = []
_HOOKED = False
_WRITES = []


def _resolved(path):
    """Resolved location of a write path, without following a final symlink."""
    path = os.path.abspath(path)
    try:
        if os.path.islink(path):
            parent = os.path.realpath(os.path.dirname(path))
            return parent + os.sep + os.path.basename(path)
        return os.path.realpath(path)
    except OSError:
        return path


def _allowed(path):
    # dir_fd-relative names (os.mkdir('demo', dir_fd=...)) are not absolute.
    # /dev/null is the bridge child's stdin, not a kit write.
    if not os.path.isabs(path) or path == '/dev/null':
        return True
    real = _resolved(path)
    return any(real == prefix or real.startswith(prefix + os.sep) for prefix in _ALLOW)


def _audit(event, args):
    path = None
    writing = False
    if event == 'open' and args:
        path = args[0]
        mode = args[1] if len(args) > 1 else ''
        flags = args[2] if len(args) > 2 else 0
        if isinstance(mode, str) and any(ch in mode for ch in 'wxa+'):
            writing = True
        if isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC):
            writing = True
    elif event in {'os.mkdir', 'os.rename', 'os.replace', 'os.symlink', 'os.unlink', 'os.remove', 'os.rmdir', 'os.truncate', 'os.link'} and args:
        path = args[1] if event in {'os.rename', 'os.replace', 'os.link', 'os.symlink'} and len(args) > 1 else args[0]
        writing = True
    if not _ALLOW or not writing or not isinstance(path, str):
        return
    _WRITES.append(f'{event}:{path}')
    if not _allowed(path):
        raise PermissionError(f'write outside tmp: {event} {path}')


def _install_hook():
    global _HOOKED
    if not _HOOKED:
        sys.addaudithook(_audit)
        _HOOKED = True


def load():
    import importlib
    import importlib.util
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
    bridge = importlib.import_module(NAME + '.bridge')
    return module, runtime, kit, lib, bridge


class _Ctx:
    def __init__(self):
        self.settings = {}
        self.calls = {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def set_config(self, key, value):
        self.settings[key] = value

    def register_cli_command(self, **kw):
        self.calls['cli'] = kw

    def register_command(self, **kw):
        self.calls['slash'] = kw

    def register_skill(self, **kw):
        self.calls['skill'] = kw


def _listing(path):
    if not Path(path).exists():
        return []
    return sorted(os.listdir(path))


def _snapshot(root):
    """Every entry under root with its bytes or link target; never follows links."""
    rows = []
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
                rows.append((rel, 'file', Path(path).read_bytes()))
    return rows


class PathSafetyTests(unittest.TestCase):
    def setUp(self):
        self.plugin, self.runtime, self.kit, self.lib, self.bridge = load()
        # The fixture pins tempfile.tempdir to a fresh per-test pytest folder.
        self.scratch = Path(tempfile.gettempdir()).resolve()
        self.base = self.scratch / ('case-' + os.urandom(6).hex())
        self.base.mkdir(parents=True)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.work = self.base / 'work'
        self.work.mkdir()
        self.outside = self.base / 'outside'
        self.outside.mkdir()
        # Allowed: this test's root, plus the interpreter's own (per-test) temp folder
        # for subprocess and tempfile plumbing.
        _install_hook()
        _WRITES.clear()
        _ALLOW[:] = [str(self.base), str(self.scratch)]
        self.addCleanup(self._release)

    def _release(self):
        _ALLOW.clear()

    def test_guard_refuses_a_write_outside_the_root(self):
        # The pinned root's parent folder is pytest's; this sibling is outside the allow-list.
        ghost = self.scratch.parent / ('guard-probe-' + os.urandom(4).hex()) / 'x.txt'
        with self.assertRaises(PermissionError) as caught:
            open(ghost, 'w')
        self.assertIn('write outside tmp', str(caught.exception))
        self.assertFalse(os.path.lexists(ghost))
        self.assertFalse(os.path.lexists(ghost.parent))
        with self.assertRaises(PermissionError):
            os.mkdir(ghost.parent)
        self.assertFalse(os.path.lexists(ghost.parent))

    def _env(self, hermes):
        if hermes is None:
            return {}
        return {'HERMES_HOME': hermes}

    def _check(self, path, *, hermes=None, cwd=None):
        old = os.getcwd()
        if cwd is not None:
            os.chdir(cwd)
        try:
            return self.runtime.check_safe_target(path, home=str(self.home), env=self._env(hermes))
        finally:
            os.chdir(old)

    def _apply(self, path, *, hermes=None):
        old = os.environ.get('HERMES_HOME')
        os.environ.pop('HERMES_HOME', None)
        if hermes is not None:
            os.environ['HERMES_HOME'] = hermes
        try:
            return self.kit.run(
                ['apply', 'component-library', path, '--json'],
                cwd=str(self.work),
                home=str(self.home),
            )
        finally:
            if old is None:
                os.environ.pop('HERMES_HOME', None)
            else:
                os.environ['HERMES_HOME'] = old

    def test_target_matrix_refuses_resolved_and_lexical_dangers(self):
        """At least 40 lexical and symlink cases. Refused paths are not created."""
        dot = self.home / '.hermes'
        custom = self.work / 'custom-hermes'
        custom.mkdir()
        backing = self.work / 'hermes-backing'
        backing.mkdir()
        hh_link = self.work / 'hh-link'
        hh_link.symlink_to(backing)
        home_link = self.work / 'link-to-home'
        home_link.symlink_to(self.home)
        parent_link = self.work / 'link-to-home-parent'
        parent_link.symlink_to(self.base)
        root_link = self.work / 'link-to-root'
        root_link.symlink_to('/')
        projects = self.work / 'real projects'
        projects.mkdir()
        projects_link = self.work / 'projects-link'
        projects_link.symlink_to(projects)
        (projects / 'café').mkdir()
        fake_var = self.work / 'fake-root' / 'var' / 'tmp'
        fake_opt = self.work / 'fake-root' / 'opt'
        fake_var.mkdir(parents=True)
        fake_opt.mkdir(parents=True)
        other = self.work / 'other-cwd'
        other.mkdir()
        (self.work / 'rel-hermes').mkdir()

        refused = []
        allowed = []

        def refuse(name, path, hermes=None, cwd=None):
            refused.append((name, path, hermes, cwd))

        def allow(name, path, hermes=None, cwd=None):
            allowed.append((name, path, hermes, cwd))

        refuse('lexical-home', str(self.home))
        refuse('lexical-home-ancestor', str(self.home.parent))
        refuse('lexical-root', '/')
        refuse('lexical-hermes', str(dot))
        refuse('lexical-hermes-sub', str(dot / 'plugins'))
        refuse('symlink-home-itself', str(home_link))
        refuse('symlink-into-home-project', str(home_link / 'project'))
        refuse('symlink-ancestor-is-home', str(parent_link / 'home'))
        refuse('symlink-ancestor-into-home-child', str(parent_link / 'home' / 'project'))
        refuse('symlink-into-hermes', str(home_link / '.hermes' / 'evil'))
        refuse('symlinked-hermes-backing', str(backing), hermes=str(hh_link))
        refuse('symlinked-hermes-backing-sub', str(backing / 'sub'), hermes=str(hh_link))
        refuse('root-symlink-top-level', str(root_link / 'cz-sec-kitpath-not-created'))
        refuse('root-symlink-etc', str(root_link / 'etc' / 'cz-sec-kitpath-not-created'))
        for system in (
            '/etc', '/etc/cz-sec-kitpath-not-created', '/usr', '/usr/bin', '/bin', '/sbin',
            '/boot', '/dev', '/proc', '/sys', '/var', '/opt', '/lib', '/lib64', '/tmp',
        ):
            refuse('system-' + system.strip('/').replace('/', '-'), system)
        refuse('hh-unset-default', str(dot), hermes=None)
        refuse('hh-empty-default', str(dot), hermes='')
        refuse('hh-blank-default', str(dot), hermes='   ')
        refuse('hh-relative-still-protects-default', str(dot / 'x'), hermes='rel-hermes', cwd=str(other))
        refuse('hh-tilde-home', str(self.home / 'proj'), hermes='~')
        refuse('hh-tilde-slash', str(self.home / 'x' / 'proj'), hermes='~/x')
        refuse('hh-tilde-user-still-protects-default', str(dot / 'from-tilde'), hermes='~nosuchczuser/.hermes')
        refuse('hh-nosuch-still-protects-default-cwd', str(dot), hermes='~nosuchczuser/.hermes', cwd=str(other))

        allow('explicit-project-inside-home', str(self.home / 'component-library'))
        allow('symlinked-projects-folder', str(projects_link / 'demo'))
        allow('spaces', str(self.work / 'my project'))
        allow('unicode', str(self.work / 'café-proj'))
        allow('fake-var-tmp-style', str(fake_var / 'x'))
        allow('fake-opt-project-style', str(fake_opt / 'project'))
        allow('hh-relative-not-cwd-bound', str(self.work / 'rel-hermes' / 'proj'), hermes='rel-hermes', cwd=str(other))
        allow('hh-relative-same-from-work', str(self.work / 'rel-hermes' / 'proj'), hermes='rel-hermes', cwd=str(self.work))
        allow('hh-unset-ordinary', str(self.work / 'ordinary'), hermes=None)
        allow('hh-empty-ordinary', str(self.work / 'ordinary-empty'), hermes='')
        allow('var-tmp-string', '/var/tmp/dedicated-proj')
        allow('opt-project-string', '/opt/dedicated-proj')
        allow('srv-child', '/srv/x')

        cases = refused + allowed
        self.assertGreaterEqual(len(cases), 40, len(cases))
        for name, path, hermes, cwd in refused:
            with self.subTest(refused=name):
                err = self._check(path, hermes=hermes, cwd=cwd)
                self.assertIsNotNone(err, name)
                self.assertTrue(str(err).startswith('refusing '), err)
                self.assertNotIn('\n', err)
        for name, path, hermes, cwd in allowed:
            with self.subTest(allowed=name):
                err = self._check(path, hermes=hermes, cwd=cwd)
                self.assertIsNone(err, (name, err))

        # Real system paths and the filesystem root are strings only: not created.
        for ghost in ('/cz-sec-kitpath-not-created', '/etc/cz-sec-kitpath-not-created', '/tmp/cz-sec-kitpath-not-created'):
            self.assertFalse(os.path.lexists(ghost), ghost)
        self.assertFalse((self.home / 'project').exists())
        self.assertFalse((dot / 'evil').exists())
        outside_writes = [item for item in _WRITES if not _allowed(item.split(':', 1)[-1])]
        self.assertEqual(outside_writes, [])

    def test_apply_and_init_do_not_write_through_home_or_root_symlinks(self):
        home_link = self.work / 'link-to-home'
        home_link.symlink_to(self.home)
        before = _listing(self.home)
        res = self._apply(str(home_link / 'project'))
        self.assertEqual(res['exit'], 2, res)
        data = json.loads(res['stdout'])
        self.assertFalse(data['ok'])
        self.assertIn('home or filesystem root', data['error'])
        self.assertEqual(_listing(self.home), before)
        self.assertFalse((self.home / 'AGENTS.md').exists())

        root_link = self.work / 'link-to-root'
        root_link.symlink_to('/')
        ghost = '/cz-sec-kitpath-not-created'
        self.assertFalse(os.path.lexists(ghost))
        root_res = self._apply(str(root_link / 'cz-sec-kitpath-not-created'))
        self.assertEqual(root_res['exit'], 2, root_res)
        self.assertFalse(os.path.lexists(ghost))
        self.assertIn('system directory', json.loads(root_res['stdout'])['error'])

        ctx = _Ctx()
        self.plugin.register(ctx)
        old_home = os.environ.get('HOME')
        old_hh = os.environ.get('HERMES_HOME')
        os.environ['HOME'] = str(self.home)
        os.environ.pop('HERMES_HOME', None)
        old = os.getcwd()
        os.chdir(self.work)
        try:
            text = ctx.calls['slash']['handler'](f'init {home_link / "library"}')
        finally:
            os.chdir(old)
            if old_home is None:
                os.environ.pop('HOME', None)
            else:
                os.environ['HOME'] = old_home
            if old_hh is None:
                os.environ.pop('HERMES_HOME', None)
            else:
                os.environ['HERMES_HOME'] = old_hh
        self.assertIn('exit=2', text)
        self.assertNotIn('library_root', ctx.settings)
        self.assertFalse((self.home / 'library').exists())

    def test_allowed_symlink_parent_stores_and_shows_the_resolved_path(self):
        projects = self.work / 'real-projects'
        projects.mkdir()
        link = self.work / 'projects-link'
        link.symlink_to(projects)
        target = link / 'demo'
        res = self._apply(str(target))
        self.assertEqual(res['exit'], 0, res)
        data = json.loads(res['stdout'])
        resolved = str(projects / 'demo')
        self.assertEqual(data['root'], resolved)
        self.assertTrue((projects / 'demo' / 'AGENTS.md').is_file())
        self.assertFalse((link / 'demo').is_symlink())

        ctx = _Ctx()
        self.plugin.register(ctx)
        old_home = os.environ.get('HOME')
        old_hh = os.environ.get('HERMES_HOME')
        os.environ['HOME'] = str(self.home)
        os.environ['HERMES_HOME'] = str(self.home / '.hermes')
        old = os.getcwd()
        os.chdir(self.work)
        try:
            reply = ctx.calls['slash']['handler'](f'init {link / "lib"} --json')
        finally:
            os.chdir(old)
            if old_home is None:
                os.environ.pop('HOME', None)
            else:
                os.environ['HOME'] = old_home
            if old_hh is None:
                os.environ.pop('HERMES_HOME', None)
            else:
                os.environ['HERMES_HOME'] = old_hh
        self.assertIn('exit=0', reply, reply)
        self.assertEqual(ctx.settings['library_root'], str(projects / 'lib'))
        self.assertTrue((projects / 'lib' / 'components' / 'COMPONENTS.md').is_file())

    def test_relative_hermes_home_does_not_depend_on_cwd(self):
        proj = str(self.work / 'rel-hermes' / 'proj')
        (self.work / 'rel-hermes').mkdir()
        other = self.work / 'other-cwd'
        other.mkdir()
        from_other = self._check(proj, hermes='rel-hermes', cwd=str(other))
        from_work = self._check(proj, hermes='rel-hermes', cwd=str(self.work))
        self.assertEqual(from_other, from_work)
        self.assertIsNone(from_other)
        self.assertIsNotNone(self._check(str(self.home / '.hermes' / 'x'), hermes='rel-hermes', cwd=str(other)))
        self.assertIsNotNone(self._check(str(self.home / '.hermes'), hermes='', cwd=str(other)))
        self.assertIsNotNone(self._check(str(self.home / '.hermes'), hermes=None, cwd=str(other)))
        self.assertIsNotNone(self._check(str(self.home / '.hermes'), hermes='~nosuchczuser/.hermes', cwd=str(other)))

    def test_race_swap_between_check_and_create_does_not_follow(self):
        target = self.work / 'race-target'
        swapped = {'done': False}

        def swap(path):
            if os.path.normpath(path) == str(target) and not swapped['done']:
                swapped['done'] = True
                if target.is_symlink():
                    target.unlink()
                elif target.exists():
                    os.rename(target, str(target) + '-real')
                os.symlink(self.outside, target)

        original = self.runtime.before_secure_create
        self.runtime.before_secure_create = swap
        # ensure_real_directory looks up the function globally in its module.
        import importlib
        mod = importlib.import_module(self.runtime.__name__)
        mod.before_secure_create = swap
        try:
            res = self._apply(str(target))
        finally:
            mod.before_secure_create = original
        self.assertTrue(swapped['done'])
        self.assertEqual(res['exit'], 2, res)
        self.assertFalse((self.outside / 'AGENTS.md').exists())
        self.assertFalse((self.outside / 'components').exists())
        self.assertEqual(_listing(self.outside), [])
        self.assertIn('symlink', json.loads(res['stdout'])['error'])

    def test_set_dotdot_and_absolute_are_refused_before_write(self):
        target = self.work / 'set-target'
        for value in ('../escape', '/etc/passwd', '.', 'foo/../../x', '~/secret'):
            with self.subTest(value=value):
                res = self.kit.run(
                    ['apply', 'web-app', str(target), '--set', f'project={value}', '--json'],
                    cwd=str(self.work),
                    home=str(self.home),
                )
                self.assertEqual(res['exit'], 2, res)
                data = json.loads(res['stdout'])
                self.assertIn("must not contain '..' or an absolute path", data['error'])
                self.assertFalse(target.exists())
                self.assertFalse((self.work / 'escape').exists())
        ok = self.kit.run(
            ['apply', 'component-library', str(self.work / 'set-ok'), '--set', 'components=modules', '--json'],
            cwd=str(self.work),
            home=str(self.home),
        )
        self.assertEqual(ok['exit'], 0, ok)
        self.assertTrue((self.work / 'set-ok' / 'modules').is_dir())

    def test_second_realpath_cannot_redirect_kit_or_library_writes(self):
        """A later realpath of the same target must not receive the write."""
        for kind in ('apply', 'init'):
            with self.subTest(kind=kind):
                safe = self.work / f'safe-{kind}'
                safe.mkdir()
                link = self.work / f'link-{kind}'
                link.symlink_to(safe)
                victim = self.home / f'projects-{kind}'
                victim.mkdir()
                leaf = 'p' if kind == 'apply' else 'lib'
                user_path = os.path.normpath(str(link / leaf))
                first = os.path.realpath(user_path)
                seen = {'n': 0}
                real = os.path.realpath

                def patched(path):
                    key = os.path.normpath(path) if isinstance(path, str) else path
                    if key == user_path:
                        seen['n'] += 1
                        if seen['n'] >= 2:
                            return str(victim / leaf)
                    return real(path)

                os.path.realpath = patched
                try:
                    if kind == 'apply':
                        res = self._apply(user_path)
                        self.assertEqual(res['exit'], 0, res)
                    else:
                        res = self.lib.init_library(user_path, forbidden=(str(self.home),))
                        self.assertTrue(res['ok'], res)
                finally:
                    os.path.realpath = real
                self.assertFalse((victim / leaf).exists(), kind)
                self.assertFalse((victim / leaf / 'AGENTS.md').exists(), kind)
                self.assertTrue((Path(first) / 'AGENTS.md').is_file(), kind)
                self.assertEqual(os.path.realpath(str(Path(first) / 'AGENTS.md')).startswith(str(safe) + os.sep) or os.path.realpath(str(Path(first) / 'AGENTS.md')) == str(safe), True)

    def test_ancestor_swap_between_check_and_create_stays_in_first_root(self):
        """Swapping a parent symlink after the check must not create under the new target."""
        hermes = self.home / '.hermes'
        hermes.mkdir()
        for label, swap_to in (('home', self.home / 'projects'), ('hermes', hermes)):
            with self.subTest(label=label):
                safe = self.work / f'safe-{label}'
                safe.mkdir()
                link = self.work / f'lk-{label}'
                link.symlink_to(safe)
                swap_to.mkdir(exist_ok=True)
                before = _listing(swap_to)
                target = link / 'p'
                swapped = {'done': False}

                def swap(path):
                    if not swapped['done']:
                        swapped['done'] = True
                        link.unlink()
                        link.symlink_to(swap_to)

                import importlib
                mod = importlib.import_module(self.runtime.__name__)
                original = mod.before_secure_create
                mod.before_secure_create = swap
                _WRITES.clear()
                try:
                    res = self._apply(str(target))
                finally:
                    mod.before_secure_create = original
                self.assertTrue(swapped['done'], label)
                self.assertFalse((swap_to / 'p').exists(), label)
                self.assertFalse((swap_to / 'p' / 'AGENTS.md').exists(), label)
                self.assertEqual(_listing(swap_to), before, label)
                self.assertTrue((safe / 'p' / 'AGENTS.md').is_file(), (label, res))
                self.assertEqual(res['exit'], 0, res)

        safe = self.work / 'safe-init'
        safe.mkdir()
        link = self.work / 'lk-init'
        link.symlink_to(safe)
        victim = self.home / 'init-projects'
        victim.mkdir()
        before = _listing(victim)

        def swap_init(path):
            if link.is_symlink():
                link.unlink()
                link.symlink_to(victim)

        import importlib
        mod = importlib.import_module(self.runtime.__name__)
        original = mod.before_secure_create
        mod.before_secure_create = swap_init
        try:
            res = self.lib.init_library(str(link / 'lib'), forbidden=(str(self.home),))
        finally:
            mod.before_secure_create = original
        self.assertTrue(res['ok'], res)
        self.assertFalse((victim / 'lib').exists())
        self.assertEqual(_listing(victim), before)
        self.assertTrue((safe / 'lib' / 'AGENTS.md').is_file())

    def test_resolved_target_hook_cannot_redirect_the_write(self):
        """The old second resolution was kit_commands.resolved_target. A swap there must not land outside."""
        safe = self.work / 'hook-safe'
        safe.mkdir()
        link = self.work / 'hook-link'
        link.symlink_to(safe)
        victim = self.home / 'hook-projects'
        victim.mkdir()
        orig = self.runtime.resolved_target
        fired = {'n': 0}
        user = os.path.normpath(str(link / 'p'))

        def swapped(path):
            if isinstance(path, str) and os.path.normpath(path) == user:
                fired['n'] += 1
                if fired['n'] >= 2:
                    link.unlink()
                    link.symlink_to(victim)
            return orig(path)

        setattr(self.runtime, 'resolved_target', swapped)
        try:
            res = self._apply(user)
        finally:
            setattr(self.runtime, 'resolved_target', orig)
        self.assertEqual(fired['n'], 1)
        self.assertFalse(hasattr(self.kit, 'resolved_target'))
        self.assertFalse((victim / 'p').exists())
        self.assertFalse((victim / 'p' / 'AGENTS.md').exists())
        self.assertTrue((safe / 'p' / 'AGENTS.md').is_file(), res)
        self.assertEqual(res['exit'], 0, res)

    def _swap_during_engine_writes(self, name, *, restore_before_report):
        """Swap the target for a link to an outside folder at the engine's third create.

        Returns (result, outside folder, folder the target was moved to, outside
        snapshot before the run, swap state, restore function).
        """
        target = self.work / name
        dest = self.outside / (name + '-dest')
        dest.mkdir()
        (dest / 'victim.txt').write_bytes(b'outside: must not change\n')
        (dest / 'components').mkdir()
        (dest / 'components' / 'keep.py').write_bytes(b'# keep\n')
        before = _snapshot(dest)
        aside = Path(str(target) + '-aside')
        engine = self.kit.ENGINE
        orig_cm = engine._create_missing
        st = {'n': 0, 'swapped': False, 'restored': False, 'id': None}

        def create_missing(parent_fd, leaf, rel, want, content, items, conflict):
            st['n'] += 1
            if st['n'] == 3 and not st['swapped']:
                st['swapped'] = True
                st['id'] = (os.stat(target).st_dev, os.stat(target).st_ino)
                os.rename(target, aside)
                os.symlink(dest, target)
            return orig_cm(parent_fd, leaf, rel, want, content, items, conflict)

        def restore():
            if st['swapped'] and not st['restored']:
                st['restored'] = True
                if target.is_symlink():
                    target.unlink()
                if aside.exists():
                    os.rename(aside, target)

        orig_apply = engine.apply_kit

        def apply_kit(kit, root, **kwargs):
            try:
                return orig_apply(kit, root, **kwargs)
            finally:
                if restore_before_report:
                    restore()

        engine._create_missing = create_missing
        engine.apply_kit = apply_kit
        try:
            res = self._apply(str(target))
        finally:
            engine._create_missing = orig_cm
            engine.apply_kit = orig_apply
        return res, dest, aside, before, st, restore

    def test_engine_swap_report_does_not_list_unwritten_paths(self):
        """A target swapped for a link during the engine's writes: nothing goes through the link.

        The engine writes relative to the folder handle the caller verified, so every
        file lands in the original folder (now moved aside) and the outside folder stays
        byte-identical. The path no longer names the verified folder, so the run is
        refused, and the report lists only what it checked, never an outside path.
        """
        res, dest, aside, before, st, restore = self._swap_during_engine_writes('er', restore_before_report=False)
        try:
            data = json.loads(res['stdout'])
            err = data.get('error') or ''
            self.assertTrue(st['swapped'], (st, res['exit']))
            self.assertGreater(st['n'], 3, st)
            self.assertEqual(_snapshot(dest), before)
            self.assertEqual((os.stat(aside).st_dev, os.stat(aside).st_ino), st['id'])
            self.assertTrue((aside / 'AGENTS.md').is_file())
            self.assertTrue((aside / 'components' / 'COMPONENTS.md').is_file())
            self.assertEqual(res['exit'], 2, res)
            self.assertEqual(data['status'], 'error', data)
            self.assertNotIn('\n', err)
            self.assertIn('not confirmed', err)
            self.assertIn('identity changed', err)
            self.assertIn('checked ', err)
            self.assertIn('does not list files it did not open', err)
            # Every written path still opens from the verified folder.
            self.assertNotIn('could not be opened there', err)
            self.assertNotIn(str(dest), err)
            self.assertNotIn(str(aside), err)
        finally:
            restore()

    def test_engine_swap_undone_before_the_report_leaves_everything_in_the_verified_folder(self):
        res, dest, aside, before, st, restore = self._swap_during_engine_writes('eu', restore_before_report=True)
        restore()
        data = json.loads(res['stdout'])
        target = self.work / 'eu'
        self.assertTrue(st['swapped'] and st['restored'], st)
        self.assertEqual(_snapshot(dest), before)
        self.assertEqual((os.stat(target).st_dev, os.stat(target).st_ino), st['id'])
        self.assertEqual(res['exit'], 0, res)
        self.assertIsNone(data['error'], data)
        self.assertTrue((target / 'AGENTS.md').is_file())
        self.assertTrue((target / 'components' / 'COMPONENTS.md').is_file())
        again = self._apply(str(target))
        self.assertEqual(again['exit'], 0, again)
        self.assertEqual({i['state'] for i in json.loads(again['stdout'])['items']}, {'kept'})

    def test_refused_apply_and_init_perform_zero_writes(self):
        _WRITES.clear()
        refused = self._apply(str(self.home))
        self.assertEqual(refused['exit'], 2, refused)
        self.assertEqual(_WRITES, [], _WRITES)
        _WRITES.clear()
        init = self.lib.init_library(str(self.home), forbidden=(str(self.home),))
        self.assertFalse(init['ok'])
        self.assertEqual(_WRITES, [], _WRITES)
        self.assertFalse((self.home / 'AGENTS.md').exists())

    def test_empty_relative_or_unset_home_still_protects_the_account_home(self):
        """HOME empty, relative, or unset must not turn off home or ~/.hermes protection."""
        import pwd
        account = pwd.getpwuid(os.getuid()).pw_dir
        self.assertTrue(os.path.isabs(account) and os.path.isdir(account))
        hermes = account + '/.hermes/cz-sec2-not-created'
        old = os.environ.get('HOME')
        try:
            for label, value in (('empty', ''), ('relative', 'relhome'), ('unset', None)):
                with self.subTest(label=label):
                    if value is None:
                        os.environ.pop('HOME', None)
                    else:
                        os.environ['HOME'] = value
                    err = self.runtime.check_safe_target(account, home=None, env={})
                    hermes_err = self.runtime.check_safe_target(hermes, home=None, env={})
                    self.assertIsNotNone(err, label)
                    self.assertTrue(str(err).startswith('refusing '), err)
                    self.assertIsNotNone(hermes_err, label)
                    self.assertTrue(str(hermes_err).startswith('refusing '), hermes_err)
                    _WRITES.clear()
                    res = self.kit.run(
                        ['apply', 'component-library', account, '--json'],
                        cwd=str(self.work),
                        home=value if value is not None else '',
                    )
                    self.assertEqual(res['exit'], 2, res)
                    self.assertEqual(_WRITES, [], _WRITES)
                    self.assertFalse(os.path.lexists(hermes))
        finally:
            if old is None:
                os.environ.pop('HOME', None)
            else:
                os.environ['HOME'] = old

    def test_undetermined_home_refuses_every_writing_command(self):
        import pwd
        old = os.environ.get('HOME')
        real_pw = pwd.getpwuid
        os.environ['HOME'] = 'relhome'

        class _Missing:
            pw_dir = 'not-absolute'

        def missing(_uid):
            return _Missing()

        pwd.getpwuid = missing
        _WRITES.clear()
        try:
            res = self.kit.run(
                ['apply', 'component-library', str(self.work / 'no-home'), '--json'],
                cwd=str(self.work),
                home='',
            )
            init = self.lib.init_library(str(self.work / 'no-home-init'), forbidden=('',))
        finally:
            pwd.getpwuid = real_pw
            if old is None:
                os.environ.pop('HOME', None)
            else:
                os.environ['HOME'] = old
        line = 'cannot determine your home folder, so the safety check cannot run'
        self.assertEqual(res['exit'], 2, res)
        self.assertIn(line, res['stdout'])
        self.assertEqual(init.get('error'), line)
        self.assertFalse(init['ok'])
        self.assertEqual(_WRITES, [])
        self.assertFalse((self.work / 'no-home').exists())
        self.assertFalse((self.work / 'no-home-init').exists())

    def test_control_characters_are_escaped_in_messages(self):
        evil = self.work / 'evil\x1b[2J\nbidi\u202e'
        evil.mkdir()
        link = self.work / 'innocent'
        link.symlink_to(evil)
        res = self.kit.run(
            ['apply', 'web-app', str(link / 'p')],
            cwd=str(self.work),
            home=str(self.home),
        )
        out = res['stdout'] + res['stderr']
        self.assertEqual(res['exit'], 0, res)
        self.assertNotIn('\x1b', out)
        self.assertNotIn('\u202e', out)
        self.assertIn('\\x1b', out)
        self.assertIn('\\n', out)
        self.assertIn('\\u202e', out)
        bad = self.work / 'bad\x1b'
        bad.symlink_to(self.home)
        refused = self.kit.run(
            ['apply', 'web-app', str(bad / 'p'), '--json'],
            cwd=str(self.work),
            home=str(self.home),
        )
        err = json.loads(refused['stdout'])['error']
        self.assertNotIn('\x1b', err)
        self.assertIn('\\x1b', err)
        long_err = self.runtime.check_safe_target('/' + ('a' * 400), home=str(self.home))
        self.assertIsNotNone(long_err)
        self.assertLess(len(long_err), 320)
        self.assertIn('...', long_err)
        self.assertNotIn('a' * 400, long_err)

    def test_bridge_child_does_not_receive_parent_secrets(self):
        import subprocess
        captured = {}
        real = subprocess.Popen

        def capturing(argv, **kwargs):
            captured['env'] = kwargs.get('env')
            captured['argv'] = argv
            raise OSError('stop-before-exec')

        subprocess.Popen = capturing
        try:
            env = {
                'PATH': '/usr/bin:/bin',
                'HOME': str(self.home),
                'HERMES_HOME': str(self.home / '.hermes'),
                'TMPDIR': str(self.base),
                'SECRET_CANARY': 'probe-secret-canary-do-not-leak',
                'AWS_SECRET_ACCESS_KEY': 'not-a-real-secret',
            }
            result = self.bridge.run(['--help'], cwd=str(self.work), env=env)
        finally:
            subprocess.Popen = real
        self.assertEqual(result['exit'], 2)
        child = captured['env']
        self.assertNotIn('SECRET_CANARY', child)
        self.assertNotIn('AWS_SECRET_ACCESS_KEY', child)
        self.assertEqual(child['PATH'], '/usr/bin:/bin')
        self.assertEqual(child['HOME'], str(self.home))
        self.assertEqual(child['HERMES_HOME'], str(self.home / '.hermes'))
        self.assertEqual(child['TMPDIR'], str(self.base))
        self.assertNotIn('shell', ' '.join(captured['argv']))


if __name__ == '__main__':
    unittest.main()
