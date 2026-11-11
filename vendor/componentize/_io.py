# vendored copy of the author's componentize/_io.py (MIT licence, same as this plugin); do not edit
"""POSIX I/O helper module for the componentize CLI runner (not a shared component).

Subprocess capture uses storage-free, memory-bounded pipe draining: it retains at most
max_output_bytes bytes per stream (reads stop and the process group is killed as soon as
either stream exceeds the cap). Memory use is O(max_output_bytes): roughly 2-3x the cap
per stream at peak, including buffer over-allocation and decoding. No output is written
to disk. Termination covers the command's process group (start_new_session) on timeout,
overflow, and normal return; processes that call setsid() or double-fork to detach
escape and are out of scope (golden/tests commands must be foreground-only; intentional
daemons need a wrapper that waits and cleans up).
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import secrets
import selectors
import signal
import stat
import subprocess
import time
from typing import Any

_CHUNK_BYTES = 1024 * 1024
_DEFAULT_SHA256_MAX_BYTES = 64 * 1024 * 1024
_DEFAULT_TEXT_MAX_BYTES = 16 * 1024 * 1024
_HOLDER_MAX_BYTES = 128
_DEFAULT_OUTPUT_MAX_BYTES = 64 * 1024 * 1024
_PIPE_READ_BYTES = 65536
_POLL_INTERVAL_SEC = 0.05
_EXIT_GRACE_SEC = 5.0


def _fmt_os_err(exc: OSError) -> str:
    return f"{type(exc).__name__}: {exc}"


def close_quietly(fd: int | None) -> None:
    """Close fd if not None, ignoring OSError (best-effort cleanup)."""
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        n = os.write(fd, view[offset:])
        if n <= 0:
            raise OSError(errno.EIO, "short write returned 0 bytes")
        offset += n


def _kill_session(proc: subprocess.Popen[bytes]) -> None:
    """SIGKILL the command's process group; a group that has already exited is fine.

    Linux answers ESRCH once the group is empty. macOS answers EPERM instead while
    every process left in the group is exiting or has exited and is not yet
    reaped, including the moment after an earlier SIGKILL when the leader is dying
    but poll() still reports it running. A leader this process started refuses
    the signal only while it is exiting, or when it changed its user (a
    set-user-ID program): wait up to _EXIT_GRACE_SEC for it to finish (this
    reaps it). A leader still running after that refused for real, and the
    PermissionError is raised.
    """
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError as exc:
        try:
            proc.wait(timeout=_EXIT_GRACE_SEC)
        except subprocess.TimeoutExpired:
            raise exc from None


def _close_pipes(proc: subprocess.Popen[bytes]) -> None:
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


def _drain_pipes_bounded(
    proc: subprocess.Popen[bytes],
    deadline: float,
    max_output_bytes: int,
) -> tuple[bytearray, bytearray, bool, bool]:
    bufs: dict[int, bytearray] = {}
    with selectors.DefaultSelector() as sel:
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                fd = stream.fileno()
                bufs[fd] = bytearray()
                sel.register(fd, selectors.EVENT_READ)
        out_buf = bufs[proc.stdout.fileno()] if proc.stdout is not None else bytearray()
        err_buf = bufs[proc.stderr.fileno()] if proc.stderr is not None else bytearray()
        while sel.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _kill_session(proc)
                return out_buf, err_buf, True, False
            events = sel.select(timeout=min(_POLL_INTERVAL_SEC, remaining))
            for key, _ in events:
                fd = int(key.fd)
                chunk = os.read(fd, _PIPE_READ_BYTES)
                if not chunk:
                    sel.unregister(fd)
                    continue
                bufs[fd].extend(chunk)
                if len(bufs[fd]) > max_output_bytes:
                    _kill_session(proc)
                    del out_buf[max_output_bytes:]
                    del err_buf[max_output_bytes:]
                    return out_buf, err_buf, False, True
            if proc.poll() is not None:
                _kill_session(proc)
    return out_buf, err_buf, False, False


def _finish_proc(
    proc: subprocess.Popen[bytes],
    deadline: float,
    timed_out: bool,
    truncated: bool,
) -> bool:
    """Wait for a normal exit until deadline; True if it timed out. Kill/close/reap happen in run_argv's finally."""
    if timed_out or truncated:
        return timed_out
    try:
        proc.wait(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        return True
    return False


def run_argv(
    argv: list[str], *, cwd: str, env: dict[str, str],
    timeout: float, max_output_bytes: int = _DEFAULT_OUTPUT_MAX_BYTES,
) -> dict[str, Any]:
    """Run argv in a new session with memory-bounded pipe draining (limits: see module docstring)."""
    proc: subprocess.Popen[bytes] | None = None
    try:
        deadline = time.monotonic() + max(0.0, float(timeout))
        proc = subprocess.Popen(
            argv, cwd=cwd, env=dict(env),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, close_fds=True,
        )
        out_buf, err_buf, timed_out, truncated = _drain_pipes_bounded(proc, deadline, max_output_bytes)
        timed_out = _finish_proc(proc, deadline, timed_out, truncated)
        err_msg = f"timeout after {timeout}s" if timed_out else ("output exceeds limit" if truncated else None)
        return {
            "rc": None if (timed_out or truncated) else proc.returncode,
            "stdout": out_buf.decode("utf-8", errors="replace"),
            "stderr": err_buf.decode("utf-8", errors="replace"),
            "timed_out": timed_out, "error": err_msg, "truncated": truncated,
        }
    except OSError as exc:
        return {
            "rc": None, "stdout": "", "stderr": "",
            "timed_out": False, "error": _fmt_os_err(exc), "truncated": False,
        }
    finally:
        if proc is not None:
            _kill_session(proc)
            _close_pipes(proc)
            proc.wait()


def _open_regular_reader(path: str, max_bytes: int) -> tuple[int | None, int | None, str | None]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    fd: int | None = None
    try:
        fd = os.open(path, flags)
        st = os.fstat(fd)
    except OSError as exc:
        close_quietly(fd)
        if exc.errno in (errno.ELOOP, errno.EISDIR, errno.ENXIO):
            return None, None, "not-regular"
        return None, None, _fmt_os_err(exc)
    if not stat.S_ISREG(st.st_mode):
        close_quietly(fd)
        return None, None, "not-regular"
    if st.st_size > max_bytes:
        close_quietly(fd)
        return None, st.st_size, f"too-large size={st.st_size}"
    return fd, st.st_size, None


def _read_bounded(fd: int, max_bytes: int, on_chunk: Any) -> int:
    """Pass each chunk to on_chunk until EOF; returns total bytes read (> max_bytes: cap exceeded)."""
    total = 0
    while total <= max_bytes:
        chunk = os.read(fd, min(_CHUNK_BYTES, max_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total <= max_bytes:
            on_chunk(chunk)
    return total


def _sha256_err(size: int | None, err: str | None) -> dict[str, Any]:
    return {"sha256": None, "size": size, "ino": None, "mtime_ns": None, "ctime_ns": None, "error": err}


def sha256_file(path: str, *, max_bytes: int = _DEFAULT_SHA256_MAX_BYTES) -> dict[str, Any]:
    """Stream-compute SHA-256 and stat fingerprint of a regular file up to max_bytes via validated FD."""
    fd, size, err = _open_regular_reader(path, max_bytes)
    if fd is None:
        return _sha256_err(size, err)
    digest = hashlib.sha256()
    try:
        total = _read_bounded(fd, max_bytes, digest.update)
        if total > max_bytes:
            return _sha256_err(total, f"too-large size={total}")
        st = os.fstat(fd)
        return {
            "sha256": digest.hexdigest(), "size": total,
            "ino": st.st_ino, "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns,
            "error": None,
        }
    except OSError as exc:
        return _sha256_err(None, _fmt_os_err(exc))
    finally:
        close_quietly(fd)


def read_text(path: str, *, max_bytes: int = _DEFAULT_TEXT_MAX_BYTES) -> dict[str, Any]:
    """Read a regular UTF-8 text file up to max_bytes via validated FD."""
    fd, _, err = _open_regular_reader(path, max_bytes)
    if fd is None:
        return {"text": None, "error": err}
    chunks: list[bytes] = []
    try:
        total = _read_bounded(fd, max_bytes, chunks.append)
        if total > max_bytes:
            return {"text": None, "error": f"too-large size={total}"}
        return {"text": b"".join(chunks).decode("utf-8", errors="strict"), "error": None}
    except UnicodeDecodeError:
        return {"text": None, "error": "not-utf8"}
    except OSError as exc:
        return {"text": None, "error": _fmt_os_err(exc)}
    finally:
        close_quietly(fd)


def _fsync_dir(dir_path: str) -> None:
    dir_fd: int | None = None
    try:
        dir_fd = os.open(dir_path, os.O_RDONLY | os.O_CLOEXEC)
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        close_quietly(dir_fd)


def write_atomic(path: str, data: str | bytes) -> dict[str, Any]:
    """Atomically write data to path via a sibling temp file, full write, and os.replace."""
    payload = data.encode("utf-8") if isinstance(data, str) else data
    parent = os.path.dirname(path) or "."
    base = os.path.basename(path)
    tmp_path = os.path.join(parent, f".{base}.tmp-{os.getpid()}-{secrets.token_hex(6)}")
    fd: int | None = None
    created = False
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o644)
        created = True
        _write_all(fd, payload)
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(tmp_path, path)
    except OSError as exc:
        close_quietly(fd)
        if created:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return {"ok": False, "error": _fmt_os_err(exc)}
    _fsync_dir(parent)
    return {"ok": True, "error": None}


def _read_holder_fd(fd: int) -> str:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, _HOLDER_MAX_BYTES)
        return raw.decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _unlock_and_close_quietly(fd: int | None) -> None:
    if fd is not None:
        release_lock(fd)


def acquire_lock(lock_path: str, *, pid: int) -> dict[str, Any]:
    """Acquire an exclusive non-blocking flock on lock_path and record diagnostic pid."""
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
    fd: int | None = None
    locked = False
    try:
        fd = os.open(lock_path, flags, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        _write_all(fd, f"{pid}\n".encode("ascii"))
        os.fsync(fd)
        return {"ok": True, "fd": fd, "error": None, "holder": None}
    except BlockingIOError:
        holder = _read_holder_fd(fd) if fd is not None else ""
        close_quietly(fd)
        return {"ok": False, "fd": None, "error": "locked", "holder": holder}
    except OSError as exc:
        if locked:
            _unlock_and_close_quietly(fd)
        else:
            close_quietly(fd)
        return {"ok": False, "fd": None, "error": _fmt_os_err(exc), "holder": None}


def release_lock(fd: int) -> dict[str, Any]:
    """Unlock and close an open lock file descriptor without unlinking the lock file."""
    if fd < 0:
        return {"ok": False, "error": f"OSError: invalid file descriptor {fd}"}
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError as exc:
        close_quietly(fd)
        return {"ok": False, "error": _fmt_os_err(exc)}
    try:
        os.close(fd)
    except OSError as exc:
        return {"ok": False, "error": _fmt_os_err(exc)}
    return {"ok": True, "error": None}
