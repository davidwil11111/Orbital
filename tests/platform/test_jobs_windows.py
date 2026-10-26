# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 134 Part B on a real Windows host (CI windows-latest): background jobs
in a kill-on-close Job Object, tree cancel, quit, and the bg.cmd launcher."""

import asyncio
import os
import subprocess
import sys
import time

import psutil
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

from agent_os.daemon_v2 import job_launcher  # noqa: E402
from agent_os.daemon_v2.jobs import JobRegistry  # noqa: E402

SID = "sess-win-1"


async def _inject(*a, **k):
    return "delivered"


def _registry():
    return JobRegistry(inject=_inject, broadcast=lambda *a: None, poll_interval_s=3600)


def _ps(command):
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]


def _tree(pid):
    try:
        p = psutil.Process(pid)
        return [p, *p.children(recursive=True)]
    except psutil.Error:
        return []


def _wait_gone(procs, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(p.is_running() for p in procs):
            return True
        time.sleep(0.2)
    return False


def test_cancel_ends_the_job_and_its_grandchild(tmp_path):
    reg = _registry()
    group = reg.open_group("p", SID, str(tmp_path))
    job = reg.spawn_job(group, "sleep with a child",
                        _ps("Start-Process -NoNewWindow ping -ArgumentList '-n','60','127.0.0.1'; "
                            "Start-Sleep 60"))
    time.sleep(3)
    procs = _tree(job.pid)
    assert len(procs) >= 2
    reg.cancel("p", job.id, notify=False)
    assert _wait_gone(procs)
    assert job.state == "cancelled"


def test_stop_all_closes_the_kill_on_close_job(tmp_path):
    reg = _registry()
    group = reg.open_group("p", SID, str(tmp_path))
    job = reg.spawn_job(group, "long", _ps("Start-Sleep 60"))
    time.sleep(2)
    procs = _tree(job.pid)
    assert reg.stop_all() == 1
    assert _wait_gone(procs)


def test_the_bg_cmd_launcher_records_output_and_exit_code(tmp_path):
    job_launcher.ensure_launcher(str(tmp_path))
    env = {**os.environ, "ORBITAL_SESSION_ID": SID, "ORBITAL_PROJECT_ID": "p"}
    out = subprocess.run(
        [os.path.join(str(tmp_path), "orbital", "bin", "bg.cmd"), "echo hello-win & exit /b 3"],
        cwd=str(tmp_path), capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "Started background job job_" in out.stdout
    assert "taskkill /T /F /PID" in out.stdout
    job_id = out.stdout.split("Started background job ")[1].split(" ")[0]
    d = os.path.join(str(tmp_path), "orbital", "jobs", job_id)
    exit_path = os.path.join(d, "exit")
    deadline = time.monotonic() + 30
    while not os.path.exists(exit_path) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert open(exit_path).read().split()[0] == "3"
    assert "hello-win" in open(os.path.join(d, "output.log"), errors="replace").read()

    reg = _registry()
    reg.watch(str(tmp_path), "p")
    asyncio.run(reg.tick())
    [row] = reg.list_jobs("p")
    assert row["session_id"] == SID
