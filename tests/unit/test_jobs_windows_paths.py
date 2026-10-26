# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 134 Part B, the Windows code paths with the Win32 parts faked, so
they are pinned on every host. The real thing runs on CI windows-latest
(tests/platform/test_jobs_windows.py)."""

import os
import re
from types import SimpleNamespace

import pytest

from agent_os.agent.tools import shell as shell_mod
from agent_os.agent.tools.shell import ShellTool
from agent_os.daemon_v2 import job_launcher, jobs
from agent_os.daemon_v2.jobs import JobRegistry


async def _inject(*a, **k):
    return "delivered"


class _FakeTree:
    def __init__(self, pid=4242):
        self.pid = pid
        self.closed = False
        self.killed = False

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 1

    def kill_tree(self):
        self.killed = True

    def close(self):
        self.closed = True


def test_windows_jobs_get_a_kill_on_close_job_object_held_until_quit(tmp_path, monkeypatch):
    seen = {}

    def fake_spawn(argv, *, cwd, log_path, env=None, append=False, kill_on_close=False):
        seen.update(argv=argv, append=append, kill_on_close=kill_on_close)
        open(log_path, "ab").close()
        return _FakeTree()
    import agent_os.platform.proc_runner as pr
    monkeypatch.setattr(pr, "spawn", fake_spawn)
    monkeypatch.setattr(jobs, "_create_time", lambda pid: 1.0)
    registry = JobRegistry(inject=_inject, broadcast=lambda *a: None)
    tool = ShellTool(workspace=str(tmp_path), os_type="windows", project_id="p",
                     safe_mode_cb=lambda: False, jobs=registry, session_id="s")
    result = tool.execute(command="python train.py", background=True)
    assert seen["kill_on_close"] is True and seen["append"] is True
    assert seen["argv"][:5] == ["powershell", "-NoProfile", "-NonInteractive",
                                "-ExecutionPolicy", "Bypass"]
    job = registry.get(result.meta["background_job"]["job_id"])
    script = seen["argv"][-1]
    assert job.scratch == [script] and os.path.exists(script)
    # the job handle stays open while the job runs; quitting closes it
    assert job.proc.closed is False
    registry.stop_all()
    assert job.proc.killed and job.proc.closed
    os.remove(script)


def test_windows_kill_command_is_taskkill_tree(monkeypatch):
    monkeypatch.setattr(jobs, "_IS_WINDOWS", True)
    assert jobs.kill_command(51234) == "taskkill /T /F /PID 51234"


def test_the_windows_launcher_is_bg_cmd():
    assert job_launcher.launcher_path("C:\\ws", "windows").endswith("bg.cmd")
    assert "bg.ps1" in job_launcher.BG_CMD
    assert "Win32_Process" in job_launcher.BG_PS1          # outside the caller's job
    assert "taskkill /T /F /PID" in job_launcher.BG_PS1
    assert "job.json.tmp" in job_launcher.BG_PS1          # job.json written last


def test_a_sandboxed_windows_background_call_runs_bg_ps1_with_the_attribution(
        tmp_path, monkeypatch):
    registry = JobRegistry(inject=_inject, broadcast=lambda *a: None)
    calls = []

    class Provider:
        def get_capabilities(self):
            return SimpleNamespace(setup_complete=True)

        async def run_command(self, *, project_id, command, args, working_dir, timeout_sec):
            calls.append(open(args[-1], encoding="utf-8-sig").read())
            return SimpleNamespace(stdout="Started background job job_0000abcd (pid 7).\n",
                                   stderr="", exit_code=0, timed_out=False)
    tool = ShellTool(workspace=str(tmp_path), os_type="windows", project_id="proj",
                     platform_provider=Provider(), safe_mode_cb=lambda: True,
                     jobs=registry, session_id="sess-1")
    d = os.path.join(str(tmp_path), "orbital", "jobs", "job_0000abcd")
    os.makedirs(d)
    open(os.path.join(d, "job.json"), "w").write(
        '{"id": "job_0000abcd", "pid": 7, "state": "running", "started_at": 1}')
    open(os.path.join(d, "cmd"), "w").write("npm run build it's\n")
    monkeypatch.setattr(jobs, "_identity_alive", lambda *a, **k: False)
    result = tool.execute(command="npm run build it's", background=True)
    script = calls[0]
    assert "$env:ORBITAL_SESSION_ID='sess-1'" in script
    assert "$env:ORBITAL_PROJECT_ID='proj'" in script
    # The launcher runs cmd.exe, so it is handed the same .ps1 wrapper every
    # other call runs (batch 1026 Windows pass); the job keeps the command.
    assert "bg.ps1' 'powershell -NoProfile -NonInteractive" in script
    wrapper = re.search(r"-File (\S+cmd_[0-9a-f]{12}\.ps1)", script).group(1)
    assert "npm run build it's" in open(wrapper, encoding="utf-8-sig").read()
    assert result.meta["background_job"]["job_id"] == "job_0000abcd"
    assert registry.get("job_0000abcd").command == "npm run build it's"


def test_a_launcher_that_did_not_start_is_an_error(tmp_path):
    registry = JobRegistry(inject=_inject, broadcast=lambda *a: None)

    class Provider:
        def get_capabilities(self):
            return SimpleNamespace(setup_complete=True)

        async def run_command(self, **kw):
            return SimpleNamespace(stdout="", stderr="Access is denied.", exit_code=1,
                                   timed_out=False)
    tool = ShellTool(workspace=str(tmp_path), os_type="windows", project_id="proj",
                     platform_provider=Provider(), safe_mode_cb=lambda: True,
                     jobs=registry, session_id="s")
    result = tool.execute(command="x", background=True)
    assert result.content.startswith("Error: the background job could not start")
    assert "yield_after_batch" not in (result.meta or {})
