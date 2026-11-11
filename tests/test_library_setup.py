"""Library setup, first-run gate, and a fresh-folder run through the real bundled CLI."""
import argparse
import contextlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NAME = 'componentize_plugin_library_test'
# The private source path must not appear.
PRIVATE_PREFIX = 'To' + 'ols/'


def load():
    spec = importlib.util.spec_from_file_location(NAME, ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, importlib.import_module(NAME + '.library_setup')


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(Path(root).rglob('*'))}


class Context:
    def __init__(self, settings=None, can_save=True):
        self.calls, self.settings = {}, dict(settings or {})
        if can_save:
            self.set_config = self.settings.__setitem__

    def get_config(self, key, default=None): return self.settings.get(key, default)
    def register_cli_command(self, **kw): self.calls['cli'] = kw
    def register_command(self, **kw): self.calls['slash'] = kw
    def register_skill(self, **kw): self.calls['skill'] = kw


@contextlib.contextmanager
def in_dir(path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


class LibrarySetupTests(unittest.TestCase):
    def setUp(self):
        self.plugin, self.lib = load()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(os.path.realpath(self.tmp.name))

    def test_fresh_folder_is_created_and_second_run_changes_nothing(self):
        root = self.base / 'new library'
        result = self.lib.init_library(str(root))
        self.assertEqual(result['status'], 'pass', result)
        for rel in ('components/__init__.py', 'components/COMPONENTS.md', 'AGENTS.md', '.gitignore'):
            self.assertTrue((root / rel).is_file(), rel)
        for rel in ('components/tests', 'scripts', 'specs'):
            self.assertTrue((root / rel).is_dir(), rel)
        agents = (root / 'AGENTS.md').read_text()
        self.assertIn('Reuse before you write', agents)
        self.assertLess(len(agents), self.lib.CONTEXT_FILE_MAX_CHARS)
        self.assertEqual((root / '.gitignore').read_text(), '.componentize/\n')
        before = snapshot(root)
        again = self.lib.init_library(str(root))
        self.assertEqual({item['state'] for item in again['items']}, {'kept'})
        self.assertEqual(snapshot(root), before)
        self.assertEqual(self.lib.inspect_library(str(root))['status'], 'pass')
        self.assertIn('# vendored from components/<module>.py, do not edit', agents)
        self.assertNotIn('hermes_audit', agents + (root / 'components/COMPONENTS.md').read_text())

    def test_folder_already_using_hermes_audit_keeps_it(self):
        root = self.base / 'tools'
        (root / 'hermes_audit').mkdir(parents=True)
        (root / 'hermes_audit/COMPONENTS.md').write_text('OLD INDEX\n')
        result = self.lib.init_library(str(root))
        self.assertEqual(result['status'], 'pass', result)
        self.assertFalse((root / 'components').exists())
        self.assertEqual((root / 'hermes_audit/COMPONENTS.md').read_text(), 'OLD INDEX\n')
        agents = (root / 'AGENTS.md').read_text()
        self.assertIn('`hermes_audit/COMPONENTS.md`', agents)
        self.assertIn('# vendored from hermes_audit/<module>.py, do not edit', agents)
        self.assertNotIn(PRIVATE_PREFIX, agents)
        self.assertEqual(self.lib.inspect_library(str(root))['status'], 'pass')

    def test_legacy_library_from_previous_plugin_upgrades_header_sentence(self):
        # A hermes_audit/ library set up by the previous plugin: its managed block was
        # rendered with a vendor_root under the author's source tree. Build exactly that block.
        root = self.base / 'old tools'
        (root / 'hermes_audit').mkdir(parents=True)
        index = root / 'hermes_audit/COMPONENTS.md'
        index.write_text(f'OLD INDEX\nheader `# vendored from {PRIVATE_PREFIX}hermes_audit/<module>.py, do not edit`\n')
        (root / 'AGENTS.md').write_text('# Mine\n')
        new_variables = self.lib._variables
        self.lib._variables = lambda r: {'components': 'hermes_audit', 'vendor_root': PRIVATE_PREFIX + 'hermes_audit'}
        try:
            self.assertEqual(self.lib.init_library(str(root))['status'], 'pass')
        finally:
            self.lib._variables = new_variables
        agents = root / 'AGENTS.md'
        old_text = agents.read_text()
        self.assertIn(f'# vendored from {PRIVATE_PREFIX}hermes_audit/<module>.py, do not edit', old_text)
        index_before = index.read_bytes()

        inspected = self.lib.inspect_library(str(root))
        self.assertEqual(inspected['status'], 'fail')
        self.assertEqual([(f['id'], f['detail']) for f in inspected['findings']],
                         [('kit-missing', 'AGENTS.md: managed block out of date')])
        self.assertTrue(inspected['findings'][0]['fix'].startswith('run: hermes componentize init '))

        upgraded = self.lib.init_library(str(root))
        self.assertEqual(upgraded['status'], 'pass', upgraded)
        self.assertEqual({i['path']: i['state'] for i in upgraded['items']}['AGENTS.md'], 'updated')
        new_text = agents.read_text()
        self.assertIn('# vendored from hermes_audit/<module>.py, do not edit', new_text)
        self.assertNotIn(PRIVATE_PREFIX, new_text)
        self.assertTrue(new_text.startswith('# Mine\n'))
        self.assertEqual(index.read_bytes(), index_before)  # the user's own index is never rewritten

        self.assertEqual(self.lib.inspect_library(str(root))['status'], 'pass')
        before = snapshot(root)
        again = self.lib.init_library(str(root))
        self.assertEqual({item['state'] for item in again['items']}, {'kept'})
        self.assertEqual(snapshot(root), before)

    def test_existing_files_are_preserved(self):
        root = self.base / 'existing'
        (root / 'components').mkdir(parents=True)
        (root / 'components/COMPONENTS.md').write_text('MY INDEX\n')
        (root / 'AGENTS.md').write_text('# Mine\n\nmy rules')
        (root / '.gitignore').write_text('*.log')
        (root / 'keep.py').write_text('x = 1\n')
        result = self.lib.init_library(str(root))
        self.assertEqual(result['status'], 'pass', result)
        self.assertEqual((root / 'components/COMPONENTS.md').read_text(), 'MY INDEX\n')
        self.assertEqual((root / 'keep.py').read_text(), 'x = 1\n')
        agents = (root / 'AGENTS.md').read_text()
        self.assertTrue(agents.startswith('# Mine\n\nmy rules\n\n' + self.lib.BLOCK_BEGIN))
        self.assertIn('`components/COMPONENTS.md`', agents)
        self.assertEqual((root / '.gitignore').read_text(), '*.log\n.componentize/\n')

    def test_managed_block_is_repaired_and_outside_text_untouched(self):
        root = self.base / 'lib'
        self.lib.init_library(str(root))
        path = root / 'AGENTS.md'
        good = path.read_text()
        path.write_text('TOP\n' + good.replace('Thin runners', 'EDITED') + 'BOTTOM\n')
        self.assertEqual(self.lib.inspect_library(str(root))['status'], 'fail')
        self.assertEqual(self.lib.init_library(str(root))['status'], 'pass')
        self.assertEqual(path.read_text(), 'TOP\n' + good + 'BOTTOM\n')

    def test_damaged_block_is_reported_not_rewritten(self):
        root = self.base / 'lib'
        self.lib.init_library(str(root))
        path = root / 'AGENTS.md'
        damaged = path.read_text().replace(self.lib.BLOCK_END, '')
        path.write_text(damaged)
        result = self.lib.init_library(str(root))
        self.assertEqual(result['status'], 'fail')
        self.assertIn('agents-block-malformed', [f['id'] for f in result['findings']])
        self.assertEqual(path.read_text(), damaged)

    def test_unsafe_targets_are_refused_without_writes(self):
        home = self.base / 'home'
        home.mkdir()
        target = self.base / 'target'
        target.mkdir()
        (self.base / 'link').symlink_to(target)
        (self.base / 'file').write_text('x')
        for root in (str(home), '/', 'relative/path', str(self.base / 'link'), str(self.base / 'file')):
            result = self.lib.init_library(root, forbidden=(str(home),))
            self.assertEqual(result['status'], 'error', root)
        self.assertEqual(list(home.iterdir()), [])
        self.assertEqual(list(target.iterdir()), [])

    def test_symlinked_index_is_a_conflict(self):
        root = self.base / 'lib'
        (root / 'components').mkdir(parents=True)
        outside = self.base / 'outside.md'
        outside.write_text('OUTSIDE\n')
        (root / 'components/COMPONENTS.md').symlink_to(outside)
        result = self.lib.init_library(str(root))
        self.assertEqual(result['status'], 'fail')
        self.assertEqual(outside.read_text(), 'OUTSIDE\n')

    def test_count_indexed_components_states_caps_and_symlinks(self):
        self.assertEqual(self.lib.count_indexed_components('')['state'], 'not_set')
        self.assertEqual(self.lib.count_indexed_components(None)['state'], 'not_set')
        self.assertEqual(self.lib.count_indexed_components('relative/lib')['state'], 'invalid_path')
        self.assertEqual(self.lib.count_indexed_components(str(self.base) + '/../etc')['state'], 'invalid_path')
        self.assertEqual(self.lib.count_indexed_components(str(self.base / 'missing'))['state'], 'missing')

        # Freshly initialized library has starter template line only -> 0 components
        fresh = self.base / 'fresh-lib'
        self.lib.init_library(str(fresh))
        fresh_info = self.lib.count_indexed_components(str(fresh))
        self.assertEqual(fresh_info, {
            'ok': True,
            'state': 'ready',
            'count': 0,
            'index_rel': 'components/COMPONENTS.md',
            'message': 'Component library index is ready.',
        })

        # Populated index counts bullet and non-bullet entries while ignoring headings/examples
        with open(fresh / 'components/COMPONENTS.md', 'a', encoding='utf-8') as handle:
            handle.write('- `alpha.parse(text)` — parse text — returns `{ok}`.\n')
            handle.write('`beta.check_items(rows)` — check rows — returns `{ok}`.\n')
        ready_info = self.lib.count_indexed_components(str(fresh) + '/')
        self.assertTrue(ready_info['ok'])
        self.assertEqual(ready_info['count'], 2)
        self.assertEqual(ready_info['index_rel'], 'components/COMPONENTS.md')

        # Legacy hermes_audit/COMPONENTS.md is counted when components/COMPONENTS.md is absent
        legacy = self.base / 'legacy-lib'
        (legacy / 'hermes_audit').mkdir(parents=True)
        (legacy / 'hermes_audit/COMPONENTS.md').write_text(
            '# Legacy\n- `gamma.run_check(x)` — check — returns `{ok}`.\n',
            encoding='utf-8',
        )
        legacy_info = self.lib.count_indexed_components(str(legacy))
        self.assertEqual(legacy_info['state'], 'ready')
        self.assertEqual(legacy_info['count'], 1)
        self.assertEqual(legacy_info['index_rel'], 'hermes_audit/COMPONENTS.md')

        # Folder exists without COMPONENTS.md -> missing_index
        bare = self.base / 'bare-dir'
        bare.mkdir()
        self.assertEqual(self.lib.count_indexed_components(str(bare))['state'], 'missing_index')

        # Refuses Hermes homes (forbidden_subtrees and symlinked parent into Hermes home)
        hermes_home = self.base / 'custom-hermes-home'
        (hermes_home / 'components').mkdir(parents=True)
        (hermes_home / '.env').write_text('API_KEY=secret\n', encoding='utf-8')
        (hermes_home / 'components/COMPONENTS.md').write_text('- `x.y()` — z.\n', encoding='utf-8')
        dot_hermes = self.base / 'fake-user' / '.hermes'
        (dot_hermes / 'lib').mkdir(parents=True)
        self.assertEqual(
            self.lib.count_indexed_components(str(dot_hermes / 'lib'), forbidden_subtrees=(str(dot_hermes),))['state'],
            'unsafe_path',
        )
        self.assertEqual(
            self.lib.count_indexed_components(str(hermes_home), forbidden_subtrees=(str(hermes_home),))['state'],
            'unsafe_path',
        )
        alias_parent = self.base / 'alias-to-hermes'
        alias_parent.symlink_to(hermes_home)
        self.assertEqual(
            self.lib.count_indexed_components(str(alias_parent / 'components'), forbidden_subtrees=(str(hermes_home),))['state'],
            'unsafe_path',
        )

        # Refuses symlink root, symlink component dir, and symlink COMPONENTS.md
        sym_root = self.base / 'sym-root'
        sym_root.symlink_to(fresh)
        self.assertEqual(self.lib.count_indexed_components(str(sym_root))['state'], 'unsafe_path')

        sym_comp = self.base / 'sym-comp'
        sym_comp.mkdir()
        (sym_comp / 'components').symlink_to(fresh / 'components')
        self.assertEqual(self.lib.count_indexed_components(str(sym_comp))['state'], 'unsafe_path')

        sym_idx = self.base / 'sym-idx'
        (sym_idx / 'components').mkdir(parents=True)
        (sym_idx / 'components/COMPONENTS.md').symlink_to(fresh / 'components/COMPONENTS.md')
        self.assertEqual(self.lib.count_indexed_components(str(sym_idx))['state'], 'unsafe_path')

        # Enforces entry cap and byte cap without leaking paths in messages
        crowded = self.base / 'crowded'
        crowded.mkdir()
        for idx in range(self.lib.MAX_DIR_ENTRIES + 2):
            (crowded / f'f_{idx}.txt').write_text('', encoding='utf-8')
        crowded_res = self.lib.count_indexed_components(str(crowded))
        self.assertEqual(crowded_res['state'], 'too_many_entries')
        self.assertNotIn(str(crowded), crowded_res['message'])

        oversize = self.base / 'oversize'
        (oversize / 'components').mkdir(parents=True)
        (oversize / 'components/COMPONENTS.md').write_text('a' * (self.lib.MAX_INDEX_BYTES + 10), encoding='utf-8')
        over_res = self.lib.count_indexed_components(str(oversize))
        self.assertEqual(over_res['state'], 'index_too_large')
        self.assertNotIn(str(oversize), over_res['message'])


class FirstRunTests(unittest.TestCase):
    def setUp(self):
        self.plugin, self.lib = load()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(os.path.realpath(self.tmp.name))
        # The folder safety check refuses anything inside a Hermes home. Point HOME and
        # HERMES_HOME at folders of this test so the result does not depend on where the
        # temporary directory of the machine running the tests happens to live.
        default_home = self.base / 'default-home'
        default_home.mkdir()
        env = unittest.mock.patch.dict(
            os.environ, {'HOME': str(default_home), 'HERMES_HOME': str(default_home / '.hermes')})
        env.start()
        self.addCleanup(env.stop)

    def register(self, **kw):
        ctx = Context(**kw)
        self.plugin.register(ctx)
        return ctx

    def cli(self, ctx, argv):
        parser = argparse.ArgumentParser()
        ctx.calls['cli']['setup_fn'](parser)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ctx.calls['cli']['handler_fn'](parser.parse_args(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_split_argv(self):
        split = self.plugin.split_argv
        self.assertEqual(split(['--json', '--tools-root', '/a', 'status', 'job']), ('status', '/a', ['job']))
        self.assertEqual(split(['status', 'job', '--tools-root=/b']), ('status', '/b', ['job']))
        self.assertEqual(split(['--state-root', 'status', 'check', 'job']), ('check', None, ['job']))
        self.assertEqual(split(['--help']), (None, None, []))

    def test_no_library_stops_with_first_run_message_and_writes_nothing(self):
        ctx = self.register()
        empty = self.base / 'empty'
        empty.mkdir()
        with in_dir(empty):
            for command in ('status job', 'check job', 'begin job --manifest spec.json', 'init', 'library'):
                reply = ctx.calls['slash']['handler'](command)
                self.assertIn('exit=2', reply, command)
                self.assertIn('no component library is set up yet', reply, command)
            rc, _out, err = self.cli(ctx, ['init'])  # not a terminal here, so no prompt
            self.assertEqual(rc, 2)
            self.assertIn('hermes componentize init', err)
        self.assertEqual(list(empty.iterdir()), [])

    def test_typed_relative_folder_is_relative_to_where_the_command_runs_and_saved_absolute(self):
        ctx = self.register()
        work = self.base / 'work'
        work.mkdir()
        with in_dir(work):
            rc, out, err = self.cli(ctx, ['init', 'my-lib', '--json'])
        self.assertEqual(rc, 0, out + err)
        self.assertTrue((work / 'my-lib').is_dir())
        self.assertEqual(ctx.settings['library_root'], str(work / 'my-lib'))

    def test_init_and_library_refuse_a_folder_inside_a_hermes_home(self):
        ctx = self.register()
        inside = Path(os.environ['HERMES_HOME']) / 'my-lib'
        for command in ('init', 'library'):
            rc, out, err = self.cli(ctx, [command, str(inside)])
            self.assertEqual(rc, 2, out)
            self.assertIn('outside Hermes home', err)
        self.assertFalse(inside.exists())
        self.assertNotIn('library_root', ctx.settings)

    def test_relative_saved_library_folder_is_refused_before_the_bundled_cli_runs(self):
        (self.base / 'rel-lib').mkdir()
        ctx = self.register(settings={'library_root': 'rel-lib'})
        with in_dir(self.base):
            rc, out, err = self.cli(ctx, ['status', 'nojob'])
        self.assertEqual(rc, 2)
        self.assertEqual(out, '')
        self.assertIn('absolute', err)
        self.assertIn('library_root', err)

    def test_init_saves_folder_and_later_commands_use_it(self):
        ctx = self.register()
        root = self.base / 'lib'
        elsewhere = self.base / 'elsewhere'
        elsewhere.mkdir()
        with in_dir(elsewhere):
            rc, out, _err = self.cli(ctx, ['init', str(root), '--json'])
            self.assertEqual(rc, 0, out)
            self.assertTrue(json.loads(out)['saved'])
            self.assertEqual(ctx.settings['library_root'], str(root))
            self.assertIn('exit=0', ctx.calls['slash']['handler']('library'))
            reply = ctx.calls['slash']['handler']('status nojob')
            self.assertNotIn('no component library', reply)
            self.assertIn('exit=2', reply)  # the bundled CLI answered: job was never begun
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_host_without_set_config_still_initialises_and_says_so(self):
        ctx = self.register(can_save=False)
        root = self.base / 'lib'
        rc, out, _err = self.cli(ctx, ['init', str(root)])
        self.assertEqual(rc, 0, out)
        self.assertIn('library-not-saved', out)
        self.assertTrue((root / 'AGENTS.md').is_file())

    def test_unsaved_library_in_working_directory_still_works(self):
        ctx = self.register(can_save=False)
        root = self.base / 'lib'
        self.cli(ctx, ['init', str(root)])
        with in_dir(root):
            self.assertNotIn('no component library', ctx.calls['slash']['handler']('status nojob'))

    def test_tilde_expansion_in_init_and_library_handlers(self):
        import unittest.mock
        fake_home = self.base / 'fake-home'
        fake_home.mkdir()
        other_home = self.base / 'other-home'
        other_home.mkdir()

        def fake_expanduser(path):
            if path == '~':
                return str(fake_home)
            return str(other_home)

        with unittest.mock.patch('os.path.expanduser', side_effect=fake_expanduser):
            ctx = self.register()
            with in_dir(self.base):
                rc, out, err = self.cli(ctx, ['init', '~/my-lib', '--json'])
                self.assertEqual(rc, 0, err)
                self.assertEqual(ctx.settings.get('library_root'), str(fake_home / 'my-lib'))
                self.assertTrue((fake_home / 'my-lib/AGENTS.md').is_file())
                self.assertEqual(list(other_home.iterdir()), [])

                for cmd in ('init', 'library'):
                    for bad_tilde in ('~otheruser', '~otheruser/lib'):
                        rc_bad, out_bad, err_bad = self.cli(ctx, [cmd, bad_tilde])
                        self.assertEqual(rc_bad, 2)
                        self.assertEqual(out_bad, '')
                        self.assertTrue(err_bad.strip())
                        self.assertEqual(err_bad.strip().count('\n'), 0)

                        slash_bad = ctx.calls['slash']['handler'](f'{cmd} {bad_tilde}')
                        self.assertTrue(slash_bad.startswith('componentize exit=2\n'), slash_bad)
                        self.assertEqual(list(other_home.iterdir()), [])

    def test_fresh_library_passes_the_real_gate_end_to_end(self):
        ctx = self.register()
        root = self.base / 'lib'
        self.assertEqual(self.cli(ctx, ['init', str(root)])[0], 0)
        runner = ('import sys\n\n\ndef main(argv: list[str] | None = None) -> int:\n'
                  '    print("hello")\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n')
        (root / 'scripts/hello.py').write_text(runner)
        (root / 'scripts/hello.prev.py').write_text(runner)
        (root / 'components/greet.py').write_text(
            'def greet(name: str) -> dict:\n'
            '    return {"ok": True, "status": "pass", "error": None, "findings": [], "text": "hello " + name}\n')
        (root / 'components/tests/test_greet.py').write_text(
            'import unittest\n\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertTrue(True)\n')
        with open(root / 'components/COMPONENTS.md', 'a') as handle:
            handle.write('- `greet.greet(name)` — builds a greeting — returns `{ok,status,error,findings,text}`.\n')
        py = sys.executable
        (root / 'specs/hello.json').write_text(json.dumps({
            'schema_version': 1, 'name': 'hello', 'kind': 'component',
            'attest': {'kind_reason': 'greeting logic is needed by two separate runners in this library',
                       'reuse': 'COMPONENTS.md was empty in this fresh library, nothing to reuse -> new component'},
            'files': [{'path': '../components/greet.py', 'role': 'component'},
                      {'path': '../scripts/hello.py', 'role': 'runner'}],
            'original': '../scripts/hello.prev.py',
            'golden': {'argv': [py, 'scripts/hello.py'], 'cwd': '..', 'env': {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'},
                       'compare': ['stdout', 'rc'], 'isolated': True},
            'tests': {'argv': [py, '-B', '-m', 'unittest', 'components/tests/test_greet.py'], 'cwd': '..'},
            'inputs': ['../components/tests/test_greet.py']}))
        steps = (['begin', 'hello', '--manifest', str(root / 'specs/hello.json')],
                 ['attest', 'hello', 'plan', 'greet component plus thin hello runner, one public function'],
                 ['attest', 'hello', 'review', 'reviewed by test author: approve, no changes requested'],
                 ['check', 'hello'], ['finish', 'hello'], ['status', 'hello'])
        out = ''
        with in_dir(self.base):  # not the library: the saved setting must select it
            for argv in steps:
                rc, out, err = self.cli(ctx, argv)
                self.assertEqual(rc, 0, f'{argv}\n{out}\n{err}')
        self.assertIn('componentize status hello: OK', out)
        self.assertTrue((root / '.componentize/hello').is_dir())


if __name__ == '__main__':
    unittest.main()
