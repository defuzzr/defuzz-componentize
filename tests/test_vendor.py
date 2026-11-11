import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
# The private source path must not appear.
PRIVATE_PATH = ('To' + 'ols/').encode()


def header_line(rel):
    return f"# vendored copy of the author's {rel} (MIT licence, same as this plugin); do not edit"


def expected_copy(rel, source):
    """The copy refresh_vendor.py must write, derived here independently."""
    if not rel.endswith('.py'):
        return source
    header = (header_line(rel) + '\n').encode()
    if source.startswith(b'#!'):
        first, _, rest = source.partition(b'\n')
        return first + b'\n' + header + rest
    return header + source


def load_refresh():
    spec = importlib.util.spec_from_file_location('refresh_vendor_under_test', ROOT / 'scripts/refresh_vendor.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HeaderWriterTests(unittest.TestCase):
    def test_neutral_header_on_line_one(self):
        out = load_refresh().vendored_bytes('hermes_audit/sample_mod.py', b'x = 1\n')
        self.assertEqual(out, (header_line('hermes_audit/sample_mod.py') + '\nx = 1\n').encode())

    def test_shebang_stays_on_line_one(self):
        out = load_refresh().vendored_bytes('componentize/tool.py', b'#!/usr/bin/env python3\nx = 1\n')
        self.assertEqual(out, ('#!/usr/bin/env python3\n' + header_line('componentize/tool.py') + '\nx = 1\n').encode())

    def test_shebang_in_gate_checked_module_is_refused(self):
        with self.assertRaises(SystemExit) as caught:
            load_refresh().vendored_bytes('hermes_audit/tool.py', b'#!/usr/bin/env python3\nx = 1\n')
        self.assertEqual(caught.exception.code, 2)

    def test_non_python_unchanged(self):
        self.assertEqual(load_refresh().vendored_bytes('kits/a/kit.json', b'{}\n'), b'{}\n')

    def test_vendored_componentize_cli_keeps_shebang(self):
        first, second = (ROOT / 'vendor/componentize/componentize.py').read_bytes().split(b'\n')[:2]
        self.assertEqual(first, b'#!/usr/bin/env python3')
        self.assertEqual(second, header_line('componentize/componentize.py').encode())


class IndexFilterTests(unittest.TestCase):
    SOURCE = (b'# Index\n\n- `kept.a(x)` \xe2\x80\x94 one.\n\n- `gone.b()` \xe2\x80\x94 two.\n'
              b'- `gone.c()` \xe2\x80\x94 three.\n\nIndex format: text.\n\n- `kept.d()` \xe2\x80\x94 four.\n')

    def test_index_keeps_only_vendored_modules_and_notes_it(self):
        out = load_refresh().vendored_bytes('hermes_audit/COMPONENTS.md', self.SOURCE, ['kept']).decode()
        self.assertNotIn('gone.', out)
        self.assertIn('- `kept.a(x)`', out)
        self.assertIn('- `kept.d()`', out)
        self.assertIn('Index format: text.', out)
        self.assertEqual(out.splitlines()[0], '# Index')
        self.assertIn('lists only the modules vendored here (`kept`)', out.splitlines()[2])
        self.assertNotIn('\n\n\n', out)

    def test_index_without_module_list_is_refused(self):
        with self.assertRaises(SystemExit):
            load_refresh().vendored_bytes('hermes_audit/COMPONENTS.md', self.SOURCE)

    def test_vendored_modules_from_manifest(self):
        rels = ['hermes_audit/__init__.py', 'hermes_audit/b.py', 'hermes_audit/a.py', 'componentize/x.py', 'hermes_audit/COMPONENTS.md']
        self.assertEqual(load_refresh().vendored_modules(rels), ['a', 'b'])

    def test_shipped_index_lists_only_shipped_modules(self):
        text = (ROOT / 'vendor/hermes_audit/COMPONENTS.md').read_text()
        shipped = {p.stem for p in (ROOT / 'vendor/hermes_audit').glob('*.py') if p.stem != '__init__'}
        listed = set(re.findall(r'^- `(\w+)\.', text, re.M))
        self.assertTrue(listed)
        self.assertLessEqual(listed, shipped)
        self.assertIn('Filtered copy for this plugin', text)


class VendorTests(unittest.TestCase):
    def test_vendor_files_match_manifest(self):
        manifest = json.loads((ROOT / 'VENDOR.json').read_text())
        self.assertRegex(manifest['source_commit'], r'^[a-f0-9]{40}$')
        for rel, hashes in manifest['files'].items():
            with self.subTest(path=rel):
                vendor = (ROOT / 'vendor' / rel).read_bytes()
                self.assertEqual(hashlib.sha256(vendor).hexdigest(), hashes['vendor_sha256'])
                if rel.endswith('.py'):
                    compile(vendor, rel, 'exec')
                    self.assertNotIn(PRIVATE_PATH, b''.join(vendor.splitlines(keepends=True)[:2]))
                if rel.startswith('hermes_audit/') and rel.endswith('.py'):
                    # The architecture gate reads a vendored header from line 1 only.
                    self.assertEqual(vendor.split(b'\n', 1)[0], header_line(rel).encode())

    def test_manifest_states_origin_and_licence(self):
        manifest = json.loads((ROOT / 'VENDOR.json').read_text())
        self.assertEqual(manifest.get('license'), 'MIT')
        self.assertEqual(manifest.get('origin'), load_refresh().ORIGIN)
        self.assertNotIn(PRIVATE_PATH.decode()[:-1], manifest['origin'])

    def test_refresh_without_source_fails_with_one_line_message(self):
        script = ROOT / 'scripts/refresh_vendor.py'
        self.assertTrue(script.is_file(), 'refresh script missing')
        no_arg = subprocess.run([sys.executable, '-B', str(script)], capture_output=True, text=True, timeout=30)
        self.assertNotEqual(no_arg.returncode, 0)
        self.assertEqual(no_arg.stdout, '')
        lines = no_arg.stderr.strip().splitlines()
        self.assertEqual(len(lines), 1, no_arg.stderr)
        self.assertNotIn('Traceback', no_arg.stderr)
        self.assertIn('--source', lines[0])

    def test_plugin_copied_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            standalone = Path(tmp) / 'plugin'
            shutil.copytree(ROOT, standalone)
            proc = subprocess.run([sys.executable, '-I', '-B', str(standalone / 'vendor/componentize/componentize.py'), '--help'], cwd=tmp, capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('survey', proc.stdout)


if __name__ == '__main__':
    unittest.main()
