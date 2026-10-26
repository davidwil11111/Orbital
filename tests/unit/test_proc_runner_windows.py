# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 134 §7 test 2 — the Windows freeze (W1), on CI windows-latest.

``powershell -File`` running a long ``python`` grandchild used to block the
shell call until the grandchild exited: CPython's ``run()`` killed only
PowerShell and then, on Windows only, called ``communicate()`` with no
timeout. Lives in ``tests/unit`` because CI's Windows job runs only that
folder; skipped everywhere else.
"""

from __future__ import annotations

import sys
import time
import uuid

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def _procs_with(marker: str) -> list:
    import psutil

    found = []
    for proc in psutil.process_iter(["cmdline"]):
        try:
            if marker in " ".join(proc.info.get("cmdline") or []):
                found.append(proc)
        except Exception:
            continue
    return found


def _wait_gone(marker: str, within: float = 5.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not _procs_with(marker):
            return True
        time.sleep(0.1)
    return False


def test_powershell_grandchild_timeout_returns_and_leaves_no_python(tmp_path, monkeypatch):
    from agent_os.agent.tools import shell as shell_mod
    from agent_os.agent.tools.shell import ShellTool

    marker = f"orbital_w1_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(shell_mod, "_TIMEOUT", 3)
    tool = ShellTool(workspace=str(tmp_path), os_type="windows")
    command = (f'Write-Output "started"; & "{sys.executable}" -c '
               f'"import time; time.sleep(30)  # {marker}"')

    started = time.monotonic()
    result = tool._execute_unsandboxed(command, {"network": False, "domains": []})
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, f"blocked {elapsed:.1f}s (W1)"
    assert "timed out after 3 seconds" in result.content
    assert "started" in result.content  # partial output survives
    assert _wait_gone(marker), "python.exe outlived the timeout"


def test_a_normal_exit_leaves_what_it_started_running(tmp_path):
    # DECISIONS 2026-10-26: the foreground Job Object has no kill-on-close,
    # so closing it after a normal exit must not take survivors down.
    import psutil

    from agent_os.platform.proc_runner import run_foreground

    marker = f"orbital_survivor_{uuid.uuid4().hex[:8]}"
    argv = [
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        f"Start-Process -WindowStyle Hidden -FilePath '{sys.executable}' "
        f"-ArgumentList '-c','\"import time; time.sleep(20)  # {marker}\"'",
    ]
    result = run_foreground(argv, cwd=str(tmp_path), timeout=30, log_dir=str(tmp_path))
    try:
        assert result.timed_out is False
        time.sleep(1.0)
        assert _procs_with(marker), "the survivor was killed on a normal exit"
    finally:
        for proc in _procs_with(marker):
            try:
                proc.kill()
            except psutil.Error:
                pass


def test_job_object_is_assigned_on_windows(tmp_path):
    from agent_os.platform.proc_runner import spawn

    proc = spawn(["cmd", "/c", "ping -n 3 127.0.0.1 > nul"], cwd=str(tmp_path),
                 log_path=str(tmp_path / "j.log"))
    try:
        # Nested jobs are Windows 8+; CI's runner may itself sit in a job.
        assert proc.job is not None
        assert proc.job.assigned is True
        proc.kill_tree()
        assert proc.poll() is not None
    finally:
        proc.close()
