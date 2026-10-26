# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 134 Part B §3.2: ``shell`` gains ``background: true``."""

import contextvars
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from agent_os.agent import loop as loop_mod
from agent_os.agent.tools import shell as shell_mod
from agent_os.agent.tools.shell import ShellTool
from agent_os.daemon_v2.jobs import JobRegistry

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shells")

PROJECT = "proj_sh"
SID = "sess-sh-1"


async def _noop_inject(*a, **k):
    return "delivered"


@pytest.fixture
def registry(tmp_path):
    reg = JobRegistry(inject=_noop_inject, broadcast=lambda *a: None,
                      workspaces=lambda: [], poll_interval_s=3600)
    yield reg
    reg.stop_all()


def _tool(ws, registry, **kw):
    return ShellTool(workspace=str(ws), os_type="macos", project_id=PROJECT,
                     safe_mode_cb=lambda: False, jobs=registry, session_id=SID, **kw)


def _in_batch(batch_id, fn, *args, **kwargs):
    ctx = contextvars.copy_context()

    def run():
        loop_mod._tool_batch.set(batch_id)
        return fn(*args, **kwargs)
    return ctx.run(run)


def test_the_schema_offers_background_only_when_jobs_are_wired(tmp_path, registry):
    with_jobs = _tool(tmp_path, registry)
    props = with_jobs.parameters["properties"]
    assert props["background"]["type"] == "boolean"
    assert "background" in with_jobs.description.lower()
    plain = ShellTool(workspace=str(tmp_path), os_type="macos")
    assert "background" not in plain.parameters["properties"]


def test_the_timeout_hint_names_background_true():
    assert "background: true" in shell_mod._TIMEOUT_HINT


def test_a_background_call_returns_at_once_with_the_job_and_yields_after_the_batch(
        tmp_path, registry):
    tool = _tool(tmp_path, registry)
    t0 = time.monotonic()
    result = tool.execute(command="echo started; sleep 20", background=True)
    assert time.monotonic() - t0 < 2
    assert result.content.startswith("Started background job job_")
    assert "kill -TERM -- -" in result.content
    assert result.meta["yield_after_batch"] is True
    job_meta = result.meta["background_job"]
    job = registry.get(job_meta["job_id"])
    assert job.state == "running" and job.session_id == SID
    assert job_meta["project_id"] == PROJECT and job_meta["group_id"] == job.group_id
    assert job_meta["command"] == "echo started; sleep 20"
    time.sleep(0.3)
    assert "started" in open(job.log_path).read()


def test_two_calls_in_one_batch_share_a_group(tmp_path, registry):
    tool = _tool(tmp_path, registry)
    a = _in_batch("batch-1", tool.execute, command="sleep 5", background=True)
    b = _in_batch("batch-1", tool.execute, command="sleep 5", background=True)
    c = _in_batch("batch-2", tool.execute, command="sleep 5", background=True)
    ga, gb, gc = (r.meta["background_job"]["group_id"] for r in (a, b, c))
    assert ga == gb != gc


def test_a_refused_job_is_an_error_that_does_not_end_the_turn(tmp_path, registry):
    tool = _tool(tmp_path, registry)
    for _ in range(8):
        tool.execute(command="sleep 30", background=True)
    result = tool.execute(command="sleep 30", background=True)
    assert result.content.startswith("Error: not started")
    assert "yield_after_batch" not in (result.meta or {})


def test_background_without_jobs_runs_in_the_foreground(tmp_path):
    tool = ShellTool(workspace=str(tmp_path), os_type="macos", safe_mode_cb=lambda: False)
    result = tool.execute(command="echo fg", background=True)
    assert result.content.startswith("Exit code: 0") and "fg" in result.content


def test_the_job_env_drops_the_allowlist_proxy(tmp_path, registry, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    tool = _tool(tmp_path, registry)
    result = tool.execute(command="echo proxy=${HTTPS_PROXY:-none}", background=True)
    job = registry.get(result.meta["background_job"]["job_id"])
    job.proc.wait(5)
    assert "proxy=none" in open(job.log_path).read()


class _RunningProvider:
    """A provider whose sandbox is set up and which just runs the command."""

    def __init__(self):
        self.commands = []

    def get_capabilities(self):
        return SimpleNamespace(setup_complete=True)

    async def run_command(self, *, project_id, command, args, working_dir, timeout_sec):
        self.commands.append(args[-1])
        out = subprocess.run([command, *args], cwd=working_dir, capture_output=True,
                             text=True, timeout=timeout_sec)
        return SimpleNamespace(stdout=out.stdout, stderr=out.stderr,
                               exit_code=out.returncode, timed_out=False)


def test_a_sandboxed_background_call_starts_through_the_launcher(tmp_path, registry):
    provider = _RunningProvider()
    tool = ShellTool(workspace=str(tmp_path), os_type="macos", project_id=PROJECT,
                     platform_provider=provider, safe_mode_cb=lambda: True,
                     jobs=registry, session_id=SID)
    result = _in_batch("batch-s", tool.execute, command="echo in-sandbox; exit 2",
                       background=True)
    assert "orbital/bin/bg" in provider.commands[0]
    assert result.meta["yield_after_batch"] is True
    job = registry.get(result.meta["background_job"]["job_id"])
    assert job is not None and job.source == "launcher" and job.session_id == SID
    assert result.content.startswith(f"Started background job {job.id}")
    # the same batch's next call joins the same group
    again = _in_batch("batch-s", tool.execute, command="true", background=True)
    assert again.meta["background_job"]["group_id"] == job.group_id
    deadline = time.monotonic() + 5
    while not os.path.exists(os.path.join(job.dir, "exit")) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert open(os.path.join(job.dir, "exit")).read().split()[0] == "2"
    assert "in-sandbox" in open(job.log_path).read()
