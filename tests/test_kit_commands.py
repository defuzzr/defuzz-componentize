"""Project kit commands (list, show, apply, check), JSON contract, and golden fixtures."""
import argparse
import contextlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import unittest.mock

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DIR = ROOT / 'tests/contract'
NAME = 'componentize_plugin_kit_test'
# Every kit shipped in the plugin; adding a kit must not require editing this test.
KIT_IDS = tuple(sorted(p.parent.name for p in (ROOT / 'vendor/kits').glob('*/kit.json')))
assert {'component-library', 'game-assets', 'video-production'} <= set(KIT_IDS)
REFRESH_CMD = (
    'env REFRESH_KIT_CONTRACT=1 PYTHONDONTWRITEBYTECODE=1 '
    f'{sys.executable} -B -m pytest {Path(__file__).resolve()} -k contract -q -p no:cacheprovider'
)


def load_from(plugin_root, pkg_name=NAME):
    spec = importlib.util.spec_from_file_location(
        pkg_name, plugin_root / '__init__.py', submodule_search_locations=[str(plugin_root)]
    )
    assert spec is not None and spec.loader is not None
    for key in [k for k in sys.modules if k == pkg_name or k.startswith(pkg_name + '.')]:
        del sys.modules[key]
    module = importlib.util.module_from_spec(spec)
    sys.modules[pkg_name] = module
    spec.loader.exec_module(module)
    lib = importlib.import_module(pkg_name + '.library_setup')
    kit = importlib.import_module(pkg_name + '.kit_commands')
    return module, lib, kit


def snapshot(root):
    if not Path(root).exists():
        return {}
    return {
        str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None)
        for p in sorted(Path(root).rglob('*'))
    }


def generate_contract_outputs(kit_mod, base_dir, home_dir):
    target = base_dir / 'contract-video'
    list_res = kit_mod.run(['list', '--json'], cwd=str(base_dir), home=str(home_dir))
    show_res = kit_mod.run(['show', 'video-production', '--json'], cwd=str(base_dir), home=str(home_dir))
    apply_res = kit_mod.run(['apply', 'video-production', str(target), '--json'], cwd=str(base_dir), home=str(home_dir))
    check_res = kit_mod.run(['check', 'video-production', str(target), '--json'], cwd=str(base_dir), home=str(home_dir))
    assert list_res['exit'] == 0 and list_res['stderr'] == '', list_res
    assert show_res['exit'] == 0 and show_res['stderr'] == '', show_res
    assert apply_res['exit'] == 0 and apply_res['stderr'] == '', apply_res
    assert check_res['exit'] == 0 and check_res['stderr'] == '', check_res
    return {
        'list.json': list_res['stdout'],
        'show.json': show_res['stdout'],
        'apply.json': apply_res['stdout'].replace(str(target), '/ROOT'),
        'check.json': check_res['stdout'].replace(str(target), '/ROOT'),
    }


class Context:
    def __init__(self, settings=None, can_save=True):
        self.calls, self.settings = {}, dict(settings or {})
        if can_save:
            self.set_config = self.settings.__setitem__

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_cli_command(self, **kw):
        self.calls['cli'] = kw

    def register_command(self, **kw):
        self.calls['slash'] = kw

    def register_skill(self, **kw):
        self.calls['skill'] = kw


@contextlib.contextmanager
def in_dir(path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


class KitCommandsTests(unittest.TestCase):
    def setUp(self):
        self.plugin, self.lib, self.kit = load_from(ROOT)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(os.path.realpath(self.tmp.name))
        self.home = self.base / 'fake-home'
        self.home.mkdir()
        env_patch = unittest.mock.patch.dict(
            os.environ,
            {'HOME': str(self.home), 'HERMES_HOME': str(self.home / '.hermes')},
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def run_kit(self, argv, cwd=None, home=None):
        return self.kit.run(
            list(argv),
            cwd=str(self.base if cwd is None else cwd),
            home=str(self.home if home is None else home),
        )

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

    def assert_apply_check_error_shape(self, res, expected_kit=None, expected_root=None):
        self.assertEqual(res['exit'], 2, res)
        self.assertEqual(res['stderr'], '', res)
        self.assertTrue(res['stdout'].endswith('\n'), res)
        self.assertEqual(res['stdout'].strip().count('\n') >= 0, True)
        data = json.loads(res['stdout'])
        self.assertEqual(
            set(data),
            {'schema', 'ok', 'status', 'error', 'findings', 'root', 'kit', 'items', 'declared_dirs', 'placement_rules'},
        )
        self.assertEqual(data['schema'], 'componentize.kit/1')
        self.assertFalse(data['ok'])
        self.assertEqual(data['status'], 'error')
        self.assertIsInstance(data['error'], str)
        self.assertTrue(data['error'].strip())
        self.assertNotIn('\n', data['error'])
        self.assertIsInstance(data['findings'], list)
        self.assertEqual(data['kit'], expected_kit)
        self.assertEqual(data['root'], expected_root)
        self.assertEqual(data['items'], [])
        self.assertEqual(data['declared_dirs'], [])
        self.assertEqual(data['placement_rules'], [])
        return data

    def test_reuses_loaded_engine(self):
        self.assertIs(self.kit.ENGINE, self.lib.ENGINE)

    def test_list_returns_shipped_kits_with_contract_keys(self):
        res = self.run_kit(['list', '--json'])
        self.assertEqual(res['exit'], 0, res)
        self.assertEqual(res['stderr'], '')
        self.assertTrue(res['stdout'].endswith('\n'))
        data = json.loads(res['stdout'])
        self.assertEqual(set(data), {'schema', 'ok', 'status', 'error', 'findings', 'kits'})
        self.assertEqual(data['schema'], 'componentize.kit/1')
        self.assertTrue(data['ok'])
        self.assertEqual(data['status'], 'pass')
        self.assertIsNone(data['error'])
        self.assertEqual(data['findings'], [])
        ids = [entry['id'] for entry in data['kits']]
        self.assertEqual(ids, list(KIT_IDS))
        for entry in data['kits']:
            self.assertEqual(set(entry), {'id', 'name', 'summary', 'tags', 'version', 'availability'})
            self.assertIsInstance(entry['name'], str)
            self.assertIsInstance(entry['summary'], str)
            self.assertIsInstance(entry['tags'], list)
            self.assertIsInstance(entry['version'], int)

    def test_show_apply_and_check_consistency_for_each_kit(self):
        for kit_id in KIT_IDS:
            with self.subTest(kit_id=kit_id):
                show_res = self.run_kit(['show', kit_id, '--json'])
                self.assertEqual(show_res['exit'], 0, show_res)
                self.assertEqual(show_res['stderr'], '')
                shown = json.loads(show_res['stdout'])
                self.assertEqual(
                    set(shown),
                    {
                        'schema', 'ok', 'status', 'error', 'findings',
                        'kit', 'declared_dirs', 'placement_rules', 'tree', 'agents_block',
                    },
                )
                self.assertEqual(shown['schema'], 'componentize.kit/1')
                self.assertTrue(shown['ok'])
                self.assertEqual(shown['status'], 'pass')
                self.assertIsNone(shown['error'])
                self.assertEqual(shown['findings'], [])
                self.assertEqual(set(shown['kit']), {'id', 'name', 'summary', 'tags', 'version', 'availability'})
                self.assertEqual(shown['kit']['id'], kit_id)

                if shown['kit']['availability'] == 'coming_soon':
                    continue  # refusal and no-write behavior covered in test_kit_availability

                # Built-in project variable is literal 'project' for show; if the folder is also named 'project',
                # apply produces the exact same declared_dirs, placement_rules, tree paths, and AGENTS.md block.
                target = self.base / kit_id / 'project'
                empty_check = self.run_kit(['check', kit_id, str(target), '--json'])
                self.assertEqual(empty_check['exit'], 1, empty_check)
                empty_data = json.loads(empty_check['stdout'])
                self.assertEqual(empty_data['schema'], 'componentize.kit/1')
                self.assertFalse(empty_data['ok'])
                self.assertEqual(empty_data['status'], 'fail')
                self.assertFalse(target.exists())

                apply_res = self.run_kit(['apply', kit_id, str(target), '--json'])
                self.assertEqual(apply_res['exit'], 0, apply_res)
                self.assertEqual(apply_res['stderr'], '')
                applied = json.loads(apply_res['stdout'])
                self.assertEqual(
                    set(applied),
                    {
                        'schema', 'ok', 'status', 'error', 'findings',
                        'root', 'kit', 'items', 'declared_dirs', 'placement_rules',
                    },
                )
                self.assertEqual(applied['schema'], 'componentize.kit/1')
                self.assertTrue(applied['ok'])
                self.assertEqual(applied['status'], 'pass')
                self.assertEqual(applied['kit'], kit_id)
                self.assertEqual(applied['root'], str(target))
                self.assertEqual({item['state'] for item in applied['items']}, {'created'})
                self.assertEqual(applied['declared_dirs'], shown['declared_dirs'])
                self.assertEqual(applied['placement_rules'], shown['placement_rules'])

                for rel in shown['tree']:
                    if rel.endswith('/'):
                        self.assertTrue((target / rel.rstrip('/')).is_dir(), rel)
                    else:
                        self.assertTrue((target / rel).is_file(), rel)
                agents_text = (target / 'AGENTS.md').read_text(encoding='utf-8')
                self.assertIn(shown['agents_block'], agents_text)

                check_res = self.run_kit(['check', kit_id, str(target), '--json'])
                self.assertEqual(check_res['exit'], 0, check_res)
                checked = json.loads(check_res['stdout'])
                self.assertEqual(checked['schema'], 'componentize.kit/1')
                self.assertTrue(checked['ok'])
                self.assertEqual(checked['status'], 'pass')
                self.assertEqual(checked['declared_dirs'], shown['declared_dirs'])
                self.assertEqual(checked['placement_rules'], shown['placement_rules'])

                before = snapshot(target)
                again_res = self.run_kit(['apply', kit_id, str(target), '--json'])
                self.assertEqual(again_res['exit'], 0, again_res)
                again = json.loads(again_res['stdout'])
                self.assertEqual({item['state'] for item in again['items']}, {'kept'})
                self.assertEqual(snapshot(target), before)

    def test_error_cases_exit_2_with_stable_shape_and_write_nothing(self):
        real_dir = self.base / 'real-target'
        real_dir.mkdir()
        symlink_dir = self.base / 'symlink-dir'
        symlink_dir.symlink_to(real_dir)
        never_created = self.base / 'never-created'

        # Unknown kit and bad IDs on apply/check/show
        for bad_id in ('no-such-kit', '../x', 'Video', 'a\n'):
            with self.subTest(bad_id=bad_id):
                res = self.run_kit(['apply', bad_id, str(never_created), '--json'])
                data = self.assert_apply_check_error_shape(res, expected_kit=bad_id, expected_root=str(never_created))
                for kid in KIT_IDS:
                    self.assertIn(kid, data['error'])
                self.assertFalse(never_created.exists())

                chk = self.run_kit(['check', bad_id, str(never_created), '--json'])
                self.assert_apply_check_error_shape(chk, expected_kit=bad_id, expected_root=str(never_created))
                self.assertFalse(never_created.exists())

                shw = self.run_kit(['show', bad_id, '--json'])
                self.assertEqual(shw['exit'], 2)
                shw_data = json.loads(shw['stdout'])
                self.assertEqual(shw_data['schema'], 'componentize.kit/1')
                self.assertFalse(shw_data['ok'])
                self.assertEqual(shw_data['status'], 'error')
                for kid in KIT_IDS:
                    self.assertIn(kid, shw_data['error'])

        # Missing DIR
        res_missing_dir = self.run_kit(['apply', 'video-production', '--json'])
        self.assert_apply_check_error_shape(res_missing_dir, expected_kit='video-production', expected_root=None)

        chk_missing_dir = self.run_kit(['check', 'video-production', '--json'])
        self.assert_apply_check_error_shape(chk_missing_dir, expected_kit='video-production', expected_root=None)

        # Extra positional
        res_extra = self.run_kit(['apply', 'video-production', str(never_created), 'extra', '--json'])
        self.assert_apply_check_error_shape(res_extra, expected_kit='video-production', expected_root=str(never_created))
        self.assertFalse(never_created.exists())

        # Malformed --set
        for bad_set in (['--set'], ['--set', 'noequals'], ['--set', '=val']):
            with self.subTest(bad_set=bad_set):
                res_set = self.run_kit(['apply', 'component-library', str(never_created), *bad_set, '--json'])
                self.assert_apply_check_error_shape(
                    res_set, expected_kit='component-library', expected_root=str(never_created)
                )
                self.assertFalse(never_created.exists())

        # Unknown option (including a dash token in ID or DIR position)
        for bad_opt_argv, exp_kit, exp_root in (
            (['apply', 'video-production', str(never_created), '--unknown', '--json'], None, None),
            (['apply', '-bad-id', str(never_created), '--json'], None, None),
            (['apply', 'video-production', '-bad-dir', '--json'], None, None),
            (['check', '-bad-id', str(never_created), '--json'], None, None),
        ):
            with self.subTest(bad_opt_argv=bad_opt_argv):
                res_opt = self.run_kit(bad_opt_argv)
                self.assert_apply_check_error_shape(res_opt, expected_kit=exp_kit, expected_root=exp_root)
                self.assertFalse(never_created.exists())

        # DIR equal to fake home
        res_home = self.run_kit(['apply', 'video-production', str(self.home), '--json'])
        self.assert_apply_check_error_shape(res_home, expected_kit='video-production', expected_root=str(self.home))
        self.assertEqual(list(self.home.iterdir()), [])

        # DIR that is a symlink
        res_sym = self.run_kit(['apply', 'video-production', str(symlink_dir), '--json'])
        self.assert_apply_check_error_shape(res_sym, expected_kit='video-production', expected_root=str(symlink_dir))
        self.assertEqual(list(real_dir.iterdir()), [])

        # Unsafe --set variable in path (component-library: --set components=../x)
        res_unsafe_var = self.run_kit(
            ['apply', 'component-library', str(never_created), '--set', 'components=../x', '--json']
        )
        self.assert_apply_check_error_shape(
            res_unsafe_var, expected_kit='component-library', expected_root=str(never_created)
        )
        self.assertFalse(never_created.exists())

    def test_filesystem_errors_on_apply_and_check_return_exit_2(self):
        afile = self.base / 'afile'
        afile.write_text('regular file\n', encoding='utf-8')
        under_file = afile / 'sub'
        for cmd in ('apply', 'check'):
            res_json = self.run_kit([cmd, 'video-production', str(under_file), '--json'])
            self.assert_apply_check_error_shape(res_json, expected_kit='video-production', expected_root=str(under_file))
            res_human = self.run_kit([cmd, 'video-production', str(under_file)])
            self.assertEqual(res_human['exit'], 2, res_human)
            self.assertEqual(res_human['stderr'], '', res_human)
            self.assertTrue(
                res_human['stdout'].startswith(f'componentize kit {cmd} video-production {under_file}: ERROR\n'),
                res_human['stdout'],
            )
            self.assertIn('error:', res_human['stdout'])
        self.assertEqual(afile.read_text(encoding='utf-8'), 'regular file\n')

        if os.geteuid() != 0:
            noperm = self.base / 'noperm'
            noperm.mkdir()
            noperm.chmod(0o000)
            try:
                under_noperm = noperm / 'p'
                for cmd in ('apply', 'check'):
                    res_json = self.run_kit([cmd, 'video-production', str(under_noperm), '--json'])
                    self.assert_apply_check_error_shape(
                        res_json, expected_kit='video-production', expected_root=str(under_noperm)
                    )
                    res_human = self.run_kit([cmd, 'video-production', str(under_noperm)])
                    self.assertEqual(res_human['exit'], 2, res_human)
                    self.assertEqual(res_human['stderr'], '', res_human)
                    self.assertTrue(
                        res_human['stdout'].startswith(
                            f'componentize kit {cmd} video-production {under_noperm}: ERROR\n'
                        ),
                        res_human['stdout'],
                    )
                    self.assertIn('error:', res_human['stdout'])
            finally:
                noperm.chmod(0o755)
            self.assertEqual(list(noperm.iterdir()), [])

    def test_tilde_expansion_uses_given_home_only(self):
        fake_real = self.base / 'fake-real-home'
        fake_real.mkdir()
        with unittest.mock.patch('os.path.expanduser', side_effect=lambda p: p.replace('~', str(fake_real), 1)):
            res_tilde = self.run_kit(['apply', 'video-production', '~', '--json'])
            self.assert_apply_check_error_shape(res_tilde, expected_kit='video-production', expected_root=str(self.home))
            self.assertEqual(list(self.home.iterdir()), [])
            self.assertEqual(list(fake_real.iterdir()), [])

            res_tilde_human = self.run_kit(['apply', 'video-production', '~'])
            self.assertEqual(res_tilde_human['exit'], 2, res_tilde_human)
            self.assertEqual(res_tilde_human['stderr'], '')
            self.assertIn(f'componentize kit apply video-production {self.home}: ERROR', res_tilde_human['stdout'])
            self.assertEqual(list(self.home.iterdir()), [])
            self.assertEqual(list(fake_real.iterdir()), [])

            res_sub = self.run_kit(['apply', 'video-production', '~/proj', '--json'])
            self.assertEqual(res_sub['exit'], 0, res_sub)
            sub_data = json.loads(res_sub['stdout'])
            expected_proj = self.home / 'proj'
            self.assertEqual(sub_data['root'], str(expected_proj))
            self.assertTrue((expected_proj / 'AGENTS.md').is_file())
            self.assertEqual(list(fake_real.iterdir()), [])

            for bad_tilde in ('~someone', '~someone/x'):
                for cmd in ('apply', 'check'):
                    res_user_json = self.run_kit([cmd, 'video-production', bad_tilde, '--json'])
                    self.assert_apply_check_error_shape(res_user_json, expected_kit='video-production', expected_root=None)
                    res_user_human = self.run_kit([cmd, 'video-production', bad_tilde])
                    self.assertEqual(res_user_human['exit'], 2, res_user_human)
                    self.assertEqual(res_user_human['stdout'], '')
                    self.assertTrue(res_user_human['stderr'].startswith('componentize kit: '), res_user_human['stderr'])
                    self.assertEqual(res_user_human['stderr'].strip().count('\n'), 0)
                    self.assertEqual(list(fake_real.iterdir()), [])

    def test_human_mode_usage_and_unknown_kit_errors_are_single_stderr_line(self):
        never_created = self.base / 'never-human'
        cases = [
            [],
            ['bogus'],
            ['list', 'extra'],
            ['list', '--set', 'a=b'],
            ['list', '--unknown'],
            ['show'],
            ['show', 'video-production', 'extra'],
            ['show', 'no-such-kit'],
            ['show', '../bad'],
            ['show', 'component-library', '--set', 'bad'],
            ['show', 'component-library', '--unknown'],
            ['apply'],
            ['apply', 'video-production'],
            ['apply', 'video-production', str(never_created), 'extra'],
            ['apply', 'no-such-kit', str(never_created)],
            ['apply', '../bad', str(never_created)],
            ['apply', '-bad-id', str(never_created)],
            ['apply', 'video-production', '-bad-dir'],
            ['apply', 'video-production', str(never_created), '--set', 'bad'],
            ['check'],
            ['check', 'video-production'],
            ['check', 'no-such-kit', str(never_created)],
            ['check', '../bad', str(never_created)],
            ['check', '-bad-id', str(never_created)],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                res = self.run_kit(argv)
                self.assertEqual(res['exit'], 2, res)
                self.assertEqual(res['stdout'], '', res)
                self.assertTrue(res['stderr'].startswith('componentize kit: '), res)
                self.assertTrue(res['stderr'].endswith('\n'), res)
                self.assertEqual(res['stderr'].strip().count('\n'), 0, res)
        self.assertFalse(never_created.exists())

    def test_subcommand_and_show_set_error_gaps(self):
        for argv in ([], ['--json'], ['unknown'], ['unknown', '--json']):
            res = self.run_kit(argv)
            self.assertEqual(res['exit'], 2, res)
            if '--json' in argv:
                self.assertEqual(res['stderr'], '')
                data = json.loads(res['stdout'])
                self.assertEqual(set(data), {'schema', 'ok', 'status', 'error', 'findings'})
                self.assertFalse(data['ok'])
                self.assertEqual(data['status'], 'error')
            else:
                self.assertEqual(res['stdout'], '')
                self.assertTrue(res['stderr'].startswith('componentize kit: '))

        for bad_set in (['--set'], ['--set', 'noequals'], ['--set', '=val']):
            res_json = self.run_kit(['show', 'component-library', *bad_set, '--json'])
            self.assertEqual(res_json['exit'], 2, res_json)
            data = json.loads(res_json['stdout'])
            self.assertEqual(set(data), {'schema', 'ok', 'status', 'error', 'findings'})
            self.assertEqual(data['status'], 'error')
            res_human = self.run_kit(['show', 'component-library', *bad_set])
            self.assertEqual(res_human['exit'], 2, res_human)
            self.assertEqual(res_human['stdout'], '')
            self.assertTrue(res_human['stderr'].startswith('componentize kit: '))

    def test_set_variable_resolves_in_show_apply_and_check(self):
        target = self.base / 'custom-lib'
        show_res = self.run_kit(['--json', 'show', 'component-library', '--set', 'components=my_mods'])
        self.assertEqual(show_res['exit'], 0, show_res)
        shown = json.loads(show_res['stdout'])
        self.assertIn('my_mods/COMPONENTS.md', shown['tree'])

        apply_res = self.run_kit(['apply', 'component-library', str(target), '--set', 'components=my_mods', '--json'])
        self.assertEqual(apply_res['exit'], 0, apply_res)
        self.assertTrue((target / 'my_mods/COMPONENTS.md').is_file())

        check_res = self.run_kit(['check', 'component-library', str(target), '--set', 'components=my_mods', '--json'])
        self.assertEqual(check_res['exit'], 0, check_res)

    def test_cli_and_slash_handlers_and_no_library_needed(self):
        ctx = self.register()
        self.assertEqual(ctx.settings, {})
        with in_dir(self.base):
            rc, cli_out, cli_err = self.cli(ctx, ['kit', 'list', '--json'])
            self.assertEqual(rc, 0, cli_err)
            self.assertEqual(cli_err, '')

            rc2, cli_out2, cli_err2 = self.cli(
                ctx, ['--json', '--tools-root', '/ignored', '--state-root', '/ignored2', 'kit', 'list']
            )
            self.assertEqual(rc2, 0, cli_err2)
            self.assertEqual(cli_out2, cli_out)

            slash_out = ctx.calls['slash']['handler']('kit list --json')
            self.assertTrue(slash_out.startswith('componentize exit=0\n'), slash_out)
            slash_json = slash_out.split('\n', 1)[1]
            self.assertEqual(json.loads(slash_json), json.loads(cli_out))

            rc_show, show_out, show_err = self.cli(ctx, ['kit', 'show', 'web-app', '--json'])
            self.assertEqual(rc_show, 0, show_err)
            slash_show = ctx.calls['slash']['handler']('kit show web-app --json')
            self.assertTrue(slash_show.startswith('componentize exit=0\n'), slash_show)
            self.assertEqual(json.loads(slash_show.split('\n', 1)[1]), json.loads(show_out))

            target = self.base / 'via-cli' / 'proj'
            rc_apply, apply_out, _ = self.cli(ctx, ['kit', 'apply', 'web-app', str(target), '--json'])
            self.assertEqual(rc_apply, 0, apply_out)
            self.assertTrue((target / 'AGENTS.md').is_file())
            slash_target = self.base / 'via-slash' / 'proj'
            slash_apply = ctx.calls['slash']['handler'](f'kit apply web-app {slash_target} --json')
            self.assertTrue(slash_apply.startswith('componentize exit=0\n'), slash_apply)
            cli_apply_env = json.loads(apply_out)
            slash_apply_env = json.loads(slash_apply.split('\n', 1)[1])
            cli_apply_env['root'] = '/NORM'
            slash_apply_env['root'] = '/NORM'
            self.assertEqual(slash_apply_env, cli_apply_env)

            rc_check, check_out, _ = self.cli(ctx, ['kit', 'check', 'web-app', str(target), '--json'])
            self.assertEqual(rc_check, 0, check_out)
            slash_check = ctx.calls['slash']['handler'](f'kit check web-app {slash_target} --json')
            self.assertTrue(slash_check.startswith('componentize exit=0\n'), slash_check)
            cli_check_env = json.loads(check_out)
            slash_check_env = json.loads(slash_check.split('\n', 1)[1])
            cli_check_env['root'] = '/NORM'
            slash_check_env['root'] = '/NORM'
            self.assertEqual(slash_check_env, cli_check_env)
            self.assertEqual(ctx.settings, {})

    def test_human_output_smoke(self):
        list_res = self.run_kit(['list'])
        self.assertEqual(list_res['exit'], 0)
        self.assertEqual(list_res['stderr'], '')
        for kid in KIT_IDS:
            self.assertIn(kid, list_res['stdout'])

        show_res = self.run_kit(['show', 'video-production'])
        self.assertEqual(show_res['exit'], 0)
        self.assertIn('Video production', show_res['stdout'])
        self.assertIn('assets/canon/CANON.md', show_res['stdout'])
        self.assertIn('<!-- kit:video-production v2 begin', show_res['stdout'])

        target = self.base / 'human-vid'
        apply_res = self.run_kit(['apply', 'video-production', str(target)])
        self.assertEqual(apply_res['exit'], 0)
        self.assertTrue(
            apply_res['stdout'].startswith(f'componentize kit apply video-production {target}: PASS\n'),
            apply_res['stdout'],
        )
        self.assertIn('created', apply_res['stdout'])

        check_res = self.run_kit(['check', 'video-production', str(target)])
        self.assertEqual(check_res['exit'], 0)
        self.assertTrue(
            check_res['stdout'].startswith(f'componentize kit check video-production {target}: PASS\n'),
            check_res['stdout'],
        )

        bad_res = self.run_kit(['apply', 'video-production'])
        self.assertEqual(bad_res['exit'], 2)
        self.assertEqual(bad_res['stdout'], '')
        self.assertTrue(bad_res['stderr'].startswith('componentize kit:'), bad_res['stderr'])

    def test_plugin_copied_alone_lists_kits_and_warns_on_broken_kit(self):
        standalone = self.base / 'standalone-plugin'
        shutil.copytree(ROOT, standalone)
        _plugin, _lib, standalone_kit = load_from(standalone, pkg_name='componentize_standalone_kit_test')
        res = standalone_kit.run(['list', '--json'], cwd=str(self.base), home=str(self.home))
        self.assertEqual(res['exit'], 0, res)
        data = json.loads(res['stdout'])
        self.assertEqual([k['id'] for k in data['kits']], list(KIT_IDS))

        # Add an unreadable kit and an invalid kit in the standalone copy; list stays pass with warn findings.
        broken_dir = standalone / 'vendor/kits/broken-kit'
        broken_dir.mkdir()
        (broken_dir / 'kit.json').write_text('{not valid json', encoding='utf-8')
        invalid_dir = standalone / 'vendor/kits/invalid-kit'
        invalid_dir.mkdir()
        (invalid_dir / 'kit.json').write_text(json.dumps({'schema_version': 99}), encoding='utf-8')

        res_warn = standalone_kit.run(['list', '--json'], cwd=str(self.base), home=str(self.home))
        self.assertEqual(res_warn['exit'], 0, res_warn)
        warn_data = json.loads(res_warn['stdout'])
        self.assertTrue(warn_data['ok'])
        self.assertEqual(warn_data['status'], 'pass')
        self.assertEqual([k['id'] for k in warn_data['kits']], list(KIT_IDS))
        self.assertEqual(len(warn_data['findings']), 2)
        self.assertTrue(all(f['severity'] == 'warn' for f in warn_data['findings']))

    def test_corrupted_component_library_kit_still_lets_kit_list_warn_and_library_report_error(self):
        standalone = self.base / 'standalone-corrupt-complib'
        shutil.copytree(ROOT, standalone)
        (standalone / 'vendor/kits/component-library/kit.json').write_text('{corrupt json', encoding='utf-8')
        standalone_plugin, standalone_lib, standalone_kit = load_from(
            standalone, pkg_name='componentize_corrupt_complib_test'
        )
        res = standalone_kit.run(['list', '--json'], cwd=str(self.base), home=str(self.home))
        self.assertEqual(res['exit'], 0, res)
        data = json.loads(res['stdout'])
        self.assertTrue(data['ok'])
        self.assertEqual(data['status'], 'pass')
        self.assertEqual([k['id'] for k in data['kits']], [k for k in KIT_IDS if k != 'component-library'])
        self.assertEqual(len(data['findings']), 1)
        self.assertEqual(data['findings'][0]['id'], 'kit-unreadable')
        self.assertEqual(data['findings'][0]['severity'], 'warn')

        lib_res = standalone_lib.init_library(str(self.base / 'broken-lib-target'))
        self.assertEqual(lib_res['status'], 'error')
        self.assertFalse(lib_res['ok'])
        self.assertIn('component-library', lib_res['error'])

        (standalone / 'vendor/kits/component-library/kit.json').write_text('null', encoding='utf-8')
        null_res = standalone_lib.init_library(str(self.base / 'null-lib-target'))
        self.assertEqual(null_res['status'], 'error')
        self.assertIn('must be a JSON object', null_res['error'])
        self.assertFalse((self.base / 'null-lib-target').exists())

    def test_apply_validates_only_requested_kit(self):
        target = self.base / 'single-kit-apply'
        with unittest.mock.patch.object(self.kit.ENGINE, 'validate_kit', wraps=self.kit.ENGINE.validate_kit) as spy:
            res = self.run_kit(['apply', 'video-production', str(target), '--json'])
        self.assertEqual(res['exit'], 0, res)
        self.assertLessEqual(spy.call_count, 2)

    def test_plugin_copied_alone_subprocess_isolated_lists_kits(self):
        import subprocess
        standalone = self.base / 'isolated-plugin'
        shutil.copytree(ROOT, standalone)
        code = (
            "import importlib, importlib.util, pathlib, sys\n"
            "root = pathlib.Path(sys.argv[1])\n"
            "spec = importlib.util.spec_from_file_location('isolated_pkg', root / '__init__.py', submodule_search_locations=[str(root)])\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "sys.modules['isolated_pkg'] = mod\n"
            "spec.loader.exec_module(mod)\n"
            "kit_mod = importlib.import_module('isolated_pkg.kit_commands')\n"
            "res = kit_mod.run(['list', '--json'], cwd=sys.argv[2], home=sys.argv[3])\n"
            "sys.stdout.write(res['stdout'])\n"
            "raise SystemExit(res['exit'])\n"
        )
        proc = subprocess.run(
            [sys.executable, '-I', '-B', '-c', code, str(standalone), str(self.base), str(self.home)],
            cwd=str(self.base),
            env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual([k['id'] for k in data['kits']], list(KIT_IDS))
        # Ensure expand_dir is shared (imported from library_setup, not duplicated in kit_commands)
        self.assertIs(self.kit.expand_dir, self.lib.expand_dir)

    def test_golden_contract_fixtures(self):
        generated = generate_contract_outputs(self.kit, self.base, self.home)
        if os.environ.get('REFRESH_KIT_CONTRACT') == '1':
            CONTRACT_DIR.mkdir(parents=True, exist_ok=True)
            for name, content in generated.items():
                (CONTRACT_DIR / name).write_text(content, encoding='utf-8')
        for name, expected in generated.items():
            path = CONTRACT_DIR / name
            self.assertTrue(
                path.is_file(),
                f'missing contract fixture {path}; refresh with:\n  {REFRESH_CMD}',
            )
            actual = path.read_text(encoding='utf-8')
            self.assertEqual(
                actual,
                expected,
                f'contract fixture {name} drifted; refresh with:\n  {REFRESH_CMD}',
            )

    def test_relative_library_root_setting_is_refused_and_typed_relative_target_uses_cwd(self):
        setting_key = 'plugins.entries.defuzz-componentize.settings.library_root'
        for rel in ('components', './components', '../components', 'nested/lib'):
            with self.subTest(rel=rel):
                resolved, err = self.lib.expand_dir(rel, cwd=str(self.base), home='/home/user')
                self.assertIsNone(resolved)
                self.assertIsInstance(err, str)
                self.assertIn(setting_key, err)
                self.assertIn('absolute', err)
                self.assertIn('~', err)
                self.assertNotIn('\n', err)

        abs_path, abs_err = self.lib.expand_dir('/home/user/components/', cwd=str(self.base), home='/home/user')
        self.assertIsNone(abs_err)
        self.assertEqual(abs_path, '/home/user/components')

        tilde_path, tilde_err = self.lib.expand_dir('~/components', cwd=str(self.base), home='/home/user')
        self.assertIsNone(tilde_err)
        self.assertEqual(tilde_path, '/home/user/components')

        # Even when a valid initialized library exists at <cwd>/rel-lib, a relative library_root setting is refused.
        decoy = self.base / 'rel-lib'
        self.assertEqual(self.lib.init_library(str(decoy))['status'], 'pass')
        ctx_rel = self.register(settings={'library_root': 'rel-lib'})
        with in_dir(self.base):
            rc, out, err = self.cli(ctx_rel, ['library'])
            self.assertEqual(rc, 2)
            self.assertEqual(out, '')
            # The saved value gets the settings page's own sentence, naming the setting.
            self.assertEqual(err, 'componentize library: library_root setting: '
                             'Enter an absolute path starting with /, such as /home/user/components.\n')

            slash_out = ctx_rel.calls['slash']['handler']('library')
            self.assertTrue(slash_out.startswith('componentize exit=2\n'), slash_out)
            self.assertIn('library_root setting: Enter an absolute path', slash_out)

            # Tilde library_root in config is expanded against home and accepted.
            tilde_lib = self.home / 'tilde-lib'
            self.assertEqual(self.lib.init_library(str(tilde_lib))['status'], 'pass')
            with unittest.mock.patch.dict(os.environ, {'HOME': str(self.home)}):
                ctx_tilde = self.register(settings={'library_root': '~/tilde-lib'})
                rc_ok, out_ok, err_ok = self.cli(ctx_tilde, ['library'])
                self.assertEqual(rc_ok, 0, err_ok)
                self.assertIn('PASS', out_ok)

        # A folder TYPED on the command line may be relative: it means "relative to where I ran this".
        typed, typed_err = self.lib.expand_dir('rel-proj', cwd=str(self.base), home='/home/user', typed=True)
        self.assertIsNone(typed_err)
        self.assertEqual(typed, str(self.base / 'rel-proj'))
        applied = self.run_kit(['apply', 'video-production', 'rel-proj'])
        self.assertEqual(applied['exit'], 0, applied)
        self.assertTrue((self.base / 'rel-proj').is_dir())
        self.assertEqual(self.run_kit(['check', 'video-production', 'rel-proj'])['exit'], 0)
        # Without a usable working directory a typed relative path is still refused, never guessed.
        self.assertIsNone(self.lib.expand_dir('rel-proj', cwd=None, home='/home/user', typed=True)[0])
        self.assertIsNone(self.lib.expand_dir('rel-proj', cwd='not-absolute', home='/home/user', typed=True)[0])


def _assert_kit_refused_before_engine(kit_mod, raw_dir, *, cwd, home, expected_root):
    with unittest.mock.patch.object(kit_mod.ENGINE, 'apply_kit', wraps=kit_mod.ENGINE.apply_kit) as spy_apply:
        res_json = kit_mod.run(['apply', 'video-production', raw_dir, '--json'], cwd=str(cwd), home=str(home))
        assert spy_apply.call_count == 0, f'ENGINE.apply_kit ran for {raw_dir!r}'
    assert res_json['exit'] == 2, res_json
    assert res_json['stderr'] == ''
    data = json.loads(res_json['stdout'])
    assert data['schema'] == 'componentize.kit/1'
    assert data['ok'] is False
    assert data['status'] == 'error'
    assert data['kit'] == 'video-production'
    assert data['root'] == str(expected_root)
    assert data['items'] == []
    assert isinstance(data['error'], str) and data['error'].startswith('refusing ')

    res_human = kit_mod.run(['apply', 'video-production', raw_dir], cwd=str(cwd), home=str(home))
    assert res_human['exit'] == 2, res_human
    assert res_human['stderr'] == ''
    assert res_human['stdout'].startswith(f'componentize kit apply video-production {expected_root}: ERROR\n')
    assert '\nerror: refusing ' in res_human['stdout']

    with unittest.mock.patch.object(kit_mod.ENGINE, 'inspect_kit', wraps=kit_mod.ENGINE.inspect_kit) as spy_check:
        res_chk = kit_mod.run(['check', 'video-production', raw_dir, '--json'], cwd=str(cwd), home=str(home))
        assert spy_check.call_count == 0, f'ENGINE.inspect_kit ran for {raw_dir!r}'
    assert res_chk['exit'] == 2, res_chk


def test_kit_apply_refuses_hermes_home_and_subpaths_resolving_symlinks(tmp_path, monkeypatch):
    _plugin, _lib, kit = load_from(ROOT)
    base = Path(os.path.realpath(tmp_path))
    fake_home = base / 'fake-home'
    fake_home.mkdir()
    monkeypatch.setenv('HOME', str(fake_home))
    monkeypatch.delenv('HERMES_HOME', raising=False)

    # 1. Default ~/.hermes (even before ~/.hermes exists, after creation, subpaths, ~ syntax, and symlinked parent)
    dot_hermes = fake_home / '.hermes'
    _assert_kit_refused_before_engine(kit, str(dot_hermes), cwd=base, home=fake_home, expected_root=dot_hermes)
    _assert_kit_refused_before_engine(
        kit, str(dot_hermes / 'plugins' / 'demo'), cwd=base, home=fake_home, expected_root=dot_hermes / 'plugins' / 'demo'
    )
    _assert_kit_refused_before_engine(kit, '~/.hermes', cwd=base, home=fake_home, expected_root=dot_hermes)
    _assert_kit_refused_before_engine(
        kit, '~/.hermes/skills/demo', cwd=base, home=fake_home, expected_root=dot_hermes / 'skills' / 'demo'
    )
    assert not dot_hermes.exists()

    dot_hermes.mkdir()
    link_into_default = base / 'link-into-default-hermes'
    link_into_default.symlink_to(dot_hermes)
    via_link = link_into_default / 'subproj'
    _assert_kit_refused_before_engine(kit, str(via_link), cwd=base, home=fake_home, expected_root=via_link)
    assert list(dot_hermes.iterdir()) == []

    # ~/.hermes itself is a symlink to an external directory; targeting that backing directory is also refused.
    dot_hermes.rmdir()
    backing_default = base / 'backing-default-hermes'
    backing_default.mkdir()
    dot_hermes.symlink_to(backing_default)
    _assert_kit_refused_before_engine(kit, str(backing_default), cwd=base, home=fake_home, expected_root=backing_default)
    _assert_kit_refused_before_engine(
        kit, str(backing_default / 'nested'), cwd=base, home=fake_home, expected_root=backing_default / 'nested'
    )
    assert list(backing_default.iterdir()) == []

    # 2. Explicit HERMES_HOME via monkeypatch (both HERMES_HOME and ~/.hermes remain refused)
    custom_hermes = base / 'custom-hermes-home'
    custom_hermes.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(custom_hermes))

    _assert_kit_refused_before_engine(kit, str(custom_hermes), cwd=base, home=fake_home, expected_root=custom_hermes)
    _assert_kit_refused_before_engine(
        kit, str(custom_hermes / 'sessions' / 'demo'), cwd=base, home=fake_home, expected_root=custom_hermes / 'sessions' / 'demo'
    )
    link_into_custom = base / 'link-into-custom'
    link_into_custom.symlink_to(custom_hermes)
    _assert_kit_refused_before_engine(
        kit, str(link_into_custom / 'demo'), cwd=base, home=fake_home, expected_root=link_into_custom / 'demo'
    )
    # ~/.hermes is still refused when HERMES_HOME points elsewhere.
    _assert_kit_refused_before_engine(
        kit, str(backing_default / 'still-refused'), cwd=base, home=fake_home, expected_root=backing_default / 'still-refused'
    )
    assert list(custom_hermes.iterdir()) == []
    assert list(backing_default.iterdir()) == []

    # 3. Safe project folder (including through a safe symlinked parent) still works.
    real_projects = base / 'real-projects'
    real_projects.mkdir()
    projects_link = base / 'projects-link'
    projects_link.symlink_to(real_projects)
    ok_res = kit.run(['apply', 'component-library', str(projects_link / 'demo'), '--json'], cwd=str(base), home=str(fake_home))
    assert ok_res['exit'] == 0, ok_res
    assert (real_projects / 'demo' / 'AGENTS.md').is_file()


def test_kit_apply_refuses_system_roots_and_restricted_subtrees(tmp_path, monkeypatch):
    _plugin, _lib, kit = load_from(ROOT)
    runtime = importlib.import_module(f'{NAME}._kit_runtime')
    base = Path(os.path.realpath(tmp_path))
    fake_home = base / 'fake-home'
    fake_home.mkdir()
    monkeypatch.setenv('HOME', str(fake_home))
    monkeypatch.setenv('HERMES_HOME', str(fake_home / '.hermes'))

    refused_targets = (
        '/etc', '/etc/componentize-never',
        '/usr', '/usr/local/componentize-never',
        '/bin', '/bin/componentize-never',
        '/sbin', '/sbin/componentize-never',
        '/lib', '/lib32', '/lib64', '/libexec',
        '/boot', '/boot/componentize-never',
        '/dev', '/dev/shm/componentize-never',
        '/proc', '/proc/self/componentize-never',
        '/sys', '/sys/kernel/componentize-never',
        '/var',
        '/opt',
    )
    for target in refused_targets:
        _assert_kit_refused_before_engine(kit, target, cwd=base, home=fake_home, expected_root=target)
    assert not Path('/dev/shm/componentize-never').exists()

    # Symlinked parent pointing into /etc is also refused before vendored code runs.
    etc_link = base / 'etc-link'
    etc_link.symlink_to('/etc')
    _assert_kit_refused_before_engine(
        kit, str(etc_link / 'sub'), cwd=base, home=fake_home, expected_root=etc_link / 'sub'
    )

    # /var and /opt are refused as roots themselves, while dedicated folders beneath them are allowed by the safety check.
    assert runtime.check_safe_target('/var/tmp/dedicated-proj', home=str(fake_home), env={}) is None
    assert runtime.check_safe_target('/opt/dedicated-proj', home=str(fake_home), env={}) is None


if __name__ == '__main__':
    unittest.main()
