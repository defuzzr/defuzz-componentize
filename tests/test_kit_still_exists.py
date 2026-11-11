"""Whether a folder this call made still exists is decided the same way on Linux and macOS.

Linux reports a link count of 0 for a removed folder that is still held open; macOS
(APFS) keeps reporting 2. The cleanup must therefore not depend on the link count:
the folder counts as still there only while the folder it was made in still has an
entry with the same device and inode. These tests make the link count lie the way
macOS does and check the answer.
"""
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_kit_path_safety import load

CHANGED = 'the folder changed while it was being checked'


def _macos_fstat(real_fstat):
    """os.fstat that reports a removed folder the way macOS does (link count 2, not 0)."""
    def fstat(fd):
        info = real_fstat(fd)
        if stat.S_ISDIR(info.st_mode) and info.st_nlink == 0:
            values = list(info[:10])
            values[stat.ST_NLINK] = 2
            return os.stat_result(values)
        return info
    return fstat


class StillExistsTests(unittest.TestCase):
    def setUp(self):
        _plugin, self.runtime, _kit, _lib, _bridge = load()
        scratch = Path(tempfile.gettempdir()).resolve()
        self.base = scratch / ('still-' + os.urandom(6).hex())
        self.base.mkdir()
        self.parent_fd = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.parent_fd)
        (self.base / 'mine').mkdir()
        self.own_fd = os.open(self.base / 'mine', os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.own_fd)
        info = os.fstat(self.own_fd)
        self.dev, self.ino = info.st_dev, info.st_ino
        patcher = mock.patch.object(os, 'fstat', _macos_fstat(os.fstat))
        patcher.start()
        self.addCleanup(patcher.stop)

    def exists(self):
        return self.runtime._still_exists(self.parent_fd, 'mine', self.own_fd, self.dev, self.ino)

    def test_untouched_folder_is_present(self):
        self.assertTrue(self.exists())

    def test_removed_folder_is_gone(self):
        os.rmdir(self.base / 'mine')
        self.assertFalse(self.exists())

    def test_removed_and_recreated_under_the_same_name_is_gone(self):
        os.rmdir(self.base / 'mine')
        (self.base / 'mine').mkdir()
        self.assertFalse(self.exists())

    def test_symlink_at_the_name_to_a_live_folder_is_not_taken_as_it(self):
        os.rmdir(self.base / 'mine')
        other = self.base.parent / (self.base.name + '-other')
        other.mkdir()
        self.addCleanup(os.rmdir, other)
        os.symlink(other, self.base / 'mine')
        self.assertFalse(self.exists())

    def test_renamed_inside_the_same_folder_is_still_present(self):
        os.rename(self.base / 'mine', self.base / 'mine.moved')
        self.assertTrue(self.exists())

    def test_moved_out_of_the_folder_it_was_made_in_is_gone(self):
        away = self.base.parent / (self.base.name + '-away')
        os.rename(self.base / 'mine', away)
        self.addCleanup(os.rmdir, away)
        self.assertFalse(self.exists())


class CleanupWithMacosLinkCountTests(unittest.TestCase):
    """The refusal message after a swap during creation, with the macOS link count."""

    def setUp(self):
        _plugin, self.runtime, _kit, _lib, _bridge = load()
        scratch = Path(tempfile.gettempdir()).resolve()
        self.base = scratch / ('still-run-' + os.urandom(6).hex())
        self.home = self.base / 'home'
        self.home.mkdir(parents=True)
        self.work = self.base / 'work'
        self.work.mkdir()
        patcher = mock.patch.object(os, 'fstat', _macos_fstat(os.fstat))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _create(self, target, on_mkdir):
        verified, err = self.runtime.verify_target(target, home=str(self.home))
        self.assertIsNone(err)
        real = os.mkdir
        count = {'n': 0}

        def mkdir(path, *args, **kwargs):
            result = real(path, *args, **kwargs)
            if kwargs.get('dir_fd') is not None:
                count['n'] += 1
                on_mkdir(count['n'])
            return result

        with mock.patch.object(os, 'mkdir', mkdir):
            try:
                fd, err = self.runtime.create_from_verified(verified)
            finally:
                verified.close()
        if fd is not None:
            os.close(fd)
        return err

    def test_removed_folders_are_not_reported_as_left(self):
        held = self.work / 'plain'
        held.mkdir()
        moved = Path(str(held) + '-moved')
        target = str(held / 'a' / 'b' / 'c')
        err = self._create(target, lambda n: os.rename(held, moved) if n == 3 else None)
        self.assertEqual(err, f'refusing {target}: {CHANGED}')
        self.assertEqual(os.listdir(moved), [])

    def test_a_folder_that_could_not_be_removed_is_still_counted(self):
        held = self.work / 'full'
        held.mkdir()
        moved = Path(str(held) + '-moved')
        target = str(held / 'a' / 'b' / 'c')

        def on_mkdir(n):
            if n == 3:
                os.rename(held, moved)
                (moved / 'a' / 'keep.txt').write_text('x')

        err = self._create(target, on_mkdir)
        self.assertEqual(
            err, f'refusing {target}: {CHANGED}; 1 folder it created was left in place '
            'because they were not empty or could not be removed')
        self.assertEqual(sorted(os.listdir(moved / 'a')), ['keep.txt'])


if __name__ == '__main__':
    unittest.main()
