# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 134 A1 — the one process runner that cannot hang.

Output goes to a file (never a pipe), the command runs in its own process
group, the wait has a timeout, a timeout kills the whole tree, and the
read-back is bounded. A command that exits normally leaves whatever it
started alone (DECISIONS 2026-10-26).

POSIX tests run here; the Windows half is in
``tests/platform/test_proc_runner_windows.py`` (CI windows-latest) plus the
fake-kernel32 job tests at the bottom of this file.
"""

from __future__ import annotations

import os
import sys
import time

import psutil
import pytest

from agent_os.platform import proc_runner
from agent_os.platform.proc_runner import (
    WindowsJob,
    collect_result,
    read_bounded,
    run_foreground,
    spawn,
)

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX runner path")


def _gone(pid: int, within: float = 3.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.05)
    return False


def _pid_from(text: str, key: str) -> int:
    for line in text.splitlines():
        if line.startswith(key + "="):
            return int(line.split("=", 1)[1])
    raise AssertionError(f"{key}= not in output: {text!r}")


# ---------------------------------------------------------------------------
# §7 test 1 — POSIX: timeout kills the tree, normal exit leaves survivors
# ---------------------------------------------------------------------------


@posix_only
def test_timeout_returns_on_time_and_kills_the_grandchild(tmp_path):
    started = time.monotonic()
    result = run_foreground(
        ["bash", "-c", 'sleep 30 & echo "child=$!"; sleep 30'],
        cwd=str(tmp_path), timeout=2, log_dir=str(tmp_path),
    )
    elapsed = time.monotonic() - started

    assert result.timed_out is True
    assert elapsed < 3.0, elapsed
    # Partial output survives the timeout (it used to be discarded).
    child = _pid_from(result.output.text, "child")
    assert _gone(child), "the backgrounded grandchild outlived the timeout"


@posix_only
def test_a_backgrounded_command_returns_at_once_and_its_survivor_is_left_alone(tmp_path):
    started = time.monotonic()
    result = run_foreground(
        ["bash", "-c", 'sleep 30 & echo "child=$!"; echo hi'],
        cwd=str(tmp_path), timeout=20, log_dir=str(tmp_path),
    )
    elapsed = time.monotonic() - started
    child = _pid_from(result.output.text, "child")
    try:
        # No pipe to wait on: ``cmd &`` no longer blocks for the full timeout.
        assert elapsed < 2.0, elapsed
        assert result.timed_out is False and result.exit_code == 0
        assert "hi" in result.output.text
        # Normal exit: whatever the command started keeps running.
        assert psutil.Process(child).is_running()
    finally:
        try:
            psutil.Process(child).kill()
        except psutil.NoSuchProcess:
            pass


@posix_only
def test_exit_code_and_stderr_are_captured(tmp_path):
    result = run_foreground(
        ["bash", "-c", "echo out; echo err >&2; exit 3"],
        cwd=str(tmp_path), timeout=10, log_dir=str(tmp_path),
    )
    assert result.exit_code == 3 and result.timed_out is False
    assert "out" in result.output.text and "err" in result.output.text


@posix_only
def test_stdin_is_not_inherited(tmp_path):
    # A command that reads stdin gets EOF instead of hanging on the daemon's.
    started = time.monotonic()
    result = run_foreground(["bash", "-c", "cat; echo done"], cwd=str(tmp_path),
                            timeout=10, log_dir=str(tmp_path))
    assert time.monotonic() - started < 3
    assert "done" in result.output.text


@posix_only
def test_short_output_leaves_no_log_behind(tmp_path):
    logs = tmp_path / "logs"
    run_foreground(["bash", "-c", "echo hi"], cwd=str(tmp_path), timeout=10,
                   log_dir=str(logs))
    assert list(logs.iterdir()) == []


# ---------------------------------------------------------------------------
# §7 test 3 — 300 MB of output stays on disk, RAM stays flat
# ---------------------------------------------------------------------------


@posix_only
def test_three_hundred_mb_of_output_keeps_rss_bounded(tmp_path):
    keep = tmp_path / "keep"
    me = psutil.Process()
    before = me.memory_info().rss
    result = run_foreground(
        ["bash", "-c", "yes 'a line of chatty progress output' | head -c 300000000"],
        cwd=str(tmp_path), timeout=60, log_dir=str(tmp_path / "logs"),
        keep_dir=str(keep),
    )
    grown = me.memory_info().rss - before

    assert result.timed_out is False
    assert grown < 50 * 1024 * 1024, f"RSS grew {grown / 1e6:.1f} MB"
    out = result.output
    assert out.truncated and out.total_bytes >= 300_000_000
    assert out.total_lines > 8_000_000
    assert len(out.head.splitlines()) == proc_runner.HEAD_LINES
    assert len(out.tail.splitlines()) == proc_runner.TAIL_LINES
    # The full file is on disk for the agent to ``read``.
    assert result.log_path and os.path.dirname(result.log_path) == str(keep)
    assert os.path.getsize(result.log_path) == out.total_bytes
    rendered = out.render(result.log_path)
    assert result.log_path in rendered and len(rendered) < 200_000


# ---------------------------------------------------------------------------
# bounded read-back
# ---------------------------------------------------------------------------


def test_read_bounded_short_file_is_returned_whole(tmp_path):
    path = tmp_path / "a.log"
    path.write_bytes(b"one\r\ntwo\nthree")
    out = read_bounded(str(path))
    assert out.truncated is False
    assert out.text == "one\ntwo\nthree"


def test_read_bounded_many_lines_gives_head_and_tail(tmp_path):
    path = tmp_path / "a.log"
    path.write_text("\n".join(f"line {i}" for i in range(300)), encoding="utf-8")
    out = read_bounded(str(path))
    assert out.truncated is True and out.total_lines == 300
    assert out.head.splitlines()[0] == "line 0" and out.head.splitlines()[-1] == "line 19"
    assert out.tail.splitlines()[0] == "line 250" and out.tail.splitlines()[-1] == "line 299"
    rendered = out.render("/x/saved.txt")
    assert "... [truncated 300 lines, saved to /x/saved.txt] ..." in rendered


def test_read_bounded_one_giant_line_is_bounded_by_bytes(tmp_path):
    path = tmp_path / "a.log"
    path.write_bytes(b"x" * (5 * 1024 * 1024))
    out = read_bounded(str(path))
    assert out.truncated is True
    assert len(out.render(None)) <= proc_runner.HEAD_BYTES + proc_runner.TAIL_BYTES + 200


def test_read_bounded_never_breaks_on_a_split_utf8_character(tmp_path):
    path = tmp_path / "a.log"
    path.write_bytes(("中文" * 100_000).encode("utf-8"))
    out = read_bounded(str(path))
    assert out.truncated is True
    assert "中文" in out.head and "中文" in out.tail


def test_read_bounded_missing_file_is_empty(tmp_path):
    out = read_bounded(str(tmp_path / "nope.log"))
    assert out.text == "" and out.truncated is False and out.total_bytes == 0


def test_collect_result_moves_a_truncated_log_and_removes_a_short_one(tmp_path):
    keep = tmp_path / "keep"
    big = tmp_path / "big.log"
    big.write_text("\n".join(str(i) for i in range(500)), encoding="utf-8")
    res = collect_result(str(big), exit_code=0, timed_out=False, keep_dir=str(keep))
    assert res.log_path == str(keep / "big.log") and os.path.exists(res.log_path)
    assert not big.exists()

    small = tmp_path / "small.log"
    small.write_text("hello\n", encoding="utf-8")
    res = collect_result(str(small), exit_code=0, timed_out=False, keep_dir=str(keep))
    assert res.log_path is None and not small.exists()
    assert res.output.text == "hello\n"


# ---------------------------------------------------------------------------
# The Part B surface: spawn without waiting, poll, kill the tree
# ---------------------------------------------------------------------------


@posix_only
def test_spawn_poll_and_kill_tree(tmp_path):
    log = tmp_path / "job.log"
    proc = spawn(["bash", "-c", 'sleep 30 & echo "child=$!"; sleep 30'],
                 cwd=str(tmp_path), log_path=str(log))
    try:
        deadline = time.monotonic() + 5
        while "child=" not in log.read_text() and time.monotonic() < deadline:
            time.sleep(0.05)
        child = _pid_from(log.read_text(), "child")
        assert proc.poll() is None
        assert proc.wait(timeout=0.2) is None
        proc.kill_tree()
        assert proc.poll() is not None
        assert _gone(child)
    finally:
        proc.close()


@posix_only
def test_spawn_appends_when_asked(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("earlier\n", encoding="utf-8")
    proc = spawn(["bash", "-c", "echo later"], cwd=str(tmp_path),
                 log_path=str(log), append=True)
    assert proc.wait(timeout=10) == 0
    proc.close()
    assert log.read_text() == "earlier\nlater\n"


# ---------------------------------------------------------------------------
# Windows Job Object, with a fake kernel32 (runs everywhere)
# ---------------------------------------------------------------------------


class _FakeKernel32:
    def __init__(self, assign_ok=True):
        self.calls: list[tuple] = []
        self.assign_ok = assign_ok
        self.limit_flags = None

    def CreateJobObjectW(self, attrs, name):
        self.calls.append(("create",))
        return 1234

    def SetInformationJobObject(self, job, info_class, info_ptr, size):
        self.calls.append(("set_info", info_class))
        self.limit_flags = proc_runner._extended_limit_flags(info_ptr)
        return 1

    def AssignProcessToJobObject(self, job, process):
        self.calls.append(("assign", job, process))
        return 1 if self.assign_ok else 0

    def TerminateJobObject(self, job, code):
        self.calls.append(("terminate", job, code))
        return 1

    def CloseHandle(self, handle):
        self.calls.append(("close", handle))
        return 1


def test_foreground_job_never_sets_kill_on_close():
    # DECISIONS 2026-10-26: closing the handle after a normal exit must not
    # kill survivors, so the foreground job carries no limit at all.
    k = _FakeKernel32()
    job = WindowsJob.create(kill_on_close=False, kernel32=k)
    assert job is not None
    assert not any(c[0] == "set_info" for c in k.calls)
    assert job.assign(77) is True
    job.close()
    assert ("terminate", 1234, 1) not in k.calls
    assert ("close", 1234) in k.calls


def test_kill_on_close_job_sets_the_flag_for_part_b():
    k = _FakeKernel32()
    WindowsJob.create(kill_on_close=True, kernel32=k)
    assert k.limit_flags is not None
    assert k.limit_flags & proc_runner.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE


def test_job_terminate_calls_terminate_job_object():
    k = _FakeKernel32()
    job = WindowsJob.create(kill_on_close=False, kernel32=k)
    job.assign(77)
    assert job.terminate() is True
    assert ("terminate", 1234, 1) in k.calls


def test_failed_assignment_is_reported_so_the_caller_falls_back():
    # A parent's job that forbids nesting (spec §8): assignment fails and
    # the tree kill falls back to psutil.
    k = _FakeKernel32(assign_ok=False)
    job = WindowsJob.create(kill_on_close=False, kernel32=k)
    assert job.assign(77) is False
    assert job.assigned is False


def test_kill_process_tree_windows_uses_the_job_then_psutil(monkeypatch):
    killed: list[str] = []

    class _P:
        def __init__(self, name):
            self.name = name

        def kill(self):
            killed.append(self.name)

    class _Job:
        assigned = True

        def terminate(self):
            killed.append("job")
            return True

    class _Popen:
        def kill(self):
            killed.append("leader")

        def wait(self, timeout=None):
            return 1

    monkeypatch.setattr(proc_runner, "_IS_WINDOWS", True)
    monkeypatch.setattr(proc_runner, "_descendants", lambda pid: [_P("grandchild")])
    proc_runner.kill_process_tree(42, job=_Job(), popen=_Popen())
    assert killed == ["job", "leader", "grandchild"]
