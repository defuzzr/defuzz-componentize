"""Swaps during creation and at report time: plain refusals, no leftover folders.

Every case runs under the per-test temp folder the shared conftest pins (HOME,
HERMES_HOME and TMPDIR all point inside it). Swaps are placed between the real
calls by wrapping ``os.mkdir`` for the duration of one command only.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from test_kit_path_safety import load

CHANGED = 'the folder changed while it was being checked'


def _tree(path):
    """Every entry under path, relative, sorted."""
    out = []
    for dirpath, dirnames, filenames in os.walk(path):
        rel = os.path.relpath(dirpath, path)
        for name in dirnames + filenames:
            out.append(os.path.normpath(os.path.join(rel, name)))
    return sorted(out)


class SwapCleanupTests(unittest.TestCase):
    def setUp(self):
        self.plugin, self.runtime, self.kit, self.lib, _bridge = load()
        scratch = Path(tempfile.gettempdir()).resolve()
        # Never run against an unpinned temp folder.
        self.assertNotEqual(str(scratch), os.path.sep)
        self.base = scratch / ('swap-' + os.urandom(6).hex())
        self.home = self.base / 'home'
        self.home.mkdir(parents=True)
        self.work = self.base / 'work'
        self.work.mkdir()

    # -- helpers -----------------------------------------------------------
    def _with_mkdir_hook(self, after_n, action, call):
        """Run call(); after the after_n-th dir_fd mkdir, run action()."""
        real = os.mkdir
        count = {'n': 0, 'fired': False}

        def mkdir(path, *args, **kwargs):
            result = real(path, *args, **kwargs)
            if kwargs.get('dir_fd') is not None:
                count['n'] += 1
                if count['n'] == after_n and not count['fired']:
                    count['fired'] = True
                    action()
            return result

        os.mkdir = mkdir
        try:
            res = call()
        finally:
            os.mkdir = real
        self.assertTrue(count['fired'], count)
        return res

    def _apply(self, path):
        res = self.kit.run(['apply', 'component-library', path, '--json'], cwd=str(self.work), home=str(self.home))
        data = json.loads(res['stdout'])
        return res['exit'], data.get('error') or ''

    def _init(self, path):
        res = self.lib.init_library(path, forbidden=(str(self.home),))
        return (0 if res.get('ok') else 2), res.get('error') or ''

    def _runners(self):
        return (('apply', self._apply), ('init', self._init))

    def _plain(self, err, target):
        self.assertEqual(err, f'refusing {target}: {CHANGED}', err)
        self.assertNotIn('Error', err)
        self.assertNotIn('Errno', err)
        self.assertNotIn('Traceback', err)

    # -- MN1 ---------------------------------------------------------------
    def test_swap_during_creation_removes_only_the_folders_this_call_created(self):
        for label, run in self._runners():
            for variant in ('identity', 'missing'):
                with self.subTest(cmd=label, variant=variant):
                    held = self.work / f'held-{label}-{variant}'
                    held.mkdir()
                    (held / 'keep.txt').write_text('x')
                    moved = Path(str(held) + '-moved')
                    before = _tree(held)
                    target = str(held / 'a' / 'b' / 'c')

                    def swap():
                        os.rename(held, moved)
                        if variant == 'identity':
                            # A different folder now answers at the same path.
                            os.makedirs(target)

                    code, err = self._with_mkdir_hook(3, swap, lambda: run(target))
                    self.assertEqual(code, 2, err)
                    self._plain(err, target)
                    self.assertEqual(_tree(moved), before)

    def test_pre_existing_empty_folder_on_the_path_is_kept(self):
        for label, run in self._runners():
            with self.subTest(cmd=label):
                held = self.work / f'pre-{label}'
                (held / 'pre').mkdir(parents=True)
                moved = Path(str(held) + '-moved')
                before = _tree(held)
                target = str(held / 'pre' / 'a' / 'b' / 'c')

                def swap():
                    os.rename(held, moved)

                code, err = self._with_mkdir_hook(3, swap, lambda: run(target))
                self.assertEqual(code, 2, err)
                self._plain(err, target)
                self.assertEqual(_tree(moved), before)
                self.assertTrue((moved / 'pre').is_dir())

    def test_created_folder_holding_a_foreign_file_is_left_and_counted(self):
        for label, run in self._runners():
            with self.subTest(cmd=label):
                held = self.work / f'foreign-{label}'
                held.mkdir()
                moved = Path(str(held) + '-moved')
                target = str(held / 'a' / 'b' / 'c')

                def swap():
                    os.rename(held, moved)
                    (moved / 'a' / 'b' / 'other.txt').write_text('not ours')

                # Each folder is made under a private name and then renamed into
                # place, so a/b first exists at its name once the third folder is made.
                code, err = self._with_mkdir_hook(3, swap, lambda: run(target))
                self.assertEqual(code, 2, err)
                self.assertTrue(err.startswith(f'refusing {target}: {CHANGED}'), err)
                self.assertIn('2 folders it created were left', err)
                self.assertNotIn('Errno', err)
                self.assertEqual(_tree(moved), ['a', 'a/b', 'a/b/other.txt'])

    def test_refusal_before_creation_still_writes_nothing(self):
        before = _tree(self.base)
        code, err = self._apply(str(self.home))
        self.assertEqual(code, 2, err)
        self.assertEqual(_tree(self.base), before)

    # -- MN2 ---------------------------------------------------------------
    def test_swapped_path_missing_file_or_symlink_gives_the_plain_sentence(self):
        elsewhere = self.work / 'elsewhere'
        elsewhere.mkdir()
        for label, run in self._runners():
            for kind in ('missing', 'file', 'symlink', 'dangling'):
                with self.subTest(cmd=label, kind=kind):
                    held = self.work / f'k-{label}-{kind}'
                    held.mkdir()
                    moved = Path(str(held) + '-moved')
                    target = str(held / 'p')

                    def swap():
                        os.rename(held, moved)
                        if kind == 'file':
                            held.write_text('x')
                        elif kind == 'symlink':
                            held.symlink_to(elsewhere)
                        elif kind == 'dangling':
                            held.symlink_to(self.work / 'nowhere')

                    code, err = self._with_mkdir_hook(1, swap, lambda: run(target))
                    self.assertEqual(code, 2, err)
                    self._plain(err, target)
                    self.assertEqual(_tree(moved), [])
                    self.assertEqual(_tree(elsewhere), [])

    def test_swap_while_verifying_gives_the_plain_sentence(self):
        held = self.work / 'verify-held'
        held.mkdir()
        target = str(held / 'p')
        real = self.runtime._open_existing_prefix

        def opened(resolved):
            result = real(resolved)
            os.rename(held, Path(str(held) + '-moved'))
            return result

        setattr(self.runtime, '_open_existing_prefix', opened)
        try:
            verified, err = self.runtime.verify_target(target, home=str(self.home))
        finally:
            setattr(self.runtime, '_open_existing_prefix', real)
        self.assertIsNone(verified)
        self._plain(err, target)

    # -- MN4 ---------------------------------------------------------------
    def test_containment_identity_check_fires_when_the_path_no_longer_names_the_handle(self):
        root = self.work / 'cr'
        root.mkdir()
        (root / 'AGENTS.md').write_text('x')
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            held = os.fstat(fd)
            result = {'items': [{'path': 'AGENTS.md', 'state': 'created'}]}
            self.assertIsNone(self.runtime.containment_report(fd, held.st_dev, held.st_ino, str(root), result))
            os.rename(root, Path(str(root) + '-moved'))
            root.mkdir()
            err = self.runtime.containment_report(fd, held.st_dev, held.st_ino, str(root), result)
            self.assertIn('the verified directory identity changed', err)
            self.assertIn('checked 1 path(s)', err)
            self.assertIn('does not list files it did not open', err)
            root.rmdir()
            err = self.runtime.containment_report(fd, held.st_dev, held.st_ino, str(root), result)
            self.assertIn('the verified directory identity changed', err)
            self.assertNotIn('Errno', err)
        finally:
            os.close(fd)


if __name__ == '__main__':
    unittest.main()
