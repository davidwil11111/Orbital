# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""One process runner that cannot hang (spec 134 A1).

``subprocess.run(capture_output=True, timeout=…)`` hung the Windows app: on
timeout CPython kills only the direct child and then, on Windows only, calls
``communicate()`` with no timeout, which waits until every grandchild holding
the pipes has exited — as long as the script runs. A chatty script's output
was also held whole in memory and split into lines in one go.

This runner fixes both by design:

- Output goes to a **file**, never a pipe, so nothing waits on EOF.
- The command starts in **its own process group** (POSIX: a new session;
  Windows: ``CREATE_NEW_PROCESS_GROUP`` plus a Job Object).
- The wait has a timeout. **On timeout only**, the whole tree is killed
  (POSIX ``killpg``; Windows ``TerminateJobObject``), with a psutil sweep of
  the descendants as the fallback. A command that exits normally leaves
  whatever it started alone, on both platforms (spec 134 DECISIONS
  2026-10-26), which is why the foreground Job Object never sets
  ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``.
- The read-back is bounded: a head and a tail read by seeking, never the
  whole file.

Two layers, so the background jobs of spec 134 Part B can reuse the pieces:

- ``run_foreground(argv, …)`` — spawn, wait, kill the tree on timeout,
  collect a bounded result. The shell tool's foreground path.
- ``spawn(argv, …)`` → ``TreeProcess`` (``poll`` / ``wait`` / ``kill_tree`` /
  ``close``), ``read_bounded(path)``, ``collect_result(path, …)``,
  ``kill_process_tree(pid, …)`` and ``WindowsJob`` — the parts on their own.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

from agent_os.utils.subprocess_flags import win_no_window_flags

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

#: Win32 ``CREATE_NEW_PROCESS_GROUP``; a literal because the ``subprocess``
#: attribute does not exist off Windows.
CREATE_NEW_PROCESS_GROUP = 0x00000200

# Read-back limits — the same shape ``ShellTool._truncate_output`` has always
# produced (200 lines, else the first 20 and the last 50), now taken from at
# most HEAD_BYTES + TAIL_BYTES of the file.
MAX_LINES = 200
HEAD_LINES = 20
TAIL_LINES = 50
HEAD_BYTES = 64 * 1024
TAIL_BYTES = 64 * 1024
_COUNT_CHUNK = 1024 * 1024

#: How long a timed-out tree gets between SIGTERM and SIGKILL (POSIX).
KILL_GRACE_S = 0.5


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class BoundedOutput:
    """What was read back from a log file.

    ``text`` is the whole output when ``truncated`` is False. When it is True,
    ``head`` and ``tail`` hold the first ``HEAD_LINES`` and last
    ``TAIL_LINES`` lines (each also capped in bytes) and ``text`` is empty;
    ``render`` joins them with the notice the agent has always seen.
    """
    text: str
    total_lines: int
    total_bytes: int
    truncated: bool
    head: str = ""
    tail: str = ""

    def render(self, saved_path: str | None) -> str:
        if not self.truncated:
            return self.text
        where = f", saved to {saved_path}" if saved_path else ""
        return (
            f"{self.head}\n"
            f"... [truncated {self.total_lines} lines{where}] ...\n"
            f"{self.tail}"
        )


@dataclass
class RunResult:
    """A finished foreground run.

    ``exit_code`` is None when the run timed out (the tree was killed).
    ``log_path`` is where the full output stays on disk, or None when it was
    short enough to be returned whole and the file was removed.
    """
    exit_code: int | None
    timed_out: bool
    output: BoundedOutput
    log_path: str | None
    duration_s: float = 0.0


# ---------------------------------------------------------------------------
# Bounded read-back
# ---------------------------------------------------------------------------


def _decode(data: bytes) -> str:
    # ``text=True`` used universal newlines; keep what the agent saw.
    return (data.decode("utf-8", errors="replace")
            .replace("\r\n", "\n").replace("\r", "\n"))


def _count_lines(f, size: int) -> int:
    """Line count the way ``str.split("\\n")`` counts, without loading the file."""
    f.seek(0)
    newlines = 0
    while True:
        chunk = f.read(_COUNT_CHUNK)
        if not chunk:
            break
        newlines += chunk.count(b"\n")
    return newlines + 1 if size else 0


def read_bounded(
    path: str,
    *,
    max_lines: int = MAX_LINES,
    head_lines: int = HEAD_LINES,
    tail_lines: int = TAIL_LINES,
    head_bytes: int = HEAD_BYTES,
    tail_bytes: int = TAIL_BYTES,
) -> BoundedOutput:
    """Read *path* back without ever holding more than head + tail bytes.

    A missing file reads as empty output.
    """
    try:
        size = os.path.getsize(path)
        f = open(path, "rb")
    except OSError:
        return BoundedOutput(text="", total_lines=0, total_bytes=0, truncated=False)
    with f:
        if size <= head_bytes + tail_bytes:
            text = _decode(f.read())
            lines = text.split("\n")
            if len(lines) <= max_lines:
                return BoundedOutput(text=text, total_lines=len(lines),
                                     total_bytes=size, truncated=False)
            return BoundedOutput(
                text="", total_lines=len(lines), total_bytes=size, truncated=True,
                head="\n".join(lines[:head_lines]),
                tail="\n".join(lines[-tail_lines:]),
            )
        head_chunk = f.read(head_bytes)
        f.seek(max(size - tail_bytes, 0))
        tail_chunk = f.read(tail_bytes)
        total_lines = _count_lines(f, size)
    head = _decode(head_chunk).split("\n")[:head_lines]
    tail = _decode(tail_chunk).split("\n")
    if len(tail) > 1:
        tail = tail[1:]  # the first piece starts mid-line
    return BoundedOutput(
        text="", total_lines=total_lines, total_bytes=size, truncated=True,
        head="\n".join(head), tail="\n".join(tail[-tail_lines:]),
    )


def _remove_quietly(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except OSError:
        # Windows: a survivor still holds the file. It stays behind.
        return False


def keep_log(path: str, keep_dir: str | None) -> str:
    """Move a truncated log into *keep_dir*; on any failure leave it put."""
    if not keep_dir:
        return path
    try:
        os.makedirs(keep_dir, exist_ok=True)
        target = os.path.join(keep_dir, os.path.basename(path))
        try:
            os.replace(path, target)
        except OSError:
            shutil.move(path, target)  # another volume (temp vs workspace)
        return target
    except OSError:
        return path


def collect_result(
    log_path: str,
    *,
    exit_code: int | None,
    timed_out: bool,
    keep_dir: str | None = None,
    duration_s: float = 0.0,
) -> RunResult:
    """Bounded read-back of a finished run's log, then settle the file.

    A truncated log moves to *keep_dir* (when given) so the agent can
    ``read`` the full output; a short one is deleted.
    """
    output = read_bounded(log_path)
    if output.truncated:
        kept: str | None = keep_log(log_path, keep_dir)
    else:
        _remove_quietly(log_path)
        kept = None
    return RunResult(exit_code=None if timed_out else exit_code,
                     timed_out=timed_out, output=output, log_path=kept,
                     duration_s=duration_s)


# ---------------------------------------------------------------------------
# Windows Job Object
# ---------------------------------------------------------------------------

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9


def _job_structs():
    import ctypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    return JOBOBJECT_EXTENDED_LIMIT_INFORMATION


def _extended_limit_flags(info_ptr) -> int:
    """``LimitFlags`` of the struct behind a ``byref`` (for tests)."""
    return info_ptr._obj.BasicLimitInformation.LimitFlags


def _real_kernel32():
    import ctypes
    import ctypes.wintypes as wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k.SetInformationJobObject.restype = wintypes.BOOL
    k.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k.AssignProcessToJobObject.restype = wintypes.BOOL
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.TerminateJobObject.restype = wintypes.BOOL
    k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.CloseHandle.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    return k


class WindowsJob:
    """A Win32 Job Object holding one command's process tree.

    Foreground runs create it with ``kill_on_close=False``: it exists only so
    a timeout can ``TerminateJobObject`` the whole tree, and closing it after
    a normal exit leaves survivors running. Part B's background jobs pass
    ``kill_on_close=True`` and keep the handle for the app's lifetime, so the
    jobs die with Orbital even when it is killed.

    Every failure is reported, never raised: an app that already runs inside
    a parent's job may be refused (spec 134 §8), and the caller then falls
    back to the psutil tree kill.
    """

    def __init__(self, handle, kernel32) -> None:
        self._handle = handle
        self._k = kernel32
        self.assigned = False

    @classmethod
    def create(cls, *, kill_on_close: bool = False,
               kernel32=None) -> "WindowsJob | None":
        try:
            k = kernel32 if kernel32 is not None else _real_kernel32()
            handle = k.CreateJobObjectW(None, None)
            if not handle:
                return None
            if kill_on_close:
                import ctypes

                info = _job_structs()()
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                if not k.SetInformationJobObject(
                        handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                        ctypes.byref(info), ctypes.sizeof(info)):
                    logger.warning("SetInformationJobObject(kill-on-close) failed")
            return cls(handle, k)
        except Exception:
            logger.debug("CreateJobObjectW failed", exc_info=True)
            return None

    def assign(self, process_handle) -> bool:
        try:
            self.assigned = bool(self._k.AssignProcessToJobObject(
                self._handle, process_handle))
        except Exception:
            self.assigned = False
        if not self.assigned:
            logger.info("AssignProcessToJobObject failed; timeouts fall back "
                        "to the psutil tree kill")
        return self.assigned

    def terminate(self, exit_code: int = 1) -> bool:
        if not self._handle:
            return False
        try:
            return bool(self._k.TerminateJobObject(self._handle, exit_code))
        except Exception:
            return False

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle:
            try:
                self._k.CloseHandle(handle)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Tree kill
# ---------------------------------------------------------------------------


def _descendants(pid: int) -> list:
    try:
        import psutil

        return psutil.Process(pid).children(recursive=True)
    except Exception:
        return []


def _kill_quietly(proc) -> None:
    try:
        proc.kill()
    except Exception:
        pass


def _killpg_quietly(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def kill_process_tree(
    pid: int,
    *,
    popen: subprocess.Popen | None = None,
    job: WindowsJob | None = None,
    pgid: int | None = None,
    grace: float = KILL_GRACE_S,
) -> None:
    """Kill *pid* and everything it started. Never raises.

    The descendants are listed first, while the parent links still exist.
    Windows: ``TerminateJobObject`` (when the process was assigned), then the
    leader, then the listed descendants (they cover a failed assignment and
    anything started before it). POSIX: SIGTERM to the process group, a short
    grace, then SIGKILL to the group and to any listed descendant that left
    it with ``setsid``.
    """
    descendants = _descendants(pid)
    if _IS_WINDOWS:
        if job is not None and job.assigned:
            job.terminate()
        if popen is not None:
            _kill_quietly(popen)
        else:
            try:
                import psutil

                psutil.Process(pid).kill()
            except Exception:
                pass
        for proc in descendants:
            _kill_quietly(proc)
        if popen is not None:
            try:
                popen.wait(timeout=5)
            except Exception:
                pass
        return

    group = pgid if pgid is not None else pid
    _killpg_quietly(group, signal.SIGTERM)
    for proc in descendants:
        try:
            proc.terminate()
        except Exception:
            pass
    if popen is not None:
        try:
            popen.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
    else:
        time.sleep(grace)
    _killpg_quietly(group, signal.SIGKILL)
    for proc in descendants:
        _kill_quietly(proc)
    if popen is not None:
        try:
            popen.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


# ---------------------------------------------------------------------------
# Spawn
# ---------------------------------------------------------------------------


class TreeProcess:
    """A command running in its own process group, writing to a log file."""

    def __init__(self, popen: subprocess.Popen, log_path: str,
                 job: WindowsJob | None) -> None:
        self.popen = popen
        self.pid = popen.pid
        self.log_path = log_path
        self.job = job
        self.killed = False

    def poll(self) -> int | None:
        return self.popen.poll()

    def wait(self, timeout: float | None = None) -> int | None:
        """The exit code, or None if still running after *timeout* seconds."""
        try:
            return self.popen.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def kill_tree(self) -> None:
        self.killed = True
        kill_process_tree(self.pid, popen=self.popen, job=self.job,
                          pgid=None if _IS_WINDOWS else self.pid)

    def close(self) -> None:
        """Release the job handle. Without kill-on-close, survivors live on."""
        if self.job is not None:
            self.job.close()


def new_log_path(log_dir: str | None = None, prefix: str = "cmd_") -> str:
    """Create an empty, exclusively-created log file and return its path.

    *log_dir* None means the user's temp folder (as-user runs: the workspace
    is writable by the sandbox worker, so nothing that runs with the user's
    rights should open a path there a worker could pre-plant).
    """
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".log", dir=log_dir or None)
    os.close(fd)
    return path


def spawn(
    argv: list[str],
    *,
    cwd: str | None,
    log_path: str,
    env: dict[str, str] | None = None,
    append: bool = False,
    kill_on_close: bool = False,
) -> TreeProcess:
    """Start *argv* in its own process group with stdout+stderr in *log_path*.

    stdin is the null device: a command waiting on input gets EOF instead of
    blocking on the daemon's own stdin.
    """
    mode = "ab" if append else "wb"
    with open(log_path, mode) as log:
        if _IS_WINDOWS:
            popen = subprocess.Popen(
                argv, cwd=cwd, env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=win_no_window_flags() | CREATE_NEW_PROCESS_GROUP,
            )
        else:
            popen = subprocess.Popen(
                argv, cwd=cwd, env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
                creationflags=win_no_window_flags(),
            )
    job = None
    if _IS_WINDOWS:
        job = WindowsJob.create(kill_on_close=kill_on_close)
        if job is not None:
            job.assign(int(popen._handle))  # type: ignore[attr-defined]
    return TreeProcess(popen, log_path, job)


def run_foreground(
    argv: list[str],
    *,
    cwd: str | None,
    timeout: float,
    env: dict[str, str] | None = None,
    log_dir: str | None = None,
    keep_dir: str | None = None,
) -> RunResult:
    """Run *argv* to completion or *timeout*, whichever comes first.

    Returns within the timeout plus the kill grace, whatever the command
    started. On timeout the whole tree is killed and the partial output
    returned. *log_dir* is where the log is written (None: the user's temp
    folder); a truncated log moves to *keep_dir*.
    """
    started = time.monotonic()
    log_path = new_log_path(log_dir)
    try:
        proc = spawn(argv, cwd=cwd, log_path=log_path, env=env)
    except BaseException:
        _remove_quietly(log_path)
        raise
    try:
        code = proc.wait(timeout=timeout)
        timed_out = code is None
        if timed_out:
            logger.info("Command timed out after %ss; killing its process tree "
                        "(pid %d)", timeout, proc.pid)
            proc.kill_tree()
    finally:
        proc.close()
    return collect_result(log_path, exit_code=code, timed_out=timed_out,
                          keep_dir=keep_dir,
                          duration_s=time.monotonic() - started)
