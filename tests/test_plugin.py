import argparse
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location('componentize_plugin_test', ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RegistrationTests(unittest.TestCase):
    def test_commands_and_skill_only(self):
        self.assertTrue((ROOT / '__init__.py').is_file(), 'plugin entrypoint missing')
        plugin = load()
        class Context:
            def __init__(self): self.calls = {}
            def get_config(self, key, default=None): return default
            def register_cli_command(self, **kw): self.calls['cli'] = kw
            def register_command(self, **kw): self.calls['slash'] = kw
            def register_skill(self, **kw): self.calls['skill'] = kw
        ctx = Context()
        plugin.register(ctx)
        self.assertEqual(set(ctx.calls), {'cli', 'slash', 'skill'})
        self.assertEqual(plugin.PLUGIN_NAME, 'defuzz-componentize')
        self.assertEqual(ctx.calls['cli']['name'], 'componentize')
        self.assertEqual(ctx.calls['slash']['name'], 'componentize')
        self.assertEqual(ctx.calls['skill']['path'], ROOT / f'skills/{plugin.PLUGIN_NAME}/SKILL.md')
        self.assertTrue(ctx.calls['skill']['path'].is_file())
        parser = argparse.ArgumentParser()
        ctx.calls['cli']['setup_fn'](parser)
        args = parser.parse_args(['attest', 'space name', 'design', 'note with spaces'])
        self.assertEqual(args.componentize_argv, ['attest', 'space name', 'design', 'note with spaces'])
        import contextlib
        import io
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'space name.py'
            target.write_text('def hello(): return 1\n')
            args = parser.parse_args(['--tools-root', tmp, 'survey', str(target), '--json'])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = ctx.calls['cli']['handler_fn'](args)
            self.assertEqual(rc, 0)
            self.assertIn('componentize.survey/1', output.getvalue())
            import shlex
            slash = ctx.calls['slash']['handler']
            self.assertIn('exit=0', slash('survey ' + shlex.quote(str(target))))
            self.assertIn('exit=2', slash("survey 'unterminated"))
            self.assertIn('exit=2', slash('pack'))

    def test_version_cli_slash_and_manifest(self):
        import contextlib
        import io
        import re
        import tempfile
        plugin = load()
        self.assertEqual(getattr(plugin, 'VERSION', None), '0.2.0')
        manifest_text = (ROOT / 'plugin.yaml').read_text()
        match = re.search(r'^version:\s*(\S+)\s*$', manifest_text, re.MULTILINE)
        assert match is not None, 'version field missing in plugin.yaml'
        self.assertEqual(match.group(1), plugin.VERSION)

        class Context:
            def __init__(self): self.calls = {}
            def get_config(self, key, default=None): return default
            def register_cli_command(self, **kw): self.calls['cli'] = kw
            def register_command(self, **kw): self.calls['slash'] = kw
            def register_skill(self, **kw): self.calls['skill'] = kw

        ctx = Context()
        plugin.register(ctx)
        parser = argparse.ArgumentParser()
        ctx.calls['cli']['setup_fn'](parser)

        with tempfile.TemporaryDirectory() as tmp:
            import os
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                args = parser.parse_args(['--version'])
                stdout = io.StringIO()
                stderr = io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    rc = ctx.calls['cli']['handler_fn'](args)
                self.assertEqual(rc, 0)
                self.assertEqual(stdout.getvalue(), 'componentize 0.2.0\n')
                self.assertEqual(stderr.getvalue(), '')

                slash_out = ctx.calls['slash']['handler']('--version')
                self.assertTrue(slash_out.startswith('componentize exit=0\ncomponentize 0.2.0\n'), repr(slash_out))
                self.assertEqual(slash_out, 'componentize exit=0\ncomponentize 0.2.0\n')

                for flag_argv in (
                    ['--json', '--version'],
                    ['--tools-root', tmp, '--version'],
                    ['--state-root', tmp, '--json', '--version'],
                ):
                    flag_args = parser.parse_args(flag_argv)
                    f_stdout, f_stderr = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(f_stdout), contextlib.redirect_stderr(f_stderr):
                        f_rc = ctx.calls['cli']['handler_fn'](flag_args)
                    self.assertEqual(f_rc, 0, flag_argv)
                    self.assertEqual(f_stdout.getvalue(), 'componentize 0.2.0\n', flag_argv)
                    self.assertEqual(f_stderr.getvalue(), '', flag_argv)

                for slash_cmd in (
                    '--json --version',
                    f'--tools-root {tmp} --version',
                    f'--tools-root={tmp} --state-root {tmp} --version',
                ):
                    s_out = ctx.calls['slash']['handler'](slash_cmd)
                    self.assertEqual(s_out, 'componentize exit=0\ncomponentize 0.2.0\n', slash_cmd)

                trailing_slash = ctx.calls['slash']['handler']('status foo --version')
                self.assertNotIn('exit=0', trailing_slash)
                self.assertNotIn('componentize 0.2.0', trailing_slash)

                trailing_args = parser.parse_args(['status', 'foo', '--version'])
                self.assertEqual(trailing_args.componentize_argv, ['status', 'foo', '--version'])
                trailing_stdout = io.StringIO()
                trailing_stderr = io.StringIO()
                with contextlib.redirect_stdout(trailing_stdout), contextlib.redirect_stderr(trailing_stderr):
                    trailing_rc = ctx.calls['cli']['handler_fn'](trailing_args)
                self.assertNotEqual(trailing_rc, 0)
                self.assertNotIn('componentize 0.2.0', trailing_stdout.getvalue() + trailing_stderr.getvalue())
            finally:
                os.chdir(old_cwd)

    def test_manifest_config_schema_labels_and_empty_capabilities(self):
        import re
        manifest_text = (ROOT / 'plugin.yaml').read_text(encoding='utf-8')
        self.assertIn('\nprovides_tools: []\n', manifest_text)
        self.assertIn('\nprovides_hooks: []\n', manifest_text)
        self.assertIn('\npython_dependencies: []\n', manifest_text)
        self.assertIn('label: Component library folder', manifest_text)
        self.assertIn('label: Outer command timeout', manifest_text)
        self.assertRegex(manifest_text, r'library_root:\n\s+type:\s*str\n\s+default:\s*""')
        self.assertRegex(manifest_text, r'timeout:\n\s+type:\s*float\n\s+default:\s*900')


class BridgeTests(unittest.TestCase):
    def test_standalone_vendor_and_bridge(self):
        self.assertTrue((ROOT / 'bridge.py').is_file(), 'bridge missing')
        import importlib
        load()
        bridge = importlib.import_module('componentize_plugin_test.bridge')
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'space name.py'
            target.write_text('def hello():\n    return 42\n')
            result = bridge.run(['survey', str(target), '--json'], cwd=tmp, env=dict(os.environ))
            self.assertEqual(result['exit'], 0, result)
            import json
            self.assertEqual(json.loads(result['stdout'])['files'][0]['path'], str(target))
            self.assertFalse((Path(tmp) / '.componentize').exists())
            manifest = Path(tmp) / 'invalid.json'
            manifest.write_text('{}')
            failure = bridge.run(['begin', 'invalid', '--manifest', str(manifest)], cwd=tmp, env=dict(os.environ))
            self.assertEqual(failure['exit'], 1, failure)
            self.assertIn('manifest invalid', failure['stderr'])
            self.assertEqual(bridge.run(['status', 'missing'], cwd=tmp, env=dict(os.environ))['exit'], 2)
            self.assertEqual(bridge.run(['nonesuch'], cwd=tmp, env=dict(os.environ))['exit'], 2)
            self.assertEqual(bridge.run(['--help'], cwd=tmp, env=dict(os.environ), timeout=0.000001)['exit'], 2)
            self.assertEqual(bridge.run(['--help'], cwd=tmp, env=dict(os.environ), timeout=float('nan'))['exit'], 2)


if __name__ == '__main__':
    unittest.main()
