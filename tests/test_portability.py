"""Clean-machine portability: version through the host parser, cwd/HOME edge cases, platforms, skill routes."""
import argparse
import contextlib
import io
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_plugin import ROOT, load


class Context:
    def __init__(self): self.calls = {}
    def get_config(self, key, default=None): return default
    def set_config(self, key, value): pass
    def register_cli_command(self, **kw): self.calls['cli'] = kw
    def register_command(self, **kw): self.calls['slash'] = kw
    def register_skill(self, **kw): self.calls['skill'] = kw


def registered():
    plugin = load()
    ctx = Context()
    plugin.register(ctx)
    return plugin, ctx


def host_parser(ctx):
    """Mirror the host: a root parser owning --version/-V (dest 'version'), with the
    plugin attached as a subparser exactly like hermes_cli.main._attach_plugin_cli_command."""
    root = argparse.ArgumentParser(prog='hermes')
    root.add_argument('--version', '-V', action='store_true')
    subparsers = root.add_subparsers(dest='command')
    info = ctx.calls['cli']
    sub = subparsers.add_parser(info['name'], help=info['help'])
    info['setup_fn'](sub)
    sub.set_defaults(func=info['handler_fn'])
    return root


class VersionThroughHostParser(unittest.TestCase):
    def run_host(self, argv):
        plugin, ctx = registered()
        args = host_parser(ctx).parse_args(argv)
        # The host prints its own version and returns whenever args.version is truthy.
        self.assertFalse(args.version, f'{argv}: host --version would shadow the plugin')
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = args.func(args)
        return plugin, rc, out.getvalue()

    def test_dash_dash_version(self):
        plugin, rc, out = self.run_host(['componentize', '--version'])
        self.assertEqual((rc, out), (0, f'componentize {plugin.VERSION}\n'))

    def test_version_subcommand(self):
        plugin, rc, out = self.run_host(['componentize', 'version'])
        self.assertEqual((rc, out), (0, f'componentize {plugin.VERSION}\n'))

    def test_slash_version_subcommand(self):
        plugin, ctx = registered()
        self.assertIn(f'componentize {plugin.VERSION}', ctx.calls['slash']['handler']('version'))


class SlashEdgeCases(unittest.TestCase):
    def test_deleted_cwd_is_one_plain_line(self):
        _plugin, ctx = registered()
        old = os.getcwd()
        gone = tempfile.mkdtemp()
        try:
            os.chdir(gone)
            os.rmdir(gone)
            text = ctx.calls['slash']['handler']('status foo')
        finally:
            os.chdir(old)
        self.assertTrue(text.startswith('componentize exit=2\n'), text)
        body = text.split('\n', 1)[1].strip()
        self.assertEqual(len(body.splitlines()), 1, text)
        self.assertIn('current folder', body)

    def _tilde_apply(self, env_patch, expanduser=None):
        _plugin, ctx = registered()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env_patch):
            old = os.getcwd()
            try:
                os.chdir(tmp)
                patcher = mock.patch('os.path.expanduser', expanduser) if expanduser else contextlib.nullcontext()
                with patcher:
                    text = ctx.calls['slash']['handler']('kit apply web-app ~/tilde_target')
            finally:
                os.chdir(old)
            self.assertFalse(Path(tmp, '~').exists())
        self.assertFalse(Path('/tilde_target').exists())
        self.assertIn('exit=2', text)
        self.assertIn('cannot expand ~ without home', text)
        self.assertNotIn('Traceback', text)

    def test_empty_home(self):
        self._tilde_apply({'HOME': ''})

    def test_no_passwd_entry(self):
        env = dict(os.environ)
        env.pop('HOME', None)
        with mock.patch.dict(os.environ, env, clear=True):
            self._tilde_apply({}, expanduser=lambda p: p)


class ManifestAndSkill(unittest.TestCase):
    def test_platforms_declared(self):
        text = (ROOT / 'plugin.yaml').read_text()
        self.assertRegex(text, r'(?m)^platforms:\s*\[linux\]\s*$')


    def test_skill_omits_review_route_options(self):
        text = (ROOT / 'skills/defuzz-componentize/SKILL.md').read_text()
        self.assertNotRegex(text, r'"option":\s*"deep"')
        self.assertNotIn('option="deep"', text)
        self.assertIn('inside the task entry', ' '.join(text.split()))
        self.assertNotRegex(text, r'(?m)^version:\s*0\.2\.0\s*$')



if __name__ == '__main__':
    unittest.main()
