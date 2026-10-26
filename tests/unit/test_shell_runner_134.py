# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 134 A1 — every shell path goes through the runner that cannot hang.

The three paths: the unsandboxed (as-user) run, macOS ``run_command``
(Seatbelt) and Windows ``run_and_capture`` (worker account). Each returns at
its timeout with the partial output and a hint that long work belongs in the
background, kills the whole tree on timeout only, and reads its output back
bounded.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import psutil
import pytest

from agent_os.agent.tools import shell as shell_mod
from agent_os.agent.tools.shell import ShellTool
from agent_os.platform.proc_runner import RunResult, read_bounded

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell path")
_META = {"network": False, "domains": []}


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


# ---------------------------------------------------------------------------
# unsandboxed path, real bash
# ---------------------------------------------------------------------------


@posix_only
def test_timeout_returns_partial_output_and_the_background_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(shell_mod, "_TIMEOUT", 2)
    tool = ShellTool(workspace=str(tmp_path), os_type="macos")
    started = time.monotonic()
    result = tool._execute_unsandboxed(
        'echo started; sleep 30 & echo "child=$!"; sleep 30', dict(_META))
    elapsed = time.monotonic() - started

    assert elapsed < 3.5, elapsed
    assert "timed out after 2 seconds" in result.content
    assert "started" in result.content
    assert "background" in result.content
    child = int(result.content.split("child=")[1].split()[0])
    assert _gone(child), "the grandchild outlived the timeout"


@posix_only
def test_a_backgrounded_command_does_not_block_the_call(tmp_path):
    tool = ShellTool(workspace=str(tmp_path), os_type="macos")
    started = time.monotonic()
    result = tool._execute_unsandboxed('sleep 30 & echo "child=$!"; echo hi', dict(_META))
    child = int(result.content.split("child=")[1].split()[0])
    try:
        assert time.monotonic() - started < 2.0
        assert result.content.startswith("Exit code: 0")
        assert "hi" in result.content
    finally:
        try:
            psutil.Process(child).kill()
        except psutil.NoSuchProcess:
            pass


@posix_only
def test_long_output_is_head_and_tail_with_the_full_file_kept(tmp_path):
    tool = ShellTool(workspace=str(tmp_path), os_type="macos")
    result = tool._execute_unsandboxed(
        "for i in $(seq 0 299); do echo line $i; done", dict(_META))
    assert "line 0" in result.content and "line 19" in result.content
    assert "line 251" in result.content and "line 299" in result.content
    assert "line 150" not in result.content
    assert "truncated 301 lines, saved to" in result.content
    saved = result.content.split("saved to ")[1].split("]")[0]
    kept_dir = tmp_path / "orbital" / "output" / "shell-output"
    assert os.path.dirname(saved) == str(kept_dir)
    assert "line 150" in open(saved, encoding="utf-8").read()


@posix_only
def test_no_temp_log_is_left_for_a_short_command(tmp_path, monkeypatch):
    logs = tmp_path / "tmp-logs"
    logs.mkdir()
    monkeypatch.setattr(shell_mod.tempfile, "tempdir", str(logs))
    tool = ShellTool(workspace=str(tmp_path / "ws"), os_type="macos")
    (tmp_path / "ws").mkdir()
    tool._execute_unsandboxed("echo hi", dict(_META))
    assert [p.name for p in logs.iterdir()] == []


def test_the_unsandboxed_path_uses_the_runner(tmp_path):
    from tests.shell_doubles import fake_shell_run

    tool = ShellTool(workspace=str(tmp_path), os_type="macos")
    with fake_shell_run(stdout="ok\n") as fake:
        result = tool._execute_unsandboxed("echo ok", dict(_META))
    assert fake.calls[0]["argv"] == ["bash", "-c", "echo ok"]
    assert fake.calls[0]["timeout"] == shell_mod._TIMEOUT
    # As-user: the log goes to the user's temp folder, not the workspace.
    assert fake.calls[0]["log_dir"] is None
    assert result.content == "Exit code: 0\nok\n"


# ---------------------------------------------------------------------------
# macOS run_command
# ---------------------------------------------------------------------------


def _macos_provider(monkeypatch):
    from agent_os.platform.macos import provider as mac_mod

    prov = mac_mod.MacOSPlatformProvider()

    async def _no_proxy(project_id):
        return None

    monkeypatch.setattr(prov, "_ensure_proxy", _no_proxy)
    monkeypatch.setattr(mac_mod, "generate_profile", lambda **kw: "(version 1)")
    return prov, mac_mod


def test_macos_run_command_goes_through_the_runner(tmp_path, monkeypatch):
    prov, mac_mod = _macos_provider(monkeypatch)
    seen = {}

    def fake_run(argv, **kw):
        seen.update(kw, argv=argv)
        path = tmp_path / "out.log"
        path.write_text("hello\n", encoding="utf-8")
        return RunResult(exit_code=0, timed_out=False,
                         output=read_bounded(str(path)), log_path=None)

    monkeypatch.setattr(mac_mod, "run_foreground", fake_run)
    result = asyncio.run(prov.run_command("p1", "bash", ["-c", "echo hello"],
                                          str(tmp_path), timeout_sec=7))
    assert seen["argv"][:3] == ["sandbox-exec", "-p", "(version 1)"]
    assert seen["argv"][3:] == ["bash", "-c", "echo hello"]
    assert seen["timeout"] == 7
    # Inside the workspace (the sandbox can write there), kept under output/.
    assert seen["log_dir"] == os.path.join(str(tmp_path), "orbital", ".tmp")
    assert seen["keep_dir"] == os.path.join(str(tmp_path), "orbital", "output", "shell-output")
    assert result.exit_code == 0 and result.stdout == "hello\n" and not result.timed_out


def test_macos_run_command_timeout_keeps_the_partial_output(tmp_path, monkeypatch):
    prov, mac_mod = _macos_provider(monkeypatch)

    def fake_run(argv, **kw):
        path = tmp_path / "out.log"
        path.write_text("partial\n", encoding="utf-8")
        return RunResult(exit_code=None, timed_out=True,
                         output=read_bounded(str(path)), log_path=None)

    monkeypatch.setattr(mac_mod, "run_foreground", fake_run)
    result = asyncio.run(prov.run_command("p1", "bash", ["-c", "x"], str(tmp_path)))
    assert result.timed_out is True and result.exit_code == 1
    assert result.stdout == "partial\n"


def test_provider_timeout_message_carries_output_and_hint(tmp_path):
    from agent_os.platform.types import CommandResult

    provider = MagicMock()
    provider.get_capabilities.return_value = SimpleNamespace(setup_complete=True)

    async def run_command(**kw):
        return CommandResult(exit_code=1, stdout="partial\n", stderr="", timed_out=True)

    provider.run_command = run_command
    tool = ShellTool(workspace=str(tmp_path), os_type="macos", platform_provider=provider)
    result = tool._execute_via_provider("sleep 999", dict(_META))
    assert "timed out after 120 seconds" in result.content
    assert "partial" in result.content and "background" in result.content


# ---------------------------------------------------------------------------
# Windows run_and_capture (Win32 layer stubbed; the real one runs on CI)
# ---------------------------------------------------------------------------


@pytest.fixture
def win_process(monkeypatch):
    for name in ("windll", "GetLastError", "FormatError",
                 "get_last_error", "set_last_error"):
        if not hasattr(ctypes, name):
            monkeypatch.setattr(ctypes, name, MagicMock(), raising=False)
    from agent_os.platform.windows import process as win_mod

    return win_mod


def _launcher(win_mod, monkeypatch, tmp_path, *, wait_raises: bool, write: str = ""):
    from agent_os.platform.types import ProcessHandle

    events: list = []
    launcher = win_mod.ProcessLauncher(MagicMock())

    def fake_launch(command, args, working_dir, env_vars=None, inherit_env=True):
        inner = args[1]
        events.append(("launch", command, inner))
        stdout_path = inner.split('> "')[1].split('"')[0]
        with open(stdout_path, "w", encoding="utf-8") as f:
            f.write(write)
        return ProcessHandle(pid=4242, command="cmd", _native_handles={
            "process_handle": 11, "thread_handle": 12})

    def fake_wait(handle, timeout_sec=None):
        events.append(("wait", timeout_sec))
        if wait_raises:
            raise TimeoutError("late")
        return 0

    class _Job:
        assigned = True

        def assign(self, h):
            events.append(("assign", h))
            return True

        def close(self):
            events.append(("job_close",))

    monkeypatch.setattr(launcher, "launch", fake_launch)
    monkeypatch.setattr(launcher, "wait", fake_wait)
    monkeypatch.setattr(launcher, "terminate", lambda h, timeout_sec=10: events.append(("graceful",)))
    monkeypatch.setattr(win_mod.WindowsJob, "create",
                        classmethod(lambda cls, kill_on_close=False, kernel32=None: _Job()))
    monkeypatch.setattr(win_mod, "kill_process_tree",
                        lambda pid, **kw: events.append(("kill_tree", pid, kw.get("job") is not None)))
    monkeypatch.setattr(win_mod, "_close_process_handles", lambda h: events.append(("close_handles",)))
    return launcher, events


def test_windows_capture_timeout_kills_the_tree_through_the_job(win_process, monkeypatch, tmp_path):
    launcher, events = _launcher(win_process, monkeypatch, tmp_path,
                                 wait_raises=True, write="partial\n")
    result = launcher.run_and_capture("powershell", ["-File", "x.ps1"], str(tmp_path),
                                      timeout_sec=3)
    assert result.timed_out is True
    assert result.stdout == "partial\n"
    assert ("assign", 11) in events
    assert ("kill_tree", 4242, True) in events
    # No 10 s CTRL_BREAK dance on a timeout: the tree is killed outright.
    assert ("graceful",) not in events
    assert ("job_close",) in events and ("close_handles",) in events


def test_windows_capture_normal_exit_leaves_survivors(win_process, monkeypatch, tmp_path):
    launcher, events = _launcher(win_process, monkeypatch, tmp_path,
                                 wait_raises=False, write="done\n")
    result = launcher.run_and_capture("cmd", ["/c", "echo done"], str(tmp_path))
    assert result.timed_out is False and result.exit_code == 0
    assert result.stdout == "done\n"
    assert not any(e[0] == "kill_tree" for e in events)
    assert ("job_close",) in events and ("close_handles",) in events


def test_windows_capture_output_is_bounded(win_process, monkeypatch, tmp_path):
    big = "\n".join(f"row {i}" for i in range(1000))
    launcher, _ = _launcher(win_process, monkeypatch, tmp_path, wait_raises=False, write=big)
    result = launcher.run_and_capture("cmd", ["/c", "x"], str(tmp_path))
    assert "row 0" in result.stdout and "row 999" in result.stdout
    assert "row 500" not in result.stdout
    assert "truncated 1000 lines, saved to" in result.stdout
    saved = result.stdout.split("saved to ")[1].split("]")[0]
    assert os.path.dirname(saved) == os.path.join(str(tmp_path), "orbital", "output", "shell-output")
