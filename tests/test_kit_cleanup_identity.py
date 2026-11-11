"""After a refused creation, the cleanup removes only folders this call created.

A folder counts as "created by this call" only through the handle the call opened
when it made it. A different empty folder that another program running as the same
user swaps in under the same name is never removed, and the "left in place" count is
the number of this call's folders that still exist. Every case works under the
per-test temp folder the shared conftest pins.
"""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from test_kit_path_safety import load

CHANGED = 'the folder changed while it was being checked'
_OS_NAMES = ('lstat', 'stat', 'fstat', 'rmdir', 'rename', 'replace', 'open', 'listdir', 'scandir', 'mkdir')


def _dir_inodes(root):
    """{inode: relative path} for every directory under root (root excluded)."""
    out = {}
    if not os.path.isdir(root):
        return out
    for dirpath, dirnames, _files in os.walk(root):
        for name in dirnames:
            path = os.path.join(dirpath, name)
            out[os.lstat(path).st_ino] = os.path.relpath(path, root)
    return out


def _left_count(err):
    marker = ' left in place'
    if marker not in err:
        return 0
    words = err.split('; ')[-1].split()
    return int(words[0])


class CleanupIdentityTests(unittest.TestCase):
    def setUp(self):
        self.plugin, self.runtime, self.kit, self.lib, _bridge = load()
        scratch = Path(tempfile.gettempdir()).resolve()
        self.assertNotEqual(str(scratch), os.path.sep)
        self.base = scratch / ('cleanup-' + os.urandom(6).hex())
        self.home = self.base / 'home'
        self.home.mkdir(parents=True)
        self.work = self.base / 'work'
        self.work.mkdir()

    # -- helpers -----------------------------------------------------------
    def _create(self, target, on_mkdir):
        """verify + create_from_verified; on_mkdir(n) runs after the n-th dir_fd mkdir."""
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

        os.mkdir = mkdir
        try:
            fd, err = self.runtime.create_from_verified(verified)
        finally:
            os.mkdir = real
            verified.close()
        if fd is not None:
            os.close(fd)
        return err

    def _during_cleanup(self, fire_at, action):
        """Patch _remove_created so action() runs after its fire_at-th os call."""
        runtime = self.runtime
        real_remove = runtime._remove_created
        state = {'n': 0, 'fired': False}

        def remove(*args, **kwargs):
            saved = {name: getattr(os, name) for name in _OS_NAMES}

            def wrap(name, fn):
                def inner(*a, **kw):
                    result = fn(*a, **kw)
                    state['n'] += 1
                    if state['n'] == fire_at and not state['fired']:
                        state['fired'] = True
                        for key, value in saved.items():
                            setattr(os, key, value)
                        try:
                            action()
                        finally:
                            for key, value in saved.items():
                                setattr(os, key, wrap(key, value))
                    return result
                return inner

            for name, fn in saved.items():
                setattr(os, name, wrap(name, fn))
            try:
                return real_remove(*args, **kwargs)
            finally:
                for name, fn in saved.items():
                    setattr(os, name, fn)

        runtime._remove_created = remove
        return state, lambda: setattr(runtime, '_remove_created', real_remove)

    def _assert_true_count(self, err, root, foreign):
        mine = [rel for ino, rel in _dir_inodes(root).items() if ino not in foreign]
        self.assertEqual(_left_count(err), len(mine), (err, mine))

    # -- swaps during the cleanup -----------------------------------------
    def test_foreign_folder_swapped_in_during_cleanup_is_never_removed(self):
        fired_any = False
        for fire_at in range(1, 40):
            with self.subTest(fire_at=fire_at):
                held = self.work / f'c{fire_at}'
                held.mkdir()
                moved = Path(str(held) + '-moved')
                target = str(held / 'a' / 'b' / 'c')
                foreign = set()

                def swap():
                    # Rename this call's deepest folder away, put a foreign one in its place.
                    deep = moved / 'a' / 'b'
                    if (deep / 'c').is_dir() and not (deep / 'c').is_symlink():
                        os.rename(deep / 'c', deep / f'c.mine{fire_at}')
                        os.mkdir(deep / 'c')
                        foreign.add(os.lstat(deep / 'c').st_ino)

                state, undo = self._during_cleanup(fire_at, swap)
                try:
                    err = self._create(target, lambda n: os.rename(held, moved) if n == 3 else None)
                finally:
                    undo()
                self.assertTrue(err.startswith(f'refusing {target}: {CHANGED}'), err)
                self.assertNotIn('Errno', err)
                inodes = _dir_inodes(moved)
                for ino in foreign:
                    self.assertIn(ino, inodes, f'foreign folder removed: {err}')
                self._assert_true_count(err, moved, foreign)
                fired_any = fired_any or bool(foreign)
                if not state['fired']:
                    break
        self.assertTrue(fired_any)

    def test_foreign_folder_at_the_name_right_after_mkdir_is_not_taken_as_ours(self):
        held = self.work / 'early'
        held.mkdir()
        moved = Path(str(held) + '-moved')
        target = str(held / 'a' / 'b' / 'c')
        foreign = set()

        def on_mkdir(n):
            if n == 1:
                # Whatever this call just made under the name `a` moves away and a
                # foreign empty folder answers at `a` before the call opens it.
                if (held / 'a').exists():
                    os.rename(held / 'a', held / 'a.mine')
                os.mkdir(held / 'a')
                foreign.add(os.lstat(held / 'a').st_ino)
            if n == 3:
                os.rename(held, moved)

        err = self._create(target, on_mkdir)
        self.assertIsNotNone(err)
        self.assertTrue(err.startswith(f'refusing {target}: {CHANGED}'), err)
        root = moved if moved.exists() else held
        inodes = _dir_inodes(root)
        for ino in foreign:
            self.assertIn(ino, inodes, f'foreign folder removed: {err}')
        self._assert_true_count(err, root, foreign)

    def test_swap_loop_keeps_every_foreign_folder_and_counts_truly(self):
        old_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        self.addCleanup(sys.setswitchinterval, old_interval)
        swapped = 0
        for round_no in range(60):
            held = self.work / f'loop{round_no}'
            held.mkdir()
            moved = Path(str(held) + '-moved')
            target = str(held / 'a' / 'b' / 'c')
            foreign = set()
            stop = threading.Event()

            def racer():
                index = 0
                while not stop.is_set():
                    for deep in (moved / 'a' / 'b', moved / 'a'):
                        leaf = deep / ('c' if deep.name == 'b' else 'b')
                        try:
                            os.rename(leaf, deep / f'{leaf.name}.r{index}')
                            os.mkdir(leaf)
                            foreign.add(os.lstat(leaf).st_ino)
                        except OSError:
                            pass
                        index += 1

            thread = threading.Thread(target=racer, daemon=True)
            thread.start()
            try:
                err = self._create(target, lambda n: os.rename(held, moved) if n == 3 else None)
            finally:
                stop.set()
                thread.join()
            self.assertTrue(err.startswith(f'refusing {target}: {CHANGED}'), err)
            inodes = _dir_inodes(moved)
            for ino in foreign:
                self.assertIn(ino, inodes, f'round {round_no}: foreign folder removed: {err}')
            swapped += len(foreign)
        # The racer really ran against the cleanup.
        self.assertGreater(swapped, 0)
        self.assertIsNotNone(self.runtime._RENAME_NOREPLACE if sys.platform.startswith('linux') else True)

    def test_no_swap_still_removes_everything_it_made(self):
        held = self.work / 'plain'
        held.mkdir()
        moved = Path(str(held) + '-moved')
        target = str(held / 'a' / 'b' / 'c')
        err = self._create(target, lambda n: os.rename(held, moved) if n == 3 else None)
        self.assertEqual(err, f'refusing {target}: {CHANGED}')
        self.assertEqual(sorted(os.listdir(moved)), [])

    def test_success_leaves_no_private_names(self):
        held = self.work / 'ok'
        held.mkdir()
        target = str(held / 'a' / 'b' / 'c')
        err = self._create(target, lambda n: None)
        self.assertIsNone(err)
        self.assertEqual(sorted(_dir_inodes(held).values()), ['a', 'a/b', 'a/b/c'])


if __name__ == '__main__':
    unittest.main()
