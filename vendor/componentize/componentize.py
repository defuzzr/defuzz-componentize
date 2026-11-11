#!/usr/bin/env python3
# vendored copy of the author's componentize/componentize.py (MIT licence, same as this plugin); do not edit
"""Thin CLI runner for the componentize workflow.

Exit codes: 0 pass/ok, 1 fail/stale (your work or its inputs changed/are wrong), 2 error (usage, state, lock, tool could not run).
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import json
import math
import os
import platform
import pwd
import re
import secrets
import stat
import sys
import tempfile
from typing import Any, Iterator, NoReturn

_TOOLS_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
# Put the package ahead of this script's directory even when PYTHONPATH
# already contains the repository root farther down the search path.
sys.path.insert(0, _TOOLS_ROOT)

# Discovery must not create local bytecode caches, even without python -B.
# Set before loading project modules; child golden/test processes are unaffected.
sys.dont_write_bytecode = True

from componentize import _io, _state, _plugins  # noqa: E402
from hermes_audit import architecture_gate, componentize_evidence, componentize_survey  # noqa: E402

__version__ = "1.3.0"

_OUTPUT: dict[str, bool] = {"json": False}


def _now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format ending with 'Z'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dumps_json(obj: Any) -> str:
    return json.dumps(obj, allow_nan=False)


def _err(msg: str, *, code: int = 2, extra: dict[str, Any] | None = None) -> int:
    if _OUTPUT["json"]:
        print(_dumps_json({"ok": False, "exit": code, "error": msg, **(extra or {})}))
    else:
        print(_show_block(msg), file=sys.stderr)
    return code


def _write_json_or_err(path: str, obj: Any) -> str | None:
    res = _write_state_atomic(path, _state.format_json(obj))
    return None if res["ok"] else str(res["error"])


def _component_dir(tools_root: str) -> str:
    """Folder holding the index: `components/` once its COMPONENTS.md exists, else legacy `hermes_audit/`."""
    try:
        mode = os.lstat(os.path.join(tools_root, "components", "COMPONENTS.md")).st_mode
    except OSError:
        return "hermes_audit"
    return "components" if stat.S_ISREG(mode) else "hermes_audit"


def _index_path(tools_root: str) -> str:
    return os.path.join(tools_root, _component_dir(tools_root), "COMPONENTS.md")


def _index_missing_text(tools_root: str) -> str:
    """One plain clause naming the index a component job needs (PROCEDURE.md step 8)."""
    expected = os.path.join(tools_root, "components", "COMPONENTS.md")
    legacy = os.path.join(tools_root, "hermes_audit", "COMPONENTS.md")
    return f"no component index: expected {_show(expected)} (or legacy {_show(legacy)})"


def _index_missing_finding(tools_root: str, files: list[dict[str, Any]]) -> dict[str, Any]:
    """The index is required for a component: nothing there fails check/finish, never passes."""
    stems = [os.path.splitext(os.path.basename(str(e["path"])))[0] for e in files if e.get("role") == "component"]
    expected = os.path.join(tools_root, "components", "COMPONENTS.md")
    example = f"- `{stems[0] if stems else '<module>'}.<function>(...)` — <purpose> — returns `{{ok,status,error,findings}}`."
    return {
        "id": "index-file-missing",
        "severity": "error",
        "detail": f"{_index_missing_text(tools_root)}; every public function of a component must be listed there",
        "fix": f"create {_show(expected)} with one line per public function, e.g. {example}",
    }


# Copied from hermes_audit/project_kit.py (_SYSTEM_SUBTREE_ROOTS, _SYSTEM_EXACT_ROOTS and its
# top-level 'lib*' rule) so the refusal reads the same in both places;
# componentize/tests/test_state_root_safety.py asserts the two tables stay equal.
# Always refused: these folders and anything inside them ...
_SYSTEM_SUBTREE_ROOTS = ('/etc', '/usr', '/bin', '/sbin', '/boot', '/dev', '/proc', '/sys')
# ... and these folders themselves, plus every top-level folder whose name starts with 'lib'.
_SYSTEM_EXACT_ROOTS = ('/var', '/opt')
_STATE_GITIGNORE = ".gitignore"


def _is_same_or_inside(path: str, base: str) -> bool:
    if base == os.path.sep:
        return True
    return path == base or path.startswith(base + os.path.sep)


def _user_home() -> str | None:
    """The home folder to protect: HOME when it is absolute, else the account's passwd entry.

    An empty or relative HOME must not switch the home and ``~/.hermes`` protection off;
    None (no absolute home at all) makes every state-writing subcommand refuse.
    """
    raw = os.environ.get("HOME", "")
    if raw and os.path.isabs(raw):
        return os.path.normpath(raw)
    try:
        pw_dir = pwd.getpwuid(os.getuid()).pw_dir
    except (KeyError, OSError):
        pw_dir = ""
    return os.path.normpath(pw_dir) if pw_dir and os.path.isabs(pw_dir) else None


def _expand_home(value: str, home: str | None) -> str:
    """``~`` and ``~/...`` against the protected home (not a possibly empty HOME); ``~user`` as usual."""
    if value == "~" or value.startswith("~" + os.sep):
        return (home + value[1:]) if home else value
    return os.path.expanduser(value)


def _hermes_homes(home: str | None) -> list[str]:
    """``~/.hermes`` always, plus HERMES_HOME when it is absolute after ``~``/``$VAR`` expansion.

    Hermes expands ``~`` and environment variables in HERMES_HOME; a relative or empty value
    is ignored here (never resolved against the command's cwd) so it cannot replace or
    weaken the default.
    """
    homes = [os.path.join(home, ".hermes")] if home else []
    raw = os.environ.get("HERMES_HOME", "").strip()
    if raw:
        expanded = _expand_home(os.path.expandvars(raw), home)
        if os.path.isabs(expanded):
            homes.append(os.path.normpath(expanded))
    return homes


def _state_root_refusal(resolved: str, lexical: str | None = None) -> str | None:
    """One plain line refusing an unsafe state root, or None.

    ``resolved`` is the exact string the command pins afterwards (_resolve_state_root); it is
    always decided on. Two more candidates can only add refusals, never remove one: the same
    string with its last component followed too (so a symlinked state root into a protected
    folder gets the protected-folder message), and ``lexical``, the absolute path this one
    resolution was made from (so a path written through a protected folder is refused even
    when a symlink there points out). Nothing here resolves the user's path again. Refuses
    the filesystem root, the home folder and its ancestors, anything inside a Hermes home,
    and system folders; and a state root held directly by the home folder, a folder above
    it, the filesystem root or a top-level folder (the plugin's library-folder rule).
    """
    real = os.path.realpath(resolved)
    candidates = (real, resolved) if lexical is None else (real, lexical, resolved)

    def refuse(reason: str) -> str:
        return f"refusing state root {_show(real)}: {reason}"

    if os.path.sep in candidates:
        return refuse("the filesystem root is not a project folder; pick a dedicated project folder")
    home = _user_home()
    if home is None:
        return refuse("the home folder is unknown (HOME is unset, empty or relative and the account has "
                      "no absolute home folder), so it cannot be protected; set HOME to an absolute path")
    for entry in _hermes_homes(home):
        for base in {entry, os.path.realpath(entry)}:
            if any(_is_same_or_inside(c, base) for c in candidates):
                return refuse(f"it is inside a Hermes home ({_show(entry)}); pick a project folder outside it")
    for base in {home, os.path.realpath(home)}:
        if base in candidates:
            return refuse("it is the home folder itself; pick a project folder inside it")
        if any(_is_same_or_inside(base, c) for c in candidates):
            return refuse("it is an ancestor of the home folder; pick a project folder")
    for c in candidates:
        for sys_root in _SYSTEM_SUBTREE_ROOTS:
            real_sys = os.path.realpath(sys_root)
            if _is_same_or_inside(c, sys_root) or (real_sys != os.path.sep and _is_same_or_inside(c, real_sys)):
                return refuse("it is a system folder; pick a dedicated project folder")
        for exact_root in _SYSTEM_EXACT_ROOTS:
            if c in (exact_root, os.path.realpath(exact_root)):
                return refuse("it is a system folder; pick a dedicated project folder")
        if os.path.dirname(c) == os.path.sep and os.path.basename(c).startswith("lib"):
            return refuse("it is a system folder; pick a dedicated project folder")
    # The plugin's library-folder rule, applied to the folder that holds the state root (the
    # tools root for the default <tools root>/.componentize): never the home folder itself, a
    # folder above it, the filesystem root or a top-level folder. Decided on the parents of
    # the strings above (the resolved one included), so nothing is resolved again. The
    # followed final component is left out: a symlinked state root keeps its own refusal.
    for c in {os.path.dirname(resolved), os.path.dirname(lexical or resolved)}:
        if (os.path.dirname(c) == os.path.sep or c == os.path.sep
                or any(_is_same_or_inside(base, c) for base in {home, os.path.realpath(home)})):
            return refuse("the folder holding it is the home folder itself, a folder above the home "
                          "folder or a top-level folder; pick a dedicated project folder")
    return None


def _lexical_state_root(state_root: str) -> str:
    """The user's state root made absolute and normalized, without touching the filesystem."""
    return os.path.normpath(os.path.abspath(state_root))


def _resolve_state_root(lexical: str) -> str:
    """The one resolution of the state root: every ancestor of the absolute, normalized
    ``lexical`` path resolved once; the last component is kept as given so a symlinked state
    root is still seen (and refused) by the later no-follow checks. main() decides on this
    string and pins the chain on this same string; it is never resolved again."""
    return os.path.join(os.path.realpath(os.path.dirname(lexical)), os.path.basename(lexical))


class _StateRefused(Exception):
    """A folder on the resolved job-state path is a symlink or not a folder (planted before
    the command, or swapped in after the state root was resolved): the command stops with this
    one line; nothing is written through it."""


# Ancestors of the state root are only walked (O_PATH where the platform has it, so a folder
# without read permission can still be passed); the state root and everything below it are
# opened for reading so they can be listed and fsynced.
_ANCESTOR_OPEN_FLAGS = (getattr(os, "O_PATH", 0) or os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_DIR_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_GITIGNORE_MAX_BYTES = 64 * 1024
# The pinned chain for the running command: "root" is the resolved state root, "fd" the folder
# holding it (opened component by component from "/" without following symlinks), or None
# while that folder does not exist yet.
_STATE: dict[str, Any] = {"root": None, "fd": None}


def _show(value: object, *, limit: int = 300) -> str:
    """Render a path, job name or spec-supplied text for a message: control, bidi and other
    non-printable code points become visible escapes; very long values are shortened."""
    out: list[str] = []
    for ch in str(value):
        if ch.isprintable():
            out.append(ch)
            continue
        cp = ord(ch)
        out.append(f"\\x{cp:02x}" if cp <= 0xFF else f"\\u{cp:04x}" if cp <= 0xFFFF else f"\\U{cp:08x}")
    text = "".join(out)
    if len(text) > limit:
        head, tail = limit * 2 // 3, limit // 4
        text = f"{text[:head]}...[{len(text) - head - tail} chars]...{text[-tail:]}"
    return text


def _show_block(text: str) -> str:
    """_show for multi-line output built elsewhere: keeps line breaks and tabs, escapes the rest."""
    return "\n".join("\t".join(_show(part, limit=4000) for part in line.split("\t"))
                     for line in str(text).split("\n"))


def _symlink_refusal(path: str) -> str:
    return f"state path is a symlink, refusing: {_show(path)}"


def _raw_lstat(name: str, dir_fd: int | None) -> os.stat_result | None:
    try:
        return os.lstat(name, dir_fd=dir_fd)
    except (FileNotFoundError, NotADirectoryError):
        return None


def _open_dir_nofollow(name: str, shown: str, *, dir_fd: int | None, flags: int = _DIR_OPEN_FLAGS) -> int:
    """Open one folder relative to dir_fd without following a symlink at it."""
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise _StateRefused(_symlink_refusal(shown)) from None
        if exc.errno == errno.ENOTDIR:
            st = _raw_lstat(name, dir_fd)
            if st is not None and stat.S_ISLNK(st.st_mode):
                raise _StateRefused(_symlink_refusal(shown)) from None
        raise
    if not stat.S_ISDIR(os.fstat(fd).st_mode):  # O_PATH|O_NOFOLLOW can open a symlink itself
        os.close(fd)
        st = _raw_lstat(name, dir_fd)
        if st is not None and stat.S_ISLNK(st.st_mode):
            raise _StateRefused(_symlink_refusal(shown))
        raise NotADirectoryError(errno.ENOTDIR, "Not a directory", shown)
    return fd


def _open_state_chain(state_root: str, *, create: bool) -> int | None:
    """Open the folder holding the state root as a chain of directory fds from "/".

    state_root is the one resolved string that main() handed to _state_root_refusal (it is
    never resolved again). Each component is opened relative to its parent's fd with
    O_DIRECTORY|O_NOFOLLOW, so the fd refers to exactly that checked string, or the command
    refuses: a symlink found at any level, including one swapped in after the check, makes
    it refuse instead of following it. With create, missing
    folders are made one at a time inside the chain (never by path); otherwise a missing
    folder gives None. The state root itself, the job folder and everything below are reached
    from this fd the same way by _state_dir_of on every access.
    """
    parts = [p for p in os.path.dirname(state_root).split(os.sep) if p]
    fds = [os.open(os.sep, _ANCESTOR_OPEN_FLAGS)]
    try:
        for i, part in enumerate(parts):
            shown = os.path.join(os.sep, *parts[:i + 1])
            try:
                fds.append(_open_dir_nofollow(part, shown, dir_fd=fds[-1], flags=_ANCESTOR_OPEN_FLAGS))
            except FileNotFoundError:
                if not create:
                    return None
                try:
                    os.mkdir(part, dir_fd=fds[-1])
                except FileExistsError:
                    pass
                fds.append(_open_dir_nofollow(part, shown, dir_fd=fds[-1], flags=_ANCESTOR_OPEN_FLAGS))
            os.close(fds.pop(-2))
        return fds.pop()
    finally:
        for fd in fds:
            _io.close_quietly(fd)


@contextlib.contextmanager
def _state_anchor(state_root: str) -> Iterator[None]:
    """Pin the state chain for one command; every state read and write goes through it."""
    try:
        fd = _open_state_chain(state_root, create=False)
    except OSError as exc:
        raise _StateRefused(f"cannot open the folder holding state root {_show(state_root)}: "
                            f"{type(exc).__name__}: {_show(exc)}") from None
    saved = dict(_STATE)
    _STATE.update(root=state_root, fd=fd)
    try:
        yield
    finally:
        if _STATE["fd"] is not None:
            _io.close_quietly(_STATE["fd"])
        _STATE.update(saved)


def _ensure_anchor_created() -> None:
    if _STATE["root"] is not None and _STATE["fd"] is None:
        _STATE["fd"] = _open_state_chain(_STATE["root"], create=True)


def _state_rel(path: str) -> list[str] | None:
    """Components of path below the folder holding the pinned state root, or None when
    no chain is pinned or path is not job state."""
    root = _STATE["root"]
    if root is None:
        return None
    p = os.path.normpath(os.path.abspath(path))
    if p == root:
        return [os.path.basename(root)]
    if not p.startswith(root + os.sep):
        return None
    return [os.path.basename(root), *[c for c in p[len(root) + 1:].split(os.sep) if c]]


@contextlib.contextmanager
def _state_dir_of(path: str) -> Iterator[tuple[int, str]]:
    """(fd of the folder holding path, leaf name), walked from the pinned chain with
    O_NOFOLLOW at every level. FileNotFoundError when a folder on the way is missing."""
    parts = _state_rel(path)
    if parts is None:
        raise ValueError(f"not a job state path: {_show(path)}")
    cur = _STATE["fd"]
    if cur is None:
        raise FileNotFoundError(errno.ENOENT, "No such file or directory", os.path.dirname(_STATE["root"]))
    owned = False
    shown = os.path.dirname(_STATE["root"])
    try:
        for comp in parts[:-1]:
            shown = os.path.join(shown, comp)
            nxt = _open_dir_nofollow(comp, shown, dir_fd=cur)
            if owned:
                os.close(cur)
            cur, owned = nxt, True
        yield cur, parts[-1]
    finally:
        if owned:
            _io.close_quietly(cur)


def _lstat_if_exists(path: str, *, dir_fd: int | None = None) -> os.stat_result | None:
    try:
        if dir_fd is None and _state_rel(path) is not None:
            with _state_dir_of(path) as (pfd, name):
                return os.lstat(name, dir_fd=pfd)
        return os.lstat(path, dir_fd=dir_fd)
    except (FileNotFoundError, NotADirectoryError):
        return None


def _open_regular_at(dir_fd: int, name: str, max_bytes: int) -> tuple[int | None, int | None, str | None]:
    """_io._open_regular_reader relative to a pinned folder fd."""
    fd: int | None = None
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dir_fd)
        st = os.fstat(fd)
    except OSError as exc:
        _io.close_quietly(fd)
        if exc.errno in (errno.ELOOP, errno.EISDIR, errno.ENXIO):
            return None, None, "not-regular"
        return None, None, _io._fmt_os_err(exc)
    if not stat.S_ISREG(st.st_mode):
        _io.close_quietly(fd)
        return None, None, "not-regular"
    if st.st_size > max_bytes:
        _io.close_quietly(fd)
        return None, st.st_size, f"too-large size={st.st_size}"
    return fd, st.st_size, None


def _open_state_file(path: str, max_bytes: int) -> tuple[int | None, int | None, str | None]:
    try:
        with _state_dir_of(path) as (pfd, name):
            return _open_regular_at(pfd, name, max_bytes)
    except OSError as exc:
        return None, None, _io._fmt_os_err(exc)


def _read_text(path: str) -> dict[str, Any]:
    """_io.read_text; job state is read through the pinned chain."""
    if _state_rel(path) is None:
        return _io.read_text(path)
    max_bytes = _io._DEFAULT_TEXT_MAX_BYTES
    fd, _, err = _open_state_file(path, max_bytes)
    if fd is None:
        return {"text": None, "error": err}
    chunks: list[bytes] = []
    try:
        total = _io._read_bounded(fd, max_bytes, chunks.append)
        if total > max_bytes:
            return {"text": None, "error": f"too-large size={total}"}
        return {"text": b"".join(chunks).decode("utf-8", errors="strict"), "error": None}
    except UnicodeDecodeError:
        return {"text": None, "error": "not-utf8"}
    except OSError as exc:
        return {"text": None, "error": _io._fmt_os_err(exc)}
    finally:
        _io.close_quietly(fd)


def _sha256_file(path: str) -> dict[str, Any]:
    """_io.sha256_file; job state is hashed through the pinned chain."""
    if _state_rel(path) is None:
        return _io.sha256_file(path)
    max_bytes = _io._DEFAULT_SHA256_MAX_BYTES
    fd, size, err = _open_state_file(path, max_bytes)
    if fd is None:
        return _io._sha256_err(size, err)
    digest = hashlib.sha256()
    try:
        total = _io._read_bounded(fd, max_bytes, digest.update)
        if total > max_bytes:
            return _io._sha256_err(total, f"too-large size={total}")
        st = os.fstat(fd)
        return {"sha256": digest.hexdigest(), "size": total, "ino": st.st_ino,
                "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns, "error": None}
    except OSError as exc:
        return _io._sha256_err(None, _io._fmt_os_err(exc))
    finally:
        _io.close_quietly(fd)


def _write_atomic_at(dir_fd: int, name: str, payload: bytes) -> dict[str, Any]:
    tmp = f".{name}.tmp-{os.getpid()}-{secrets.token_hex(6)}"
    fd: int | None = None
    created = False
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644, dir_fd=dir_fd)
        created = True
        _io._write_all(fd, payload)
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except OSError as exc:
        _io.close_quietly(fd)
        if created:
            try:
                os.unlink(tmp, dir_fd=dir_fd)
            except OSError:
                pass
        return {"ok": False, "error": _io._fmt_os_err(exc)}
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    return {"ok": True, "error": None}


def _write_state_atomic(path: str, data: str | bytes) -> dict[str, Any]:
    """_io.write_atomic; job state is written (temp file + replace) inside the pinned chain."""
    if _state_rel(path) is None:
        return _io.write_atomic(path, data)
    payload = data.encode("utf-8") if isinstance(data, str) else data
    try:
        with _state_dir_of(path) as (pfd, name):
            return _write_atomic_at(pfd, name, payload)
    except OSError as exc:
        return {"ok": False, "error": _io._fmt_os_err(exc)}


def _unlink(path: str) -> None:
    if _state_rel(path) is None:
        os.unlink(path)
        return
    with _state_dir_of(path) as (pfd, name):
        os.unlink(name, dir_fd=pfd)


def _listdir(path: str) -> list[str]:
    if _state_rel(path) is None:
        return os.listdir(path)
    with _state_dir_of(path) as (pfd, name):
        fd = _open_dir_nofollow(name, path, dir_fd=pfd)
        try:
            return os.listdir(fd)
        finally:
            os.close(fd)


def _mkdir_state(path: str, *, exist_ok: bool) -> bool:
    """Create one job-state folder inside the pinned chain; True when it was created."""
    with _state_dir_of(path) as (pfd, name):
        try:
            os.mkdir(name, dir_fd=pfd)
            return True
        except FileExistsError:
            if not exist_ok:
                raise
            st = _raw_lstat(name, pfd)
            if st is not None and stat.S_ISLNK(st.st_mode):
                raise _StateRefused(_symlink_refusal(path)) from None
            if st is None or not stat.S_ISDIR(st.st_mode):
                raise
            return False


def _makedirs(path: str, *, exist_ok: bool = True) -> None:
    """os.makedirs; below the state root each folder is made inside the pinned chain."""
    parts = _state_rel(path)
    if parts is None:
        os.makedirs(path, exist_ok=exist_ok)
        return
    base = os.path.dirname(_STATE["root"])
    for i in range(1, len(parts) + 1):
        _mkdir_state(os.path.join(base, *parts[:i]), exist_ok=exist_ok or i < len(parts))


def _create_state_dirs(state_root: str, job_dir: str) -> tuple[bool, str | None]:
    """Create the state root and job folder inside the pinned chain; (state root created, error).

    Missing folders above the state root are created inside the chain too, so a concurrent
    swap can make a command refuse but cannot make it create anything outside the checked path.
    """
    created = False
    current = state_root
    try:
        _ensure_anchor_created()
        created = _mkdir_state(state_root, exist_ok=True)
        current = job_dir
        _mkdir_state(job_dir, exist_ok=True)
    except _StateRefused as exc:
        return created, str(exc)
    except FileExistsError:
        return created, f"state path is not a directory, refusing: {_show(current)}"
    return created, None


def _inside_git_work_tree(path: str) -> bool:
    """True when a folder above path holds a `.git` entry (folder, or file for a worktree)."""
    current = os.path.dirname(os.path.realpath(path))
    while True:
        if os.path.lexists(os.path.join(current, ".git")):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def _gitignore_ignores_all(text: str) -> bool:
    """True when a .gitignore makes git ignore everything in its folder: a line that is
    exactly `*` (comment lines and trailing spaces ignored, as git does; leading spaces are
    part of a git pattern, so `  *` does not count) and no `!` re-include line."""
    lines = [ln.rstrip(" ") for ln in text.split("\n")]
    lines = [ln for ln in lines if ln and not ln.startswith("#")]
    return "*" in lines and not any(ln.startswith("!") for ln in lines)


def _gitignore_verdict(root_fd: int) -> str:
    """absent | symlink | not-regular | ignores-all | not-ignoring (lstat, never followed)."""
    st = _raw_lstat(_STATE_GITIGNORE, root_fd)
    if st is None:
        return "absent"
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    if not stat.S_ISREG(st.st_mode):
        return "not-regular"
    fd, _, err = _open_regular_at(root_fd, _STATE_GITIGNORE, _GITIGNORE_MAX_BYTES)
    if fd is None:
        return "not-regular" if err == "not-regular" else "not-ignoring"
    chunks: list[bytes] = []
    try:
        total = _io._read_bounded(fd, _GITIGNORE_MAX_BYTES, chunks.append)
    except OSError:
        return "not-ignoring"
    finally:
        _io.close_quietly(fd)
    if total > _GITIGNORE_MAX_BYTES:
        return "not-ignoring"
    return "ignores-all" if _gitignore_ignores_all(b"".join(chunks).decode("utf-8", errors="replace")) else "not-ignoring"


def _gitignore_refusal_line(gi_path: str, verdict: str) -> str:
    what = "a symlink" if verdict == "symlink" else "not a regular file"
    return (f"refusing {_show(gi_path)}: the state folder's .gitignore is {what}; "
            f"replace it with a regular file containing a line with only *")


def _gitignore_refusal(state_root: str) -> str | None:
    """Before anything is created: refuse a state folder whose .gitignore is a symlink or not a file."""
    if not _inside_git_work_tree(state_root):
        return None
    gi_path = os.path.join(state_root, _STATE_GITIGNORE)
    try:
        with _state_dir_of(gi_path) as (root_fd, _):
            verdict = _gitignore_verdict(root_fd)
    except (FileNotFoundError, NotADirectoryError):
        return None
    return _gitignore_refusal_line(gi_path, verdict) if verdict in ("symlink", "not-regular") else None


def _ensure_state_gitignore(state_root: str, created: bool) -> tuple[str, str | None]:
    """Make sure git ignores the state folder inside a git work tree; (status, line to print).

    The folder's own .gitignore decides (a per-folder file overrides the ones above it), so
    its content is checked instead of running git. Status "refused" means begin must stop.
    """
    if not _inside_git_work_tree(state_root):
        return "not-git", None
    gi_path = os.path.join(state_root, _STATE_GITIGNORE)
    shown_root, shown_gi = _show(state_root), _show(gi_path)
    not_ignored = (f"job state in {shown_root} is local to this checkout and is NOT ignored by git: "
                   f"{shown_gi} has no line containing only * (or re-includes files with !); add that line to it")
    with _state_dir_of(gi_path) as (root_fd, name):
        verdict = _gitignore_verdict(root_fd)
        if verdict == "absent":
            if not created and os.path.basename(state_root) != ".componentize":
                return "not-ignored", (f"job state in {shown_root} is local to this checkout and is NOT ignored by git: "
                                       f"add it to your .gitignore")
            try:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644,
                             dir_fd=root_fd)
            except FileExistsError:
                verdict = _gitignore_verdict(root_fd)
            except OSError as exc:
                return "error", (f"warning: could not write {shown_gi} ({type(exc).__name__}: {_show(exc)}); "
                                 f"do not commit {shown_root}")
            else:
                try:
                    _io._write_all(fd, b"*\n")
                finally:
                    os.close(fd)
                if created:
                    return "created", f"job state in {shown_root} is local to this checkout and is ignored by git ({shown_gi})"
                return "added", (f"job state in {shown_root} is local to this checkout and is ignored by git "
                                 f"(added .gitignore to the existing folder)")
    if verdict in ("symlink", "not-regular"):
        return "refused", _gitignore_refusal_line(gi_path, verdict)
    if verdict == "ignores-all":
        return "present", None
    return "not-ignored", not_ignored


def _acquire_lock_at(lock_path: str) -> dict[str, Any]:
    """_io.acquire_lock with the lock file opened inside the pinned chain."""
    fd: int | None = None
    locked = False
    try:
        with _state_dir_of(lock_path) as (pfd, name):
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644, dir_fd=pfd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        _io._write_all(fd, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(fd)
        return {"ok": True, "fd": fd, "error": None, "holder": None}
    except BlockingIOError:
        holder = _io._read_holder_fd(fd) if fd is not None else ""
        _io.close_quietly(fd)
        return {"ok": False, "fd": None, "error": "locked", "holder": holder}
    except OSError as exc:
        if locked and fd is not None:
            _io.release_lock(fd)
        else:
            _io.close_quietly(fd)
        return {"ok": False, "fd": None, "error": _io._fmt_os_err(exc), "holder": None}


def _acquire_job_lock(lock_path: str, *, busy_prefix: str = "") -> tuple[int, str | None]:
    if _state_rel(lock_path) is not None:
        lock = _acquire_lock_at(lock_path)
    else:
        lock = _io.acquire_lock(lock_path, pid=os.getpid())
    if lock["ok"] and lock["fd"] is not None:
        return lock["fd"], None
    if lock["error"] == "locked":
        return -1, f"{busy_prefix}locked by {_show(lock['holder'])}"
    return -1, f"lock error: {_show(lock['error'])}"


def _find_duplicate_inode_findings(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    path_inodes: list[tuple[str, tuple[int, int] | None]] = []
    for p in _state.collect_inode_check_paths(manifest):
        st = _lstat_if_exists(p)
        dev_ino = (st.st_dev, st.st_ino) if st is not None else None
        path_inodes.append((p, dev_ino))
    return _state.find_duplicate_inode_findings(path_inodes)


def _emit_manifest_findings(findings: list[dict[str, Any]]) -> int:
    if not _OUTPUT["json"]:
        for f in findings:
            print(f"{_show(f['detail'])} — {_show(f['fix'])}", file=sys.stderr)
    return _err("manifest invalid", code=1, extra={"findings": findings})


def _load_and_validate_manifest(manifest_path: str, expected_name: str) -> tuple[dict[str, Any] | None, int]:
    abs_manifest = os.path.abspath(manifest_path)
    read_res = _read_text(abs_manifest)
    if read_res["error"] is not None or read_res["text"] is None:
        if read_res["error"] == "not-regular":
            return None, _err(f"manifest must be a regular file (symlinks are not followed): {_show(manifest_path)}")
        return None, _err(f"cannot read manifest {_show(manifest_path)}: {_show(read_res['error'])}")
    raw_manifest, json_err = _state.parse_json_strict(read_res["text"])
    if json_err is not None:
        return None, _err(f"invalid manifest JSON: {_show(json_err)}")
    resolved = _state.resolve_manifest_paths(raw_manifest, os.path.dirname(abs_manifest))
    val_res = componentize_evidence.validate_manifest(resolved)
    if val_res["status"] == "error":
        return None, _err(f"manifest error: {_show(val_res['error'])}")
    if val_res["status"] == "fail":
        return None, _emit_manifest_findings(val_res["findings"])
    _, inventory_findings = _plugins.inventory(resolved)
    if inventory_findings:
        return None, _emit_manifest_findings(inventory_findings)
    if val_res["name"] != expected_name:
        return None, _err(f"manifest name {val_res['name']!r} does not match {expected_name!r}")
    dup_findings = _find_duplicate_inode_findings(resolved)
    if dup_findings:
        return None, _emit_manifest_findings(dup_findings)
    return resolved, 0


def _capture_golden_twice(golden: dict[str, Any], timeout: float) -> tuple[dict[str, Any] | None, int]:
    runs: list[dict[str, Any]] = []
    for _ in range(2):
        r = _io.run_argv(golden["argv"], cwd=golden["cwd"], env=golden["env"], timeout=timeout)
        trunc_stream = _state.detect_truncated_stream(r)
        if trunc_stream is not None:
            return None, _err(f"output exceeds limit ({trunc_stream})")
        if r["timed_out"] or r["error"] is not None:
            return None, _err(f"golden run failed: {_show(r['error'])}")
        runs.append(r)
    cmp_res = componentize_evidence.compare_capture(runs[0], runs[1], compare=golden["compare"])
    if cmp_res["status"] == "error":
        return None, _err(f"compare error: {_show(cmp_res['error'])}")
    if not cmp_res["matched"]:
        details = "; ".join(f["detail"] for f in cmp_res["findings"])
        return None, _err(f"nondeterministic: {_show(details)}", code=1, extra={"streams": cmp_res["streams"]})
    return runs[0], 0


def _write_baseline_streams(b_paths: dict[str, str], run_data: dict[str, Any]) -> tuple[str | None, str]:
    rc_str = str(int(run_data["rc"]))
    for key, content in (
        ("baseline_stdout", run_data["stdout"]),
        ("baseline_stderr", run_data["stderr"]),
        ("baseline_rc", rc_str),
    ):
        w = _write_state_atomic(b_paths[key], content)
        if not w["ok"]:
            return str(w["error"]), rc_str
    return None, rc_str


def _write_baseline_gen1(paths: dict[str, str], golden: dict[str, Any], run_data: dict[str, Any] | None) -> str | None:
    try:
        _makedirs(paths["baseline_dir"])
    except OSError as exc:
        return f"{type(exc).__name__}: {_show(exc)}"
    if "waived" in golden:
        return _write_json_or_err(paths["baseline_meta"], {"waived": golden["waived"]})
    assert run_data is not None
    s_err, rc_str = _write_baseline_streams(paths, run_data)
    if s_err is not None:
        return s_err
    meta = _state.build_baseline_meta(golden, run_data["stdout"], run_data["stderr"], rc_str)
    return _write_json_or_err(paths["baseline_meta"], meta)


def _emit_begin_output(
    name: str,
    kind: str,
    waived: bool,
    rc_val: int | None,
    tools_root: str,
    as_json: bool,
    state_gitignore: str,
) -> None:
    index_path = _index_path(tools_root)
    if as_json:
        print(_dumps_json({
            "ok": True,
            "exit": 0,
            "name": name,
            "kind": kind,
            "baseline_gen": 1,
            "waived": waived,
            "rc": rc_val,
            "index_path": index_path,
            "state_gitignore": state_gitignore,
        }))
        return
    status_str = "waived" if waived else f"captured, rc={rc_val}"
    print(f"begun {name} kind={kind} baseline=gen-1 ({status_str})")
    index_rel = f"{_component_dir(tools_root)}/COMPONENTS.md"
    print(f"reuse audit: {index_rel}")
    if not os.path.lexists(index_path):
        needed = "check and finish fail until it lists each public function" if kind == "component" else "nothing to audit"
        print(f"warning: {_index_missing_text(tools_root)}; {needed}")
        return
    idx_res = _read_text(index_path)
    if idx_res["error"] is not None or idx_res["text"] is None:
        print(f"warning: {index_rel} missing or unreadable ({_show(idx_res['error'])})")
    else:
        print(_show_block(idx_res["text"].rstrip("\n")))


_BASELINE_DEBRIS_NAMES = frozenset({"stdout", "stderr", "rc", "meta.json"})
_TEMP_DEBRIS_RE = re.compile(r"^\.(stdout|stderr|rc|meta\.json)\.tmp-")
_ATTEST_TEMP_RE = re.compile(r"^\.attest\.json\.tmp-")
_NOT_BEGUN_MSG = "not begun: run componentize begin <name> --manifest <spec.json>"
_ALREADY_BEGUN_MSG = (
    "already begun; to change the spec run: "
    "componentize begin <name> --amend --manifest <path> --reason '<why>'"
)
_RERUN_FINISH_FIX = "re-run `componentize finish <name>` then `componentize status <name>`"


def _is_baseline_debris(name: str) -> bool:
    return name in _BASELINE_DEBRIS_NAMES or bool(_TEMP_DEBRIS_RE.match(name))


def _refuse_symlink_or_not_dir(path: str, *, dir_fd: int | None = None, name: str | None = None) -> int:
    target = name if name is not None else path
    st = _lstat_if_exists(target, dir_fd=dir_fd)
    if st is not None and stat.S_ISLNK(st.st_mode):
        return _err(f"state path is a symlink, refusing: {_show(path)}", code=2)
    return _err(
        f"state path is a symlink or not a directory (unexpected entry in uncommitted baseline), refusing: {_show(path)}",
        code=2,
    )


def _open_sub_dir_fd(
    name: str,
    full_path: str,
    *,
    dir_fd: int | None = None,
    allow_missing: bool = True,
    fds: list[int] | None = None,
) -> tuple[int | None, int]:
    try:
        fd = os.open(name, _DIR_OPEN_FLAGS, dir_fd=dir_fd)
    except FileNotFoundError:
        if allow_missing:
            return None, 0
        raise
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR, 40, 20) or isinstance(exc, NotADirectoryError):
            return None, _refuse_symlink_or_not_dir(full_path, dir_fd=dir_fd, name=name)
        raise
    if fds is not None:
        fds.append(fd)
    return fd, 0


def _check_manifest_gen1_absent(
    job_fd: int,
    manifest_dir: str,
    manifest_gen: str,
    *,
    fds: list[int] | None = None,
) -> tuple[int | None, int]:
    mdir_st = _lstat_if_exists("manifest", dir_fd=job_fd)
    if mdir_st is None:
        return None, 0
    if stat.S_ISLNK(mdir_st.st_mode):
        return None, _err(f"state path is a symlink, refusing: {_show(manifest_dir)}", code=2)
    if not stat.S_ISDIR(mdir_st.st_mode):
        return None, 0
    manifest_fd, code = _open_sub_dir_fd("manifest", manifest_dir, dir_fd=job_fd, allow_missing=True, fds=fds)
    if code != 0 or manifest_fd is None:
        return manifest_fd, code
    mgen_st = _lstat_if_exists("gen-1.json", dir_fd=manifest_fd)
    if mgen_st is not None:
        if stat.S_ISLNK(mgen_st.st_mode):
            return manifest_fd, _err(f"state path is a symlink, refusing: {_show(manifest_gen)}", code=2)
        return manifest_fd, _err(_ALREADY_BEGUN_MSG)
    return manifest_fd, 0


def _validate_gen1_debris(gen1_fd: int, baseline_dir: str) -> tuple[list[str], int]:
    names: list[str] = []
    for name in sorted(os.listdir(gen1_fd)):
        entry_path = os.path.join(baseline_dir, name)
        est = _lstat_if_exists(name, dir_fd=gen1_fd)
        if est is None:
            continue
        if stat.S_ISLNK(est.st_mode):
            return [], _err(f"state path is a symlink, refusing: {_show(entry_path)}", code=2)
        if not _is_baseline_debris(name) or not stat.S_ISREG(est.st_mode):
            return [], _err(f"unexpected entry in uncommitted baseline, refusing: {_show(entry_path)}", code=2)
        names.append(name)
    return names, 0


def _validate_job_debris(job_fd: int, job_dir: str, attest_path: str) -> tuple[list[str], int]:
    names: list[str] = []
    att_st = _lstat_if_exists("attest.json", dir_fd=job_fd)
    if att_st is not None:
        if stat.S_ISLNK(att_st.st_mode):
            return [], _err(f"state path is a symlink, refusing: {_show(attest_path)}", code=2)
        if not stat.S_ISREG(att_st.st_mode):
            return [], _err(f"unexpected entry in uncommitted baseline, refusing: {_show(attest_path)}", code=2)
        names.append("attest.json")
    for name in sorted(os.listdir(job_fd)):
        if _ATTEST_TEMP_RE.match(name):
            tpath = os.path.join(job_dir, name)
            tst = _lstat_if_exists(name, dir_fd=job_fd)
            if tst is None:
                continue
            if stat.S_ISLNK(tst.st_mode):
                return [], _err(f"state path is a symlink, refusing: {_show(tpath)}", code=2)
            if not stat.S_ISREG(tst.st_mode):
                return [], _err(f"unexpected entry in uncommitted baseline, refusing: {_show(tpath)}", code=2)
            names.append(name)
    return names, 0


def _unlink_validated(dir_fd: int, names: list[str]) -> None:
    for name in names:
        try:
            os.unlink(name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass


def _open_recovery_fds(
    paths: dict[str, str], fds: list[int],
) -> tuple[int | None, int | None, int]:
    job_dir = paths["job_dir"]
    baseline_root = os.path.join(job_dir, "baseline")
    if _state_rel(job_dir) is None:
        job_fd, code = _open_sub_dir_fd(job_dir, job_dir, dir_fd=None, allow_missing=True, fds=fds)
    else:
        try:
            with _state_dir_of(job_dir) as (root_fd, name):
                job_fd, code = _open_sub_dir_fd(name, job_dir, dir_fd=root_fd, allow_missing=True, fds=fds)
        except FileNotFoundError:
            job_fd, code = None, 0
    if code != 0 or job_fd is None:
        return None, None, code
    _, code = _check_manifest_gen1_absent(job_fd, paths["manifest_dir"], paths["manifest_gen"], fds=fds)
    if code != 0:
        return None, None, code
    base_fd, code = _open_sub_dir_fd("baseline", baseline_root, dir_fd=job_fd, allow_missing=True, fds=fds)
    if code != 0 or base_fd is None:
        return job_fd, None, code
    gen1_fd, code = _open_sub_dir_fd("gen-1", paths["baseline_dir"], dir_fd=base_fd, allow_missing=True, fds=fds)
    return job_fd, gen1_fd, code


def _recover_uncommitted_begin(paths: dict[str, str]) -> int:
    """Recover uncommitted gen-1 baseline debris using fd-anchored validate-then-delete.

    Deletions are confined to the pinned <job>/baseline/gen-1 directory and <job>
    (attest.json + .attest.json.tmp-*), by allowlisted NAMES that were validated as
    regular files; directory traversal cannot be redirected by concurrent path swaps.
    A concurrent writer that replaces a validated entry inside these trusted
    directories may cause that replacement entry to be unlinked; this stays within
    the job's own state and is accepted under the trust boundary (state dir owned by
    the user; lock advisory). Subsequent begin writes go through the pinned state chain
    (_write_state_atomic: no symlink is followed at any level), not through these pinned
    gen-1/<job> fds. All validation completes before any deletion begins.
    """
    job_dir = paths["job_dir"]
    state_root = os.path.dirname(job_dir)
    sr_st = _lstat_if_exists(state_root)
    if sr_st is not None and (stat.S_ISLNK(sr_st.st_mode) or not stat.S_ISDIR(sr_st.st_mode)):
        return _refuse_symlink_or_not_dir(state_root)
    fds: list[int] = []
    try:
        job_fd, gen1_fd, code = _open_recovery_fds(paths, fds)
        if code != 0 or job_fd is None:
            return code
        gen1_to_unlink: list[str] = []
        if gen1_fd is not None:
            gen1_to_unlink, code = _validate_gen1_debris(gen1_fd, paths["baseline_dir"])
            if code != 0:
                return code
        job_to_unlink, code = _validate_job_debris(job_fd, job_dir, paths["attest"])
        if code != 0:
            return code
        _unlink_validated(job_fd, job_to_unlink)
        if gen1_fd is not None:
            _unlink_validated(gen1_fd, gen1_to_unlink)
        return 0
    finally:
        for fd in reversed(fds):
            _io.close_quietly(fd)


def _execute_begin_locked(
    args: argparse.Namespace,
    paths: dict[str, str],
    manifest: dict[str, Any],
) -> tuple[dict[str, Any] | None, bool, int]:
    rec_code = _recover_uncommitted_begin(paths)
    if rec_code != 0:
        return None, False, rec_code
    golden = manifest["golden"]
    waived = "waived" in golden
    run_data: dict[str, Any] | None = None
    if not waived:
        run_data, g_code = _capture_golden_twice(golden, float(args.timeout))
        if run_data is None:
            return None, waived, g_code
    _makedirs(paths["manifest_dir"])
    err = _write_baseline_gen1(paths, golden, run_data)
    if err is not None:
        return None, waived, _err(f"baseline write error: {_show(err)}")
    err = _write_json_or_err(paths["attest"], _state.build_initial_attest(manifest["attest"]))
    if err is not None:
        return None, waived, _err(f"attest write error: {_show(err)}")
    err = _write_json_or_err(paths["manifest_gen"], manifest)
    if err is not None:
        return None, waived, _err(f"manifest write error: {_show(err)}")
    return run_data, waived, 0


def _cmd_begin(args: argparse.Namespace) -> int:
    if getattr(args, "amend", False):
        return _cmd_begin_amend(args)
    manifest, code = _load_and_validate_manifest(args.manifest, args.name)
    if manifest is None:
        return code
    paths = _state.job_paths(args.state_root, args.name, gen=1)
    try:
        sym_err = _find_symlink_in_paths((args.state_root, paths["job_dir"]))
        if sym_err is not None:
            return _err(sym_err, code=2)
        # Every refusal is decided before the first folder is created.
        gi_refusal = _gitignore_refusal(args.state_root)
        if gi_refusal is not None:
            return _err(gi_refusal, code=2)
        created, mk_err = _create_state_dirs(args.state_root, paths["job_dir"])
        if mk_err is not None:
            return _err(mk_err, code=2)
        gi_status, gi_line = _ensure_state_gitignore(args.state_root, created)
    except OSError as exc:
        return _err(f"cannot create state dir: {type(exc).__name__}: {_show(exc)}")
    if gi_status == "refused":
        return _err(gi_line or "refusing the state folder's .gitignore", code=2)
    if gi_line is not None and not args.json:
        print(gi_line)

    lock_fd, lock_err = _acquire_job_lock(paths["lock"])
    if lock_err is not None:
        return _err(lock_err)
    try:
        try:
            run_data, waived, b_code = _execute_begin_locked(args, paths, manifest)
            if b_code != 0:
                return b_code
        except OSError as exc:
            return _err(f"filesystem error: {type(exc).__name__}: {_show(exc)}")
    finally:
        _io.release_lock(lock_fd)
    rc_val = None if waived or run_data is None else int(run_data["rc"])
    _emit_begin_output(args.name, manifest["kind"], waived, rc_val, args.tools_root, bool(args.json), gi_status)
    return 0


def _load_and_validate_attest(attest_path: str) -> tuple[dict[str, Any] | None, str | None, str | None]:
    sym_err = _find_symlink_in_paths((attest_path,))
    if sym_err is not None:
        return None, None, sym_err
    att_res = _read_text(attest_path)
    if att_res["error"] is not None or att_res["text"] is None:
        return None, None, f"cannot read attest.json: {_show(att_res['error'])}"
    att_data, json_err = _state.parse_json_strict(att_res["text"])
    if json_err is not None:
        return None, None, f"corrupt attest.json: invalid attest.json: {_show(json_err)}"
    val_err = _state.validate_attest_data(att_data)
    if val_err is not None:
        return None, None, val_err
    return att_data, _state.sha256_text(att_res["text"]), None


def _execute_attest_locked(args: argparse.Namespace, paths: dict[str, str]) -> int:
    try:
        if _lstat_if_exists(paths["manifest_gen"]) is None:
            return _err(_NOT_BEGUN_MSG, code=2)
        if _lstat_if_exists(paths["attest"]) is None:
            return _err(
                "state incomplete: attest.json missing; restore it from backup or start over with a new job name (componentize begin <new-name> --manifest <spec.json>)",
                code=2,
            )
    except OSError as exc:
        return _err(f"cannot access state: {_show(exc)}", code=2)
    att_data, _, att_err = _load_and_validate_attest(paths["attest"])
    if att_err is not None or att_data is None:
        return _err(att_err or "corrupt attest.json", code=2)
    att_data[args.phase].append({"text": args.text, "at": _now_iso()})
    err = _write_json_or_err(paths["attest"], att_data)
    if err is not None:
        return _err(f"cannot write attest.json: {_show(err)}", code=2)
    inv_err = _invalidate_receipt_under_lock(paths["receipt"])
    if inv_err is not None:
        return _err(inv_err, code=2)
    return 0


def _cmd_attest(args: argparse.Namespace) -> int:
    if len(args.text.strip()) < _state.MIN_NOTE_CHARS:
        return _err(f"attestation text must be at least {_state.MIN_NOTE_CHARS} characters after trimming", code=2)
    paths = _state.job_paths(args.state_root, args.name, gen=1)
    pre_err = _precheck_begun_job_state(args.state_root, paths)
    if pre_err is not None:
        return pre_err
    try:
        if _lstat_if_exists(paths["manifest_gen"]) is None:
            return _err(_NOT_BEGUN_MSG, code=2)
    except OSError as exc:
        return _err(f"cannot access state: {_show(exc)}", code=2)
    lock_fd, lock_err = _acquire_job_lock(paths["lock"])
    if lock_err is not None:
        return _err(lock_err, code=2)
    try:
        code = _execute_attest_locked(args, paths)
        if code != 0:
            return code
    finally:
        _io.release_lock(lock_fd)
    if args.json:
        print(_dumps_json({"ok": True, "exit": 0, "name": args.name, "phase": args.phase, "attested": True}))
    else:
        print(f"attested {args.phase} for {args.name}")
    return 0


def _load_stored_manifest(state_root: str, name: str, m_gen: int) -> tuple[dict[str, Any] | None, str | None]:
    m_paths = _state.job_paths(state_root, name, gen=m_gen)
    m_res = _read_text(m_paths["manifest_gen"])
    if m_res["error"] is not None or m_res["text"] is None:
        return None, f"cannot read manifest gen-{m_gen}: {_show(m_res['error'])}"
    manifest, json_err = _state.parse_json_strict(m_res["text"])
    if json_err is not None:
        return None, f"corrupt manifest gen-{m_gen}: {_show(json_err)}"
    val_res = componentize_evidence.validate_manifest(manifest)
    if not val_res.get("ok") or val_res.get("name") != name:
        return None, f"corrupt state: invalid manifest gen-{m_gen}"
    return manifest, None


def _write_rebaseline_gen(
    b_paths: dict[str, str],
    golden: dict[str, Any],
    run_data: dict[str, Any] | None,
    *,
    reason: str,
    prev_gen: int,
    manifest_gen: int,
) -> str | None:
    try:
        _makedirs(b_paths["baseline_dir"], exist_ok=False)
    except OSError as exc:
        return f"{type(exc).__name__}: {_show(exc)}"
    if "waived" in golden:
        meta = _state.build_rebaseline_meta(
            golden, reason=reason, at=_now_iso(), previous_gen=prev_gen, manifest_gen=manifest_gen,
        )
        return _write_json_or_err(b_paths["baseline_meta"], meta)
    assert run_data is not None
    s_err, rc_str = _write_baseline_streams(b_paths, run_data)
    if s_err is not None:
        return s_err
    meta = _state.build_rebaseline_meta(
        golden, run_data["stdout"], run_data["stderr"], rc_str,
        reason=reason, at=_now_iso(), previous_gen=prev_gen, manifest_gen=manifest_gen,
    )
    return _write_json_or_err(b_paths["baseline_meta"], meta)


def _load_baseline_meta_strict(state_root: str, name: str, b_gen: int) -> tuple[dict[str, Any] | None, str | None]:
    b_paths = _state.job_paths(state_root, name, gen=b_gen)
    res = _read_text(b_paths["baseline_meta"])
    if res["error"] is not None or res["text"] is None:
        return None, f"cannot read baseline meta gen-{b_gen}: {_show(res['error'])}"
    meta, json_err = _state.parse_json_strict(res["text"])
    if json_err is not None or not isinstance(meta, dict):
        return None, f"corrupt baseline meta gen-{b_gen}: {_show(json_err or 'not an object')}"
    return meta, None


def _execute_rebaseline_locked(args: argparse.Namespace, paths: dict[str, str]) -> tuple[int | None, int]:
    inv_err = _invalidate_receipt_under_lock(paths["receipt"])
    if inv_err is not None:
        return None, _err(inv_err, code=2)
    baseline_root = os.path.join(paths["job_dir"], "baseline")
    sym_err = _find_symlink_in_paths((paths["job_dir"], paths["manifest_dir"], baseline_root))
    if sym_err is not None:
        return None, _err(sym_err, code=2)
    m_gen, b_gen, gen_err = _discover_current_gens(paths["job_dir"])
    if gen_err is not None or m_gen is None or b_gen is None:
        return None, _err(gen_err or "corrupt state", code=2)
    manifest, load_err = _load_stored_manifest(args.state_root, args.name, m_gen)
    if load_err is not None or manifest is None:
        return None, _err(load_err or "corrupt state", code=2)
    cur_meta, meta_err = _load_baseline_meta_strict(args.state_root, args.name, b_gen)
    if meta_err is not None or cur_meta is None:
        return None, _err(meta_err or "corrupt baseline meta", code=2)
    golden = manifest["golden"]
    if "waived" in golden and "waived" in cur_meta:
        return None, _err("golden waived; nothing to rebaseline", code=2)
    next_b = _state.next_gen_number(b_gen)
    b_paths = _state.job_paths(args.state_root, args.name, gen=next_b)
    if _lstat_if_exists(b_paths["baseline_dir"]) is not None:
        return None, _err(f"incomplete baseline gen-{next_b} present; remove it manually", code=2)
    run_data: dict[str, Any] | None = None
    if "waived" not in golden:
        run_data, g_code = _capture_golden_twice(golden, float(args.timeout))
        if run_data is None:
            return None, g_code
    w_err = _write_rebaseline_gen(
        b_paths, golden, run_data, reason=args.reason, prev_gen=b_gen, manifest_gen=m_gen,
    )
    if w_err is not None:
        return None, _err(f"baseline write error: {_show(w_err)}", code=2)
    return next_b, 0


def _precheck_begun_job_state(state_root: str, paths: dict[str, str]) -> int | None:
    baseline_root = os.path.join(paths["job_dir"], "baseline")
    try:
        sym_err = _find_symlink_in_paths((state_root, paths["job_dir"], paths["manifest_dir"], baseline_root))
        if sym_err is not None:
            return _err(sym_err, code=2)
        job_st = _lstat_if_exists(paths["job_dir"])
        if job_st is None or not stat.S_ISDIR(job_st.st_mode):
            return _err(_NOT_BEGUN_MSG, code=2)
    except OSError as exc:
        return _err(f"cannot access state: {_show(exc)}", code=2)
    return None


def _cmd_rebaseline(args: argparse.Namespace) -> int:
    if args.reason is None or len(args.reason.strip()) < _state.MIN_NOTE_CHARS:
        return _err(f"rebaseline reason must be at least {_state.MIN_NOTE_CHARS} characters after trimming", code=2)
    paths = _state.job_paths(args.state_root, args.name, gen=1)
    pre_err = _precheck_begun_job_state(args.state_root, paths)
    if pre_err is not None:
        return pre_err
    lock_fd, lock_err = _acquire_job_lock(paths["lock"])
    if lock_err is not None:
        return _err(lock_err, code=2)
    try:
        try:
            next_b, err_code = _execute_rebaseline_locked(args, paths)
        except OSError as exc:
            return _err(f"filesystem error: {type(exc).__name__}: {_show(exc)}", code=2)
    finally:
        _io.release_lock(lock_fd)
    if next_b is None:
        return err_code
    if args.json:
        print(_dumps_json({"ok": True, "exit": 0, "name": args.name, "baseline_gen": next_b, "reason": args.reason}))
    else:
        print(f"rebaselined {args.name}: gen-{next_b} ({_show(args.reason)})")
    return 0


def _append_amend_log(manifest_dir: str, entry: dict[str, Any]) -> str | None:
    log_path = os.path.join(manifest_dir, "amend-log.json")
    entries: list[Any] = []
    if _lstat_if_exists(log_path) is not None:
        sym_err = _find_symlink_in_paths((log_path,))
        if sym_err is not None:
            return sym_err
        res = _read_text(log_path)
        if res["error"] is not None or res["text"] is None:
            return f"cannot read amend-log.json: {_show(res['error'])}"
        parsed, json_err = _state.parse_json_strict(res["text"])
        if json_err is not None:
            return f"invalid amend-log.json: {_show(json_err)}"
        if not isinstance(parsed, list):
            return "corrupt amend-log.json: expected list"
        entries = parsed
    target_path = os.path.join(manifest_dir, f"gen-{entry.get('gen')}.json")
    committed = _lstat_if_exists(target_path) is not None
    merged = _state.merge_amend_log_entries(entries, entry, target_gen_committed=committed)
    return _write_json_or_err(log_path, merged)


def _execute_amend_locked(
    args: argparse.Namespace, paths: dict[str, str],
) -> tuple[tuple[int, bool] | None, int]:
    inv_err = _invalidate_receipt_under_lock(paths["receipt"])
    if inv_err is not None:
        return None, _err(inv_err, code=2)
    baseline_root = os.path.join(paths["job_dir"], "baseline")
    sym_err = _find_symlink_in_paths((paths["job_dir"], paths["manifest_dir"], baseline_root))
    if sym_err is not None:
        return None, _err(sym_err, code=2)
    m_gen, m_err = _check_begun_manifest_gen(paths["manifest_dir"])
    if m_err is not None or m_gen is None:
        return None, _err(m_err or _NOT_BEGUN_MSG, code=2)
    new_manifest, v_code = _load_and_validate_manifest(args.manifest, args.name)
    if new_manifest is None:
        return None, v_code
    prev_manifest, load_err = _load_stored_manifest(args.state_root, args.name, m_gen)
    if load_err is not None or prev_manifest is None:
        return None, _err(load_err or "corrupt state", code=2)
    if _state.format_json(new_manifest) == _state.format_json(prev_manifest):
        return None, _err("no change", code=2)
    next_m = _state.next_gen_number(m_gen)
    entry = _state.build_amend_log_entry(gen=next_m, reason=args.reason, at=_now_iso(), previous_gen=m_gen)
    log_err = _append_amend_log(paths["manifest_dir"], entry)
    if log_err is not None:
        return None, _err(log_err, code=2)
    next_paths = _state.job_paths(args.state_root, args.name, gen=next_m)
    m_write_err = _write_json_or_err(next_paths["manifest_gen"], new_manifest)
    if m_write_err is not None:
        return None, _err(f"manifest write error: {_show(m_write_err)}", code=2)
    g_changed = _state.golden_config_changed(prev_manifest["golden"], new_manifest["golden"])
    return (next_m, g_changed), 0


def _cmd_begin_amend(args: argparse.Namespace) -> int:
    if args.reason is None or len(args.reason.strip()) < _state.MIN_NOTE_CHARS:
        return _err(f"amend reason must be at least {_state.MIN_NOTE_CHARS} characters after trimming", code=2)
    paths = _state.job_paths(args.state_root, args.name, gen=1)
    pre_err = _precheck_begun_job_state(args.state_root, paths)
    if pre_err is not None:
        return pre_err
    lock_fd, lock_err = _acquire_job_lock(paths["lock"])
    if lock_err is not None:
        return _err(lock_err, code=2)
    try:
        try:
            res, err_code = _execute_amend_locked(args, paths)
        except OSError as exc:
            return _err(f"filesystem error: {type(exc).__name__}: {_show(exc)}", code=2)
    finally:
        _io.release_lock(lock_fd)
    if res is None:
        return err_code
    next_m, g_changed = res
    if args.json:
        print(_dumps_json({
            "ok": True,
            "exit": 0,
            "name": args.name,
            "manifest_gen": next_m,
            "reason": args.reason,
            "golden_changed": g_changed,
        }))
    else:
        print(f"amended {args.name}: manifest gen-{next_m}")
        if g_changed:
            print("golden changed; run componentize rebaseline")
    return 0


def _check_err(name: str, msg: str) -> int:
    return _err(msg, code=2, extra=_state.build_check_error_extra(name))


def _check_err_locked(name: str, last_check_path: str, msg: str) -> int:
    err_obj = _state.build_error_last_check(name=name, error=msg, at=_now_iso())
    write_err = _write_json_or_err(last_check_path, err_obj)
    full_msg = f"{msg} (last_check_write_error: {_show(write_err)})" if write_err is not None else msg
    return _check_err(name, full_msg)


def _find_symlink_in_paths(paths_list: tuple[str, ...]) -> str | None:
    for p in paths_list:
        st = _lstat_if_exists(p)
        if st is not None and stat.S_ISLNK(st.st_mode):
            return f"state path is a symlink, refusing: {_show(p)}"
    return None


def _check_begun_manifest_gen(manifest_dir: str) -> tuple[int | None, str | None]:
    m_st = _lstat_if_exists(manifest_dir)
    if m_st is not None and stat.S_ISLNK(m_st.st_mode):
        return None, f"state path is a symlink, refusing: {_show(manifest_dir)}"
    if m_st is None or not stat.S_ISDIR(m_st.st_mode):
        return None, _NOT_BEGUN_MSG
    entries = sorted(_listdir(manifest_dir))
    for name in entries:
        p = os.path.join(manifest_dir, name)
        st = _lstat_if_exists(p)
        if st is not None and stat.S_ISLNK(st.st_mode):
            return None, f"state path is a symlink, refusing: {_show(p)}"
    m_gen = _state.select_current_gen(entries, suffix=".json")
    if m_gen is None:
        return None, _NOT_BEGUN_MSG
    return m_gen, None


def _discover_current_gens(job_dir: str) -> tuple[int | None, int | None, str | None]:
    manifest_dir = os.path.join(job_dir, "manifest")
    baseline_root = os.path.join(job_dir, "baseline")
    m_gen, m_err = _check_begun_manifest_gen(manifest_dir)
    if m_err is not None:
        return None, None, m_err
    b_st = _lstat_if_exists(baseline_root)
    if b_st is not None and stat.S_ISLNK(b_st.st_mode):
        return None, None, f"state path is a symlink, refusing: {_show(baseline_root)}"
    if b_st is None or not stat.S_ISDIR(b_st.st_mode):
        return None, None, "corrupt state: missing manifest or baseline directory"
    b_candidates: list[str] = []
    for name in sorted(_listdir(baseline_root)):
        b_dir = os.path.join(baseline_root, name)
        meta_p = os.path.join(b_dir, "meta.json")
        dir_st = _lstat_if_exists(b_dir)
        if dir_st is not None and stat.S_ISLNK(dir_st.st_mode):
            return None, None, f"state path is a symlink, refusing: {_show(b_dir)}"
        meta_st = _lstat_if_exists(meta_p)
        if meta_st is not None and stat.S_ISLNK(meta_st.st_mode):
            return None, None, f"state path is a symlink, refusing: {_show(meta_p)}"
        if dir_st is not None and stat.S_ISDIR(dir_st.st_mode) and meta_st is not None and stat.S_ISREG(meta_st.st_mode):
            b_candidates.append(name)
    b_gen = _state.select_current_gen(b_candidates, suffix="")
    if m_gen is None or b_gen is None:
        return None, None, "corrupt state: missing manifest or baseline generation"
    return m_gen, b_gen, None


def _stat_fp_for_path(path: str) -> tuple[int | None, int | None]:
    st = _lstat_if_exists(path)
    return (st.st_ino, st.st_ctime_ns) if st is not None else (None, None)


def _read_baseline_capture(
    b_paths: dict[str, str],
) -> tuple[dict[str, Any] | None, dict[str, str], dict[str, tuple[int | None, int | None]], str | None]:
    shas: dict[str, str] = {}
    fps: dict[str, tuple[int | None, int | None]] = {}
    texts: dict[str, str] = {}
    for label, pkey in (("stdout", "baseline_stdout"), ("stderr", "baseline_stderr"), ("rc", "baseline_rc")):
        p = b_paths[pkey]
        res = _read_text(p)
        if res["error"] is not None or res["text"] is None:
            return None, {}, {}, f"cannot read baseline {label}: {_show(res['error'])}"
        texts[label] = str(res["text"])
        shas[label] = _state.sha256_text(texts[label])
        fps[p] = _stat_fp_for_path(p)
    try:
        rc_int = int(texts["rc"].strip())
    except ValueError:
        return None, {}, {}, "corrupt baseline rc: not an integer"
    return {"stdout": texts["stdout"], "stderr": texts["stderr"], "rc": rc_int}, shas, fps, None


def _load_check_state(
    state_root: str, name: str, m_gen: int, b_gen: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    m_paths = _state.job_paths(state_root, name, gen=m_gen)
    b_paths = _state.job_paths(state_root, name, gen=b_gen)
    m_res = _read_text(m_paths["manifest_gen"])
    if m_res["error"] is not None or m_res["text"] is None:
        return None, None, f"cannot read manifest gen-{m_gen}: {_show(m_res['error'])}"
    b_meta_res = _read_text(b_paths["baseline_meta"])
    if b_meta_res["error"] is not None or b_meta_res["text"] is None:
        return None, None, f"cannot read baseline meta gen-{b_gen}: {_show(b_meta_res['error'])}"
    m_sha, meta_sha = _state.sha256_text(m_res["text"]), _state.sha256_text(b_meta_res["text"])
    manifest, m_err = _state.parse_json_strict(m_res["text"])
    meta, b_err = _state.parse_json_strict(b_meta_res["text"])
    if m_err is not None or b_err is not None:
        return None, None, f"corrupt state JSON: {_show(m_err or b_err)}"
    val_res = componentize_evidence.validate_manifest(manifest)
    if val_res.get("ok") and val_res.get("name") != name:
        return None, None, f"corrupt state: manifest name {val_res.get('name')!r} does not match {name!r}"
    state_hashes: dict[str, str | None] = {m_paths["manifest_gen"]: m_sha, b_paths["baseline_meta"]: meta_sha}
    state_fps = {m_paths["manifest_gen"]: _stat_fp_for_path(m_paths["manifest_gen"]), b_paths["baseline_meta"]: _stat_fp_for_path(b_paths["baseline_meta"])}
    b_shas: dict[str, str] = {"meta.json": meta_sha}
    if not isinstance(meta, dict) or not isinstance(manifest, dict) or "waived" in meta or "waived" in manifest.get("golden", {}):
        cons_err = _state.validate_manifest_meta_consistency(val_res, manifest, meta, b_gen=b_gen)
        return (None, None, cons_err) if cons_err is not None else (manifest, {"meta": meta, "capture": None, "manifest_sha": m_sha, "baseline_shas": b_shas, "state_hashes": state_hashes, "state_fps": state_fps}, None)
    cap, cap_shas, cap_fps, cap_err = _read_baseline_capture(b_paths)
    if cap is None:
        return None, None, cap_err
    cons_err = _state.validate_manifest_meta_consistency(val_res, manifest, meta, b_gen=b_gen, cap_shas=cap_shas)
    if cons_err is not None:
        return None, None, cons_err
    for label, pkey in (("stdout", "baseline_stdout"), ("stderr", "baseline_stderr"), ("rc", "baseline_rc")):
        b_shas[label] = cap_shas[label]
        state_hashes[b_paths[pkey]] = cap_shas[label]
    state_fps.update(cap_fps)
    return manifest, {"meta": meta, "capture": cap, "manifest_sha": m_sha, "baseline_shas": b_shas, "state_hashes": state_hashes, "state_fps": state_fps}, None


def _hash_declared_paths(
    paths_list: list[str],
) -> tuple[
    dict[str, str | None],
    dict[str, tuple[int | None, int | None]],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
]:
    hashes: dict[str, str | None] = {}
    fps: dict[str, tuple[int | None, int | None]] = {}
    unr_map: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, Any]] = []
    for p in paths_list:
        res = _sha256_file(p)
        if res["error"] is not None or res["sha256"] is None:
            hashes[p] = None
            fps[p] = (None, None)
            finding = _state.build_unreadable_finding(p, str(res["error"]))
            unr_map[p] = finding
            findings.append(finding)
        else:
            hashes[p] = str(res["sha256"])
            fps[p] = _state.extract_stat_fingerprint(res)
    return hashes, fps, unr_map, findings


def _gate_single_file(
    entry: dict[str, Any],
    index_text: str | None,
    h1: dict[str, str | None],
    unr_map: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    f_path = str(entry["path"])
    role = str(entry["role"])
    if h1.get(f_path) is None:
        unr = unr_map.get(f_path) or _state.build_unreadable_finding(f_path, "unreadable")
        return {"path": f_path, "role": role, "status": "error", "findings": [unr]}, [unr]
    src_res = _read_text(f_path)
    if src_res["error"] is not None or src_res["text"] is None:
        h1[f_path] = None
        unr = _state.build_unreadable_finding(f_path, str(src_res["error"]))
        return {"path": f_path, "role": role, "status": "error", "findings": [unr]}, [unr]
    vs_text: str | None = None
    if role == "vendored":
        vs_path = str(entry["vendor_source"])
        if h1.get(vs_path) is None:
            return {"path": f_path, "role": role, "status": "error", "findings": []}, []
        vs_res = _read_text(vs_path)
        if vs_res["error"] is not None or vs_res["text"] is None:
            h1[vs_path] = None
            unr = _state.build_unreadable_finding(vs_path, str(vs_res["error"]))
            return {"path": f_path, "role": role, "status": "error", "findings": []}, [unr]
        vs_text = vs_res["text"]
    idx = index_text if role == "component" else None
    audit_res = architecture_gate.audit_module(f_path, source=src_res["text"], role=role, index_text=idx, vendor_source=vs_text)
    return _state.normalize_gate_audit_result(f_path, role, audit_res)


def _run_check_gates(
    files: list[dict[str, Any]],
    index_path: str,
    h1: dict[str, str | None],
    unr_map: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    index_text: str | None = None
    findings: list[dict[str, Any]] = []
    if any(entry.get("role") == "component" for entry in files) and h1.get(index_path) is not None:
        idx_res = _read_text(index_path)
        if idx_res["error"] is not None or idx_res["text"] is None:
            h1[index_path] = None
            findings.append(_state.build_unreadable_finding(index_path, str(idx_res["error"])))
        else:
            index_text = idx_res["text"]
    gate_list: list[dict[str, Any]] = []
    for entry in files:
        g_item, g_findings = _gate_single_file(entry, index_text, h1, unr_map)
        gate_list.append(g_item)
        findings.extend(g_findings)
    return gate_list, findings


def _run_check_golden(
    golden_cfg: dict[str, Any], baseline: dict[str, Any], timeout: float,
) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
    meta = baseline["meta"]
    if "waived" in meta:
        return {"waived": golden_cfg["waived"], "streams": {}}, [], None
    run_res = _io.run_argv(
        golden_cfg["argv"], cwd=golden_cfg["cwd"], env=golden_cfg["env"], timeout=timeout,
    )
    trunc_stream = _state.detect_truncated_stream(run_res)
    if trunc_stream is not None:
        return {"matched": False, "streams": {}}, [], f"output exceeds limit ({trunc_stream})"
    if run_res["timed_out"] or run_res["error"] is not None:
        return {"matched": False, "streams": {}}, [], f"golden run failed: {_show(run_res['error'])}"
    cmp_res = componentize_evidence.compare_capture(
        baseline["capture"], run_res, compare=golden_cfg["compare"],
    )
    if cmp_res["status"] == "error":
        return {"matched": False, "streams": {}}, [], f"compare error: {_show(cmp_res['error'])}"
    return (
        {"matched": cmp_res["matched"], "streams": cmp_res["streams"]},
        list(cmp_res["findings"]),
        None,
    )


def _run_check_tests(
    tests_cfg: dict[str, Any], timeout: float,
) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
    if "waived" in tests_cfg:
        return {"waived": tests_cfg["waived"]}, [], None
    with tempfile.TemporaryDirectory(prefix="componentize-home-") as tmp_home:
        test_env = {
            "PATH": os.environ.get("PATH", ""),
            # Portable: C exists everywhere (C.UTF-8 is unreliable on macOS);
            # PYTHONUTF8 (PEP 540) keeps Python children on UTF-8 I/O.
            "LC_ALL": "C",
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": tmp_home,
        }
        run_res = _io.run_argv(
            tests_cfg["argv"], cwd=tests_cfg["cwd"], env=test_env, timeout=timeout,
        )
    trunc_stream = _state.detect_truncated_stream(run_res)
    if trunc_stream is not None:
        return {"rc": None}, [], f"output exceeds limit ({trunc_stream})"
    if run_res["timed_out"] or run_res["error"] is not None:
        return {"rc": None}, [], f"tests run failed: {_show(run_res['error'])}"
    rc_val = int(run_res["rc"])
    if rc_val != 0:
        finding = _state.build_tests_failed_finding(rc_val, run_res["stdout"], run_res["stderr"])
        return {"rc": rc_val}, [finding], None
    return {"rc": 0}, [], None


def _save_last_check(last_check_path: str, **kwargs: Any) -> tuple[dict[str, Any], str | None]:
    obj = _state.build_last_check(**kwargs, at=_now_iso(), sha_fn=componentize_evidence.canonical_sha256)
    err = _write_json_or_err(last_check_path, obj)
    return obj, err


def _read_amend_log_sha(manifest_dir: str) -> tuple[str | None, str | None]:
    log_path = os.path.join(manifest_dir, "amend-log.json")
    st = _lstat_if_exists(log_path)
    if st is None:
        return None, None
    if stat.S_ISLNK(st.st_mode):
        return None, f"state path is a symlink, refusing: {_show(log_path)}"
    res = _sha256_file(log_path)
    if res["error"] is not None or res["sha256"] is None:
        return None, f"unreadable: {_show(log_path)}"
    return str(res["sha256"]), None


def _collect_check_start_evidence(
    attest_path: str, manifest_dir: str,
) -> tuple[str | None, str, str | None, str | None, str | None]:
    # Fingerprints the checker code that actually runs (_TOOLS_ROOT), not --tools-root (which only selects COMPONENTS.md).
    c_digest, c_err = _compute_checker_digest(_TOOLS_ROOT)
    if c_err is not None or c_digest is None:
        return None, "", None, None, c_err or "cannot compute checker digest"
    _, att_sha, att_err = _load_and_validate_attest(attest_path)
    if att_err is not None or att_sha is None:
        return None, "", None, None, att_err or "cannot read attest.json"
    amend_sha, amend_err = _read_amend_log_sha(manifest_dir)
    if amend_err is not None:
        return None, "", None, None, amend_err
    return c_digest, _runtime_string(), att_sha, amend_sha, None


def _run_tool_schemas(manifest, *, binary_warnings=()):
    return [_plugins.capture(spec, binary_warnings=[f for f in binary_warnings if f['plugin_path'] == spec['path']])
            for spec in manifest.get('hermes_plugins', [])]


def _run_check_verification(
    args: argparse.Namespace, paths: dict[str, str], m_gen: int, b_gen: int,
    manifest: dict[str, Any], baseline: dict[str, Any],
) -> tuple[dict[str, Any] | None, int]:
    c_digest, runtime, att_sha, amend_sha, start_err = _collect_check_start_evidence(paths["attest"], paths["manifest_dir"])
    if start_err is not None or c_digest is None or att_sha is None:
        return None, _check_err_locked(args.name, paths["last_check"], start_err or "check start error")
    index_path = _index_path(args.tools_root)
    hash_paths = _state.collect_hash_paths(manifest["files"], index_path, manifest.get("inputs"))
    h1, fp1, unr_map, findings = _hash_declared_paths(hash_paths)
    if index_path in unr_map and not os.path.lexists(index_path):
        # Nothing there at all: say which index is required, not a bare FileNotFoundError.
        missing = _index_missing_finding(args.tools_root, manifest["files"])
        findings = [missing if f is unr_map[index_path] else f for f in findings]
        unr_map[index_path] = missing
    findings.extend(_find_duplicate_inode_findings(manifest))
    inventory_sha, inventory_findings = _plugins.inventory(manifest, include_warnings=True)
    binary_warnings = [f for f in inventory_findings if f['severity'] == 'warn']
    inventory_findings = [f for f in inventory_findings if f['severity'] != 'warn']
    findings.extend(inventory_findings)
    gate_list, gate_findings = _run_check_gates(manifest["files"], index_path, h1, unr_map)
    findings.extend(gate_findings)
    timeout = float(args.timeout)
    reports = _run_tool_schemas(manifest, binary_warnings=binary_warnings)
    for report in reports:
        findings.extend(report["findings"])
    golden_sec, golden_findings, golden_err = _run_check_golden(manifest["golden"], baseline, timeout)
    findings.extend(golden_findings)
    if golden_err is not None:
        return None, _check_err_locked(args.name, paths["last_check"], golden_err)
    tests_sec, tests_findings, tests_err = _run_check_tests(manifest["tests"], timeout)
    findings.extend(tests_findings)
    if tests_err is not None:
        return None, _check_err_locked(args.name, paths["last_check"], tests_err)
    state_h1: dict[str, str | None] = dict(baseline["state_hashes"])
    state_fp1: dict[str, tuple[int | None, int | None]] = dict(baseline["state_fps"])
    all_h2, all_fp2, _, _ = _hash_declared_paths([*state_h1.keys(), *hash_paths])
    changed = _state.diff_file_hashes({**state_h1, **h1}, all_h2, {**state_fp1, **fp1}, all_fp2)
    inventory_after, inventory_errors = _plugins.inventory(manifest)
    if inventory_after != inventory_sha or inventory_errors != inventory_findings:
        changed.append("plugin inventory")
    if changed:
        return None, _check_err_locked(args.name, paths["last_check"], f"inputs changed during check: {_show(', '.join(changed))}")
    evidence = _state.assemble_current_evidence(
        manifest_sha=baseline["manifest_sha"], manifest_gen=m_gen,
        baseline_gen=b_gen, baseline_shas=baseline["baseline_shas"], file_hashes=h1,  # type: ignore[arg-type]
        checker_digest=c_digest, runtime=runtime, attestations_sha=att_sha, amend_log_sha=amend_sha,
    )
    last_check, write_err = _save_last_check(paths["last_check"], name=args.name, evidence=evidence, gate=gate_list, golden=golden_sec, tests=tests_sec, findings=findings, tool_schemas=reports if manifest.get("hermes_plugins") else None, plugin_inventory_sha=inventory_sha)
    if write_err is not None:
        return None, _check_err(args.name, f"cannot write last_check.json (last_check_write_error: {_show(write_err)})")
    return last_check, 0


def _invalidate_receipt_under_lock(receipt_path: str) -> str | None:
    st = _lstat_if_exists(receipt_path)
    if st is None:
        return None
    if stat.S_ISLNK(st.st_mode):
        return f"state path is a symlink, refusing: {_show(receipt_path)}"
    try:
        _unlink(receipt_path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"cannot remove stale receipt.json: {_show(exc)}"
    return None


def _execute_check_locked(
    args: argparse.Namespace, paths: dict[str, str],
) -> tuple[dict[str, Any] | None, int]:
    inv_err = _invalidate_receipt_under_lock(paths["receipt"])
    if inv_err is not None:
        return None, _check_err_locked(args.name, paths["last_check"], inv_err)
    sym_err = _find_symlink_in_paths((paths["job_dir"], paths["manifest_dir"], paths["last_check"]))
    if sym_err is not None:
        return None, _check_err_locked(args.name, paths["last_check"], sym_err)
    m_gen, b_gen, gen_err = _discover_current_gens(paths["job_dir"])
    if gen_err is not None or m_gen is None or b_gen is None:
        return None, _check_err_locked(args.name, paths["last_check"], gen_err or "corrupt state")
    manifest, baseline, load_err = _load_check_state(args.state_root, args.name, m_gen, b_gen)
    if load_err is not None or manifest is None or baseline is None:
        return None, _check_err_locked(args.name, paths["last_check"], load_err or "corrupt state")
    return _run_check_verification(args, paths, m_gen, b_gen, manifest, baseline)


def _emit_check_output(last_check: dict[str, Any], as_json: bool) -> int:
    passed = last_check["status"] == "pass"
    exit_code = 0 if passed else 1
    if as_json:
        print(_dumps_json({
            "ok": passed,
            "exit": exit_code,
            "name": last_check["name"],
            "status": last_check["status"],
            "findings": last_check["findings"],
            "check_result_sha": last_check["check_result_sha"],
        }))
    else:
        print(_show_block(_state.format_check_text(last_check)))
    return exit_code


def _cmd_check(args: argparse.Namespace) -> int:
    paths = _state.job_paths(args.state_root, args.name, gen=1)
    try:
        sym_err = _find_symlink_in_paths((args.state_root, paths["job_dir"]))
        if sym_err is not None:
            return _check_err(args.name, sym_err)
        job_st = _lstat_if_exists(paths["job_dir"])
        if job_st is None or not stat.S_ISDIR(job_st.st_mode):
            return _check_err(args.name, _NOT_BEGUN_MSG)
    except OSError as exc:
        return _check_err(args.name, f"cannot access state: {_show(exc)}")
    lock_fd, lock_err = _acquire_job_lock(paths["lock"])
    if lock_err is not None:
        return _check_err(args.name, lock_err)
    try:
        try:
            last_check, err_code = _execute_check_locked(args, paths)
        except OSError as exc:
            return _check_err_locked(args.name, paths["last_check"], f"filesystem error: {type(exc).__name__}: {_show(exc)}")
    finally:
        _io.release_lock(lock_fd)
    if last_check is None:
        return err_code
    return _emit_check_output(last_check, bool(args.json))


def _runtime_string() -> str:
    return f"{sys.implementation.name} {platform.python_version()} {sys.executable}"


def _get_checker_files(repo_root: str = _TOOLS_ROOT) -> list[tuple[str, str]]:
    relpaths = [
        "hermes_audit/__init__.py",
        "hermes_audit/architecture_gate.py",
        "hermes_audit/componentize_evidence.py",
        "hermes_audit/tool_schema_cost.py",
    ]
    comp_dir = os.path.join(repo_root, "componentize")
    st = _lstat_if_exists(comp_dir)
    if st is not None and stat.S_ISDIR(st.st_mode):
        for entry in sorted(os.listdir(comp_dir)):
            if entry.endswith(".py") and not entry.startswith("test_") and not entry.endswith("_test.py"):
                relpaths.append(f"componentize/{entry}")
    return [(rel, os.path.join(repo_root, rel)) for rel in sorted(set(relpaths))]


def _compute_checker_digest(repo_root: str = _TOOLS_ROOT) -> tuple[str | None, str | None]:
    checker_shas: dict[str, str] = {}
    for rel, full_path in _get_checker_files(repo_root):
        res = _sha256_file(full_path)
        if res["error"] is not None or res["sha256"] is None:
            return None, f"unreadable: {_show(full_path)}"
        checker_shas[rel] = str(res["sha256"])
    return componentize_evidence.canonical_sha256(checker_shas), None


def _compute_baseline_shas(job_dir: str, b_gen: int) -> tuple[dict[str, str] | None, str | None]:
    b_dir = os.path.join(job_dir, "baseline", f"gen-{b_gen}")
    meta_path = os.path.join(b_dir, "meta.json")
    meta_res = _read_text(meta_path)
    if meta_res["error"] is not None or meta_res["text"] is None:
        return None, f"unreadable: {_show(meta_path)}"
    meta, json_err = _state.parse_json_strict(meta_res["text"])
    if json_err is not None or not isinstance(meta, dict):
        return None, f"corrupt baseline meta {_show(meta_path)}: {_show(json_err or 'not an object')}"
    waived = "waived" in meta
    if waived and (not isinstance(meta.get("waived"), str) or not meta["waived"].strip()):
        return None, f"corrupt baseline meta {_show(meta_path)}: invalid waived value"
    shas: dict[str, str] = {}
    for fname in ("stdout", "stderr", "rc", "meta.json"):
        fpath = os.path.join(b_dir, fname)
        if fname != "meta.json" and waived and _lstat_if_exists(fpath) is None:
            continue
        res = _sha256_file(fpath)
        if res["error"] is not None or res["sha256"] is None:
            return None, f"unreadable: {_show(fpath)}"
        shas[fname] = str(res["sha256"])
    return shas, None


def _compute_current_evidence(
    paths: dict[str, str], manifest: dict[str, Any], M: int, B: int, tools_root: str,
) -> tuple[dict[str, Any] | None, str | None]:
    m_path = os.path.join(paths["manifest_dir"], f"gen-{M}.json")
    m_sha = _sha256_file(m_path)
    if m_sha["error"] is not None or m_sha["sha256"] is None:
        return None, f"unreadable: {_show(m_path)}"
    b_shas, b_err = _compute_baseline_shas(paths["job_dir"], B)
    if b_err is not None or b_shas is None:
        return None, b_err
    c_digest, runtime, att_sha, amend_sha, start_err = _collect_check_start_evidence(paths["attest"], paths["manifest_dir"])
    if start_err is not None or c_digest is None or att_sha is None:
        return None, start_err or f"unreadable: {_show(paths['attest'])}"
    index_path = _index_path(tools_root)
    f_hashes: dict[str, str] = {}
    for fpath in _state.collect_hash_paths(manifest["files"], index_path, manifest.get("inputs")):
        f_res = _sha256_file(fpath)
        if f_res["error"] is not None or f_res["sha256"] is None:
            return None, f"unreadable: {_show(fpath)}"
        f_hashes[fpath] = str(f_res["sha256"])
    return _state.assemble_current_evidence(
        manifest_sha=str(m_sha["sha256"]), manifest_gen=M, baseline_gen=B,
        baseline_shas=b_shas, file_hashes=f_hashes, checker_digest=c_digest,
        runtime=runtime, attestations_sha=att_sha, amend_log_sha=amend_sha,
    ), None


def _verify_finish_attestations(name: str, kind: str, attest_path: str) -> int:
    att_data, _, att_err = _load_and_validate_attest(attest_path)
    if att_err is not None or att_data is None:
        return _err(att_err or "corrupt attest.json", code=2, extra={"name": name, "evidence_digest": None})
    missing, req_err = _state.missing_required_attestations(kind, att_data)
    if req_err is not None:
        return _err(req_err, code=2, extra={"name": name, "evidence_digest": None})
    if missing:
        msg = f"finish-missing-attestation: {_show(', '.join(missing))}"
        return _err(msg, code=1, extra={"name": name, "evidence_digest": None})
    return 0


def _read_verified_last_check(last_check_path: str) -> tuple[dict[str, Any] | None, str | None]:
    sym_err = _find_symlink_in_paths((last_check_path,))
    if sym_err is not None:
        return None, sym_err
    res = _read_text(last_check_path)
    if res["error"] is not None or res["text"] is None:
        return None, f"cannot read last_check.json: {_show(res['error'])}"
    data, json_err = _state.parse_json_strict(res["text"])
    if json_err is not None or not isinstance(data, dict):
        return None, f"corrupt last_check.json: {_show(json_err or 'not an object')}"
    core = {k: v for k, v in data.items() if k not in ("at", "check_result_sha")}
    if data.get("check_result_sha") != componentize_evidence.canonical_sha256(core):
        return None, "corrupt last_check.json: check_result_sha mismatch"
    return data, None


def _verify_disk_last_check(last_check_path: str) -> str | None:
    return _read_verified_last_check(last_check_path)[1]


def _build_and_write_receipt_locked(
    args: argparse.Namespace, paths: dict[str, str], last_check: dict[str, Any],
) -> tuple[dict[str, Any] | None, int]:
    ext = {"name": args.name, "evidence_digest": None}
    lc_err = _verify_disk_last_check(paths["last_check"])
    if lc_err is not None:
        return None, _err(lc_err, code=2, extra=ext)
    cur_m, cur_b, gen_err = _discover_current_gens(paths["job_dir"])
    if gen_err is not None or cur_m is None or cur_b is None:
        return None, _err(gen_err or "corrupt state", code=2, extra=ext)
    manifest, load_err = _load_stored_manifest(args.state_root, args.name, cur_m)
    if load_err is not None or manifest is None:
        return None, _err(load_err or "corrupt state", code=2, extra=ext)
    att_code = _verify_finish_attestations(args.name, str(manifest["kind"]), paths["attest"])
    if att_code != 0:
        return None, att_code
    index_path = _index_path(args.tools_root)
    expected_set = set(_state.collect_hash_paths(manifest["files"], index_path, manifest.get("inputs")))
    lc_hashes = last_check.get("file_hashes")
    if not isinstance(lc_hashes, dict) or not expected_set or set(lc_hashes.keys()) != expected_set:
        return None, _err("coverage mismatch", code=2, extra=ext)
    disk_ev, cur_err = _compute_current_evidence(paths, manifest, cur_m, cur_b, args.tools_root)
    if cur_err is not None or disk_ev is None:
        return None, _err(cur_err or "cannot compute current evidence", code=2, extra=ext)
    drifted = _state.diff_checked_state_evidence(last_check, disk_ev)
    if drifted:
        return None, _err(f"state changed after check: {_show(', '.join(drifted))}", code=2, extra=ext)
    sym_err = _find_symlink_in_paths((paths["receipt"],))
    if sym_err is not None:
        return None, _err(sym_err, code=2, extra=ext)
    checked_ev = _state.assemble_checked_receipt_evidence(last_check)
    receipt = _state.build_receipt(
        name=args.name, current=checked_ev, last_check=last_check,
        created_at=_now_iso(), digest_fn=componentize_evidence.evidence_digest,
        receipt_keys=componentize_evidence.RECEIPT_KEYS,
    )
    w_err = _write_json_or_err(paths["receipt"], receipt)
    return (None, _err(f"cannot write receipt.json: {_show(w_err)}", code=2, extra=ext)) if w_err is not None else (receipt, 0)


def _cmd_finish(args: argparse.Namespace) -> int:
    paths = _state.job_paths(args.state_root, args.name, gen=1)
    try:
        sym_err = _find_symlink_in_paths((args.state_root, paths["job_dir"]))
        if sym_err is not None:
            return _check_err(args.name, sym_err)
        job_st = _lstat_if_exists(paths["job_dir"])
        if job_st is None or not stat.S_ISDIR(job_st.st_mode):
            return _check_err(args.name, _NOT_BEGUN_MSG)
    except OSError as exc:
        return _check_err(args.name, f"cannot access state: {_show(exc)}")
    lock_fd, lock_err = _acquire_job_lock(paths["lock"])
    if lock_err is not None:
        return _check_err(args.name, lock_err)
    try:
        try:
            last_check, err_code = _execute_check_locked(args, paths)
            if last_check is None:
                return err_code
            if last_check["status"] != "pass":
                return _emit_check_output(last_check, bool(args.json))
            receipt, r_code = _build_and_write_receipt_locked(args, paths, last_check)
        except OSError as exc:
            return _err(f"filesystem error: {type(exc).__name__}: {_show(exc)}", code=2, extra={"name": args.name, "evidence_digest": None})
    finally:
        _io.release_lock(lock_fd)
    if receipt is None:
        return r_code
    digest = str(receipt["evidence_digest"])
    if args.json:
        print(_dumps_json({"ok": True, "exit": 0, "name": args.name, "evidence_digest": digest}))
    else:
        print(f"finish {args.name}: receipt {digest[:12]}")
    return 0


def _status_err(name: str, msg: str, *, evidence_digest: str | None = None) -> int:
    extra = _state.build_status_payload(
        ok=False, exit_code=2, name=name, status="error",
        stale=[], findings=[], evidence_digest=evidence_digest,
    )
    return _err(msg, code=2, extra=extra)


def _emit_status_stale(
    name: str,
    stale: list[str],
    findings: list[dict[str, Any]],
    evidence_digest: str | None,
    as_json: bool,
) -> int:
    if as_json:
        print(_dumps_json(_state.build_status_payload(
            ok=False, exit_code=1, name=name, status="stale",
            stale=stale, findings=findings, evidence_digest=evidence_digest,
        )))
    else:
        print(_show_block(_state.format_status_stale_text(name, stale, findings)))
    return 1


def _read_strict_optional_json(path: str) -> tuple[Any, str | None]:
    st = _lstat_if_exists(path)
    if st is None:
        return None, None
    if stat.S_ISLNK(st.st_mode):
        return None, f"state path is a symlink, refusing: {_show(path)}"
    res = _read_text(path)
    if res["error"] is not None or res["text"] is None:
        return None, f"unreadable: {_show(path)}"
    data, json_err = _state.parse_json_strict(res["text"])
    if json_err is not None:
        return None, f"corrupt JSON in {_show(path)}: {_show(json_err)}"
    return data, None


def _emit_status_ok(
    args: argparse.Namespace,
    paths: dict[str, str],
    receipt: dict[str, Any],
    m_gen: int,
    b_gen: int,
    amend_data: Any,
    tool_schemas: list | None = None,
) -> int:
    digest = str(receipt["evidence_digest"])
    if args.json:
        payload = _state.build_status_payload(
            ok=True, exit_code=0, name=args.name, status="ok",
            stale=[], findings=[], evidence_digest=digest,
        )
        if tool_schemas is not None:
            payload['tool_schemas'] = tool_schemas
        print(_dumps_json(payload))
        return 0
    b_meta_path = os.path.join(paths["job_dir"], "baseline", f"gen-{b_gen}", "meta.json")
    meta_data, meta_err = _read_strict_optional_json(b_meta_path)
    if meta_err is not None:
        return _status_err(args.name, meta_err, evidence_digest=digest)
    reb_reason = _state.extract_rebaseline_reason(b_gen, meta_data)
    amd_reason = _state.extract_amend_reason(m_gen, amend_data)
    print(_show_block(_state.format_status_ok_line(
        args.name, digest, m_gen, b_gen,
        rebaselined_reason=reb_reason, amended_reason=amd_reason,
    )))
    return 0


_DUMMY_HEX = "0" * 64
_DUMMY_CURRENT: dict[str, Any] = {
    "manifest_sha": _DUMMY_HEX,
    "manifest_gen": 1,
    "baseline_gen": 1,
    "baseline_shas": {},
    "file_hashes": {},
    "checker_digest": _DUMMY_HEX,
    "runtime": "cpython",
    "attestations_sha": _DUMMY_HEX,
    "amend_log_sha": None,
}


def _load_receipt_locked(name: str, receipt_path: str, as_json: bool) -> tuple[dict[str, Any] | None, int]:
    sym_err = _find_symlink_in_paths((receipt_path,))
    if sym_err is not None:
        return None, _status_err(name, sym_err)
    if _lstat_if_exists(receipt_path) is None:
        _, m_err = _check_begun_manifest_gen(os.path.join(os.path.dirname(receipt_path), "manifest"))
        if m_err is not None:
            return None, _status_err(name, m_err)
        finding = {"id": "receipt-missing", "severity": "error", "detail": "no receipt", "fix": _RERUN_FINISH_FIX}
        return None, _emit_status_stale(name, ["no receipt"], [finding], None, as_json)
    res = _read_text(receipt_path)
    if res["error"] is not None or res["text"] is None:
        return None, _status_err(name, f"cannot read receipt.json: {_show(res['error'])}")
    receipt, json_err = _state.parse_json_strict(res["text"])
    if json_err is not None:
        return None, _status_err(name, f"corrupt receipt JSON: {_show(json_err)}")
    probe = componentize_evidence.verify_receipt(receipt, current=_DUMMY_CURRENT)
    if probe["status"] == "error":
        return None, _status_err(name, f"corrupt receipt: {_show(probe['error'])}")
    if receipt.get("name") != name:
        return None, _status_err(name, f"receipt name {receipt.get('name')!r} does not match {name!r}")
    return receipt, 0


def _is_declared_file_unreadable_err(
    err_msg: str, manifest: dict[str, Any], tools_root: str,
) -> bool:
    index_path = _index_path(tools_root)
    declared = set(_state.collect_hash_paths(manifest["files"], index_path, manifest.get("inputs")))
    # Fingerprints the checker code that actually runs (_TOOLS_ROOT), not --tools-root (which only selects COMPONENTS.md).
    checker_paths = {full for _, full in _get_checker_files(_TOOLS_ROOT)}
    for dpath in declared - checker_paths:
        if err_msg == f"unreadable: {_show(dpath)}":
            return True
    return False


def _verify_status_locked(args: argparse.Namespace, paths: dict[str, str]) -> int:
    as_json = bool(args.json)
    receipt, r_code = _load_receipt_locked(args.name, paths["receipt"], as_json)
    if receipt is None:
        return r_code
    digest = str(receipt["evidence_digest"])
    sym_err = _find_symlink_in_paths((paths["job_dir"], paths["manifest_dir"]))
    if sym_err is not None:
        return _status_err(args.name, sym_err, evidence_digest=digest)
    m_gen, b_gen, gen_err = _discover_current_gens(paths["job_dir"])
    if gen_err is not None or m_gen is None or b_gen is None:
        return _status_err(args.name, gen_err or "corrupt state", evidence_digest=digest)
    m_path = os.path.join(paths["manifest_dir"], f"gen-{m_gen}.json")
    m_res = _read_text(m_path)
    if m_res["error"] is not None or m_res["text"] is None:
        return _status_err(args.name, f"unreadable: {_show(m_path)}", evidence_digest=digest)
    manifest, json_err = _state.parse_json_strict(m_res["text"])
    if json_err is not None:
        return _status_err(args.name, f"corrupt manifest JSON: {_show(json_err)}", evidence_digest=digest)
    val_res = componentize_evidence.validate_manifest(manifest)
    if val_res["status"] in ("error", "fail") or not val_res.get("ok") or val_res.get("name") != args.name:
        return _status_err(args.name, "stored manifest invalid", evidence_digest=digest)
    amend_log_path = os.path.join(paths["manifest_dir"], "amend-log.json")
    amend_data, amend_err = _read_strict_optional_json(amend_log_path)
    if amend_err is not None or (amend_data is not None and not isinstance(amend_data, list)):
        return _status_err(args.name, amend_err or "corrupt amend-log.json: expected list", evidence_digest=digest)
    current, cur_err = _compute_current_evidence(paths, manifest, m_gen, b_gen, args.tools_root)
    if cur_err is not None or current is None:
        err_str = cur_err or "unreadable"
        if _is_declared_file_unreadable_err(err_str, manifest, args.tools_root):
            return _emit_status_stale(args.name, [err_str], [{"id": "receipt-stale", "severity": "error", "detail": err_str, "fix": "ensure all evidence files are readable"}], digest, as_json)
        return _status_err(args.name, err_str, evidence_digest=digest)
    return _evaluate_receipt_vs_current(args, paths, receipt, manifest, current, m_gen, b_gen, amend_data)


def _evaluate_receipt_vs_current(
    args: argparse.Namespace,
    paths: dict[str, str],
    receipt: dict[str, Any],
    manifest: dict[str, Any],
    current: dict[str, Any],
    m_gen: int,
    b_gen: int,
    amend_data: Any = None,
) -> int:
    digest = str(receipt["evidence_digest"])
    vres = componentize_evidence.verify_receipt(receipt, current=current)
    if vres["status"] == "error":
        return _status_err(args.name, f"receipt error: {_show(vres['error'])}", evidence_digest=digest)
    index_path = _index_path(args.tools_root)
    expected_keys = set(_state.collect_hash_paths(manifest["files"], index_path, manifest.get("inputs")))
    stale_list = list(vres["stale"])
    findings_list = list(vres["findings"])
    if not expected_keys or set(receipt["file_hashes"].keys()) != expected_keys:
        if "coverage" not in stale_list:
            stale_list.append("coverage")
        findings_list.append({
            "id": "receipt-coverage",
            "severity": "error",
            "detail": "coverage: receipt file_hashes keys do not match declared manifest files",
            "fix": _RERUN_FINISH_FIX,
        })
    for dup in _find_duplicate_inode_findings(manifest):
        stale_list.append(str(dup["detail"]))
        findings_list.append(dup)
    last_check, last_error = _read_verified_last_check(paths['last_check'])
    if last_error or last_check is None or last_check.get('check_result_sha') != receipt['check_result_sha']:
        stale_list.append('last-check')
        findings_list.append({'id':'receipt-stale', 'severity':'error', 'detail':last_error or 'last-check differs from receipt', 'fix':_RERUN_FINISH_FIX})
    inventory_sha, inventory_findings = _plugins.inventory(manifest)
    if inventory_findings or (last_check is not None and 'plugin_inventory_sha' in last_check and last_check['plugin_inventory_sha'] != inventory_sha):
        stale_list.append('plugin inventory')
        findings_list.extend(inventory_findings)
    if vres["status"] != "pass" or stale_list or findings_list:
        return _emit_status_stale(args.name, stale_list, findings_list, digest, bool(args.json))
    code = _emit_status_ok(args, paths, receipt, m_gen, b_gen, amend_data, last_check.get("tool_schemas"))
    if code == 0 and not args.json:
        for report in last_check.get('tool_schemas', []):
            print(_show_block(_plugins.format_report(report)))
    return code


def _cmd_status(args: argparse.Namespace) -> int:
    paths = _state.job_paths(args.state_root, args.name, gen=1)
    try:
        sym_err = _find_symlink_in_paths((args.state_root, paths["job_dir"]))
        if sym_err is not None:
            return _status_err(args.name, sym_err)
        job_st = _lstat_if_exists(paths["job_dir"])
        if job_st is None or not stat.S_ISDIR(job_st.st_mode):
            return _status_err(args.name, _NOT_BEGUN_MSG)
    except OSError as exc:
        return _status_err(args.name, f"cannot access state: {_show(exc)}")
    lock_fd, lock_err = _acquire_job_lock(paths["lock"], busy_prefix="busy: ")
    if lock_err is not None:
        return _status_err(args.name, lock_err)
    try:
        try:
            return _verify_status_locked(args, paths)
        except OSError as exc:
            return _status_err(args.name, f"filesystem error: {type(exc).__name__}: {_show(exc)}")
    finally:
        _io.release_lock(lock_fd)


def _positive_finite_timeout(raw: str) -> float:
    try:
        val = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid timeout {raw!r}: must be a number") from exc
    if not math.isfinite(val) or val <= 0.0:
        raise argparse.ArgumentTypeError(f"invalid timeout {raw!r}: must be finite and > 0")
    return val


class _CLIArgumentParser(argparse.ArgumentParser):
    def parse_known_args(self, *args: Any, **kwargs: Any) -> Any:
        # Remember actual subparser selection, not a raw token that could be
        # an option value. Root-level unknown-argument errors occur later.
        if self.prog == "componentize survey":
            _OUTPUT["survey"] = True
        return super().parse_known_args(*args, **kwargs)

    def error(self, message: str) -> NoReturn:
        if _OUTPUT["json"]:
            if _OUTPUT.get("survey", False):
                result = componentize_survey.survey_sources({})
                result.update(ok=False, status="error", error=message, path=None, exit=2)
                print(_dumps_json(result))
            else:
                _err(message, code=2)
            raise SystemExit(2)
        super().error(message)


def _add_global_flags(p: argparse.ArgumentParser, *, default_tools: str, suppress: bool) -> None:
    sr_def: Any = argparse.SUPPRESS if suppress else None
    tr_def: Any = argparse.SUPPRESS if suppress else default_tools
    js_kw: dict[str, Any] = {"default": argparse.SUPPRESS} if suppress else {}
    p.add_argument("--state-root", default=sr_def, help="State root directory (default: <tools-root>/.componentize)")
    p.add_argument("--tools-root", default=tr_def, help="Component library root directory")
    p.add_argument("--json", action="store_true", **js_kw, help="Emit machine-readable JSON")


def _cmd_survey(args: argparse.Namespace) -> int:
    path = os.path.abspath(args.path)
    sources: dict[str, str] = {}
    error = None
    try:
        st = os.lstat(path)
        if stat.S_ISDIR(st.st_mode):
            paths = []
            def onerror(exc: OSError) -> NoReturn:
                raise exc
            for directory, dirs, names in os.walk(path, followlinks=False, onerror=onerror):
                dirs[:] = sorted(d for d in dirs if d not in {'.git', '.componentize', '.venv', 'venv', '__pycache__', 'node_modules'}
                                 and not os.path.islink(os.path.join(directory, d)))
                paths.extend(os.path.join(directory, name) for name in sorted(names)
                             if name.endswith('.py') and not os.path.islink(os.path.join(directory, name)))
        elif stat.S_ISREG(st.st_mode) and path.endswith('.py'):
            paths = [path]
        else:
            raise ValueError('survey target must be a regular Python file or directory (no symlinks)')
        for candidate in paths:
            read = _read_text(candidate)
            if read['error'] is not None:
                raise ValueError(f"cannot read {_show(candidate)}: {_show(read['error'])}")
            sources[candidate] = read['text']
    except (OSError, ValueError) as exc:
        error = str(exc)
    result = componentize_survey.survey_sources(sources)
    result['path'] = path
    if error:
        result.update(ok=False, status='error', error=error)
    rc = 0 if result['ok'] else 2
    result['exit'] = rc
    if args.json:
        print(_dumps_json(result))
    else:
        print(f"survey {_show(path)}: {len(result['files'])} scripts; {len(result['proposed_splits'])} proposed splits (review required)")
        for item in result['files']:
            print(f"  {item['path']}: {item['line_count']} lines; functions: {', '.join(item['functions']) or '(none)'}")
        for split in result['proposed_splits']:
            print(f"  proposed {split['component']}: {', '.join(split['component_functions'])}")
        if result['error']:
            print(result['error'], file=sys.stderr)
    return rc


def _build_parser() -> argparse.ArgumentParser:
    default_tools = _TOOLS_ROOT
    parser = _CLIArgumentParser(prog="componentize")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _add_global_flags(parser, default_tools=default_tools, suppress=False)

    global_parent = _CLIArgumentParser(add_help=False)
    _add_global_flags(global_parent, default_tools=default_tools, suppress=True)

    sub = parser.add_subparsers(dest="subcommand", required=True)
    p_survey = sub.add_parser("survey", parents=[global_parent], help="Read-only Python inventory and proposed splits; writes nothing")
    p_survey.add_argument("path", help="Python script or directory to inventory without execution")
    p_survey.set_defaults(handler=_cmd_survey)

    containment_epilog = (
        "Subprocess containment covers the command's process group (start_new_session) on timeout, "
        "overflow, and normal return; processes that call setsid() or double-fork to detach escape "
        "and are out of scope (golden/tests commands must be foreground-only; intentional daemons "
        "need a wrapper that waits and cleans up)."
    )
    tests_epilog = containment_epilog + (
        " The tests run with a fresh temporary HOME created under TMPDIR (else /tmp) and removed "
        "afterwards; set TMPDIR to place it on another disk."
    )

    p_begin = sub.add_parser(
        "begin",
        parents=[global_parent],
        help="Initialize componentize job and capture baseline",
        epilog=containment_epilog,
    )
    p_begin.add_argument("name", help="Componentize job name")
    p_begin.add_argument("--manifest", required=True, help="Path to spec.json manifest")
    p_begin.add_argument("--amend", action="store_true", help="Amend manifest into a new generation")
    p_begin.add_argument("--reason", default=None, help=f"Reason for manifest amendment (>= {_state.MIN_NOTE_CHARS} chars)")
    p_begin.add_argument("--timeout", type=_positive_finite_timeout, default=600.0, help="Golden run timeout in seconds")
    p_begin.set_defaults(handler=_cmd_begin)

    p_attest = sub.add_parser(
        "attest",
        parents=[global_parent],
        help="Record phase attestation note (a successful attest deletes any receipt; nonblocking lock, callers retry on exit 2 if locked)",
        description=(
            "Record phase attestation note; a successful attest deletes any receipt. "
            "Acquires a nonblocking job lock (exits 2 if locked; callers retry)."
        ),
    )
    p_attest.add_argument("name", help="Componentize job name")
    p_attest.add_argument("phase", choices=list(_state.ATTEST_PHASES), help="Attestation phase")
    p_attest.add_argument("text", help=f"Attestation text (>= {_state.MIN_NOTE_CHARS} chars after trimming)")
    p_attest.set_defaults(handler=_cmd_attest)

    p_check = sub.add_parser(
        "check",
        parents=[global_parent],
        help="Run architecture gate, golden comparison, and tests (if locked, exits 2 with receipt untouched; the running holder will invalidate it)",
        description=(
            "Run architecture gate, golden comparison, and tests. "
            "Acquires a nonblocking job lock and immediately invalidates receipt.json; "
            "if locked by another process, exits 2 with receipt.json untouched (the running holder will invalidate it)."
        ),
        epilog=tests_epilog,
    )
    p_check.add_argument("name", help="Componentize job name")
    p_check.add_argument("--timeout", type=_positive_finite_timeout, default=600.0, help="Subprocess timeout in seconds")
    p_check.set_defaults(handler=_cmd_check)

    p_finish = sub.add_parser(
        "finish",
        parents=[global_parent],
        help="Run check, verify attestations, and write bound receipt.json",
        epilog=tests_epilog,
    )
    p_finish.add_argument("name", help="Componentize job name")
    p_finish.add_argument("--timeout", type=_positive_finite_timeout, default=600.0, help="Subprocess timeout in seconds")
    p_finish.set_defaults(handler=_cmd_finish)

    p_status = sub.add_parser(
        "status",
        parents=[global_parent],
        help="Re-verify receipt.json against current state",
    )
    p_status.add_argument("name", help="Componentize job name")
    p_status.set_defaults(handler=_cmd_status)

    p_rebaseline = sub.add_parser(
        "rebaseline",
        parents=[global_parent],
        help="Capture a new baseline generation with a reason",
        epilog=containment_epilog,
    )
    p_rebaseline.add_argument("name", help="Componentize job name")
    p_rebaseline.add_argument("--reason", required=True, help=f"Reason for rebaselining (>= {_state.MIN_NOTE_CHARS} chars)")
    p_rebaseline.add_argument("--timeout", type=_positive_finite_timeout, default=600.0, help="Golden run timeout in seconds")
    p_rebaseline.set_defaults(handler=_cmd_rebaseline)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for componentize."""
    eff_argv = sys.argv[1:] if argv is None else list(argv)
    _OUTPUT["json"] = "--json" in eff_argv
    _OUTPUT["survey"] = False
    parser = _build_parser()
    try:
        args = parser.parse_args(eff_argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    _OUTPUT["json"] = bool(args.json)
    args.tools_root = os.path.abspath(args.tools_root)
    if args.state_root is None:
        args.state_root = os.path.join(args.tools_root, ".componentize")
    # The only abspath/normpath of the user's --state-root; _resolve_state_root below is its
    # only realpath.
    args.state_root = _lexical_state_root(args.state_root)
    if args.handler is _cmd_survey:
        return int(args.handler(args))
    # Every other subcommand writes state (status writes its lock): refuse unsafe roots and
    # job names before anything is created, then work on the resolved path, reached only
    # through the pinned chain of folder fds (_state_anchor).
    if not isinstance(args.name, str) or not componentize_evidence._NAME_RE.fullmatch(args.name):
        return _err(f"refusing job name {_show(args.name)}: it must match {componentize_evidence._NAME_RE.pattern}",
                    code=2)
    # Resolve once, decide on that string, pin that same string: a symlinked ancestor swapped
    # after this line is not on the pinned path, and a real ancestor replaced by a symlink is
    # refused by the no-follow walk.
    lexical = args.state_root
    try:
        resolved = _resolve_state_root(lexical)
        refusal = _state_root_refusal(resolved, lexical)
    except OSError as exc:
        # realpath raises (Python 3.11; EINVAL on macOS) when a link on the path is swapped
        # while it is read: that path has no one resolution to decide on, so it is refused.
        return _err(f"refusing state root {_show(lexical)}: its path cannot be resolved "
                    f"({_show(exc.strerror or type(exc).__name__)}); a folder on it changed while it "
                    "was read, try again or pick a project folder whose path does not change", code=2)
    if refusal is not None:
        return _err(refusal, code=2)
    args.state_root = resolved
    # One rule for every path written into or compared with a receipt: the index path is built
    # on the resolved --tools-root, like the state root, so the same checkout reached through a
    # symlinked folder, `..` or a trailing slash finds the same receipt keys (declared files keep
    # the form stored in the manifest at begin, which does not depend on --tools-root).
    try:
        args.tools_root = os.path.realpath(args.tools_root)
    except OSError as exc:
        return _err(f"refusing --tools-root {_show(args.tools_root)}: its path cannot be resolved "
                    f"({_show(exc.strerror or type(exc).__name__)}); try again", code=2)
    try:
        with _state_anchor(args.state_root):
            return int(args.handler(args))
    except _StateRefused as exc:
        return _err(str(exc), code=2)


if __name__ == "__main__":
    raise SystemExit(main())
