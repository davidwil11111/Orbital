# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 117 D4 — an APPROVED shell call the sandbox blocked raises the card.

Under 需确认 (check_in) every shell call is intercepted first. When the user
approves it, ``AgentManager.approve()`` executes the tool itself (not the
loop). A safe-mode result carrying ``meta["sandbox_denial"]`` must then raise
the ``unsandboxed_run`` card exactly like the loop's tool-result path does —
the session stays paused for approval and the loop is NOT restarted; it
resumes when the user answers the card. Live regression (Windows): approving
``cmd /c type private\\note.txt`` produced the ``[sandbox]`` hint but no card.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_os.agent.prompt_builder import Autonomy
from agent_os.agent.session import Session, persist_user_row
from agent_os.agent.tools.base import ToolResult
from agent_os.daemon_v2.agent_manager import AgentManager, ProjectHandle
from agent_os.daemon_v2.autonomy import UNSANDBOXED_RUN_KIND, AutonomyInterceptor
from agent_os.daemon_v2.models import make_session_key

PROJECT = "proj_approve_denial"
SID = "sess-approve-denial-0001"
COMMAND = r"cmd /c type private\note.txt"
DENIAL = {"command": COMMAND, "reason": "Access is denied."}


class _Registry:
    """``shell`` returns a safe-mode sandbox denial (or a plain result)."""

    def __init__(self, denial=DENIAL):
        self._denial = denial
        self.executed: list[tuple[str, dict]] = []

    def is_async(self, name):
        return False

    def execute(self, name, arguments):
        self.executed.append((name, arguments))
        if self._denial is None:
            return ToolResult(content="Exit code: 0\nhello")
        return ToolResult(
            content="Exit code: 1\nAccess is denied.\n[sandbox] blocked",
            meta={"network": False, "sandbox_denial": self._denial},
        )


def _manager(project):
    store = MagicMock()
    store.get_project.side_effect = lambda pid: project if pid == PROJECT else None
    store.update_project.side_effect = lambda pid, updates: project.update(updates)
    ws = MagicMock()
    mgr = AgentManager(
        project_store=store, ws_manager=ws, sub_agent_manager=MagicMock(),
        activity_translator=MagicMock(), process_manager=MagicMock(),
        platform_provider=MagicMock(),
    )
    mgr._platform_provider.clear_network = AsyncMock()
    mgr._start_loop = AsyncMock()
    return mgr, ws


def _project(tmp_path, **over):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    base = {"project_id": PROJECT, "name": "P", "workspace": str(ws), "safe_mode": True,
            "unsandboxed_executables": [], "pending_unsandboxed_runs": [],
            "autonomy": "check_in"}
    base.update(over)
    return base


def _plant(mgr, tmp_path, project, *, registry, queue_state="chat",
           preset=Autonomy.CHECK_IN):
    """A check_in session paused on an intercepted shell call ``call_0``."""
    session = Session.new(SID, str(tmp_path))
    persist_user_row(session, "show me the note")
    session.append({"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_0", "type": "function",
         "function": {"name": "shell", "arguments": '{"command": "x"}'}}]})
    interceptor = AutonomyInterceptor(preset, mgr._ws, PROJECT, session_id=SID)
    interceptor.on_intercept(
        {"id": "call_0", "name": "shell", "arguments": {"command": COMMAND}}, [])
    session._paused_for_approval = True
    session.pause()
    loop = MagicMock()
    loop._queue_state = queue_state
    handle = ProjectHandle(session=session, loop=loop, provider=MagicMock(),
                           registry=registry, context_manager=MagicMock(),
                           interceptor=interceptor, task=None,
                           config_snapshot={"workspace": project["workspace"]})
    mgr._handles[make_session_key(PROJECT, SID)] = handle
    return handle


def _card_requests(ws):
    return [c[0][1] for c in ws.broadcast.call_args_list
            if c[0][1].get("type") == "approval.request"
            and c[0][1].get("kind") == UNSANDBOXED_RUN_KIND]


@pytest.mark.asyncio
async def test_approved_shell_with_sandbox_denial_raises_the_unsandboxed_run_card(tmp_path):
    project = _project(tmp_path)
    mgr, ws = _manager(project)
    registry = _Registry()
    handle = _plant(mgr, tmp_path, project, registry=registry)

    await mgr.approve(PROJECT, "call_0", session_id=SID)

    # The approved call ran and got its ([sandbox]) tool result.
    assert registry.executed == [("shell", {"command": COMMAND})]
    tool_rows = [m for m in handle.session.get_messages() if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tool_rows] == ["call_0"]
    assert "[sandbox]" in tool_rows[0]["content"]

    # The original pending is gone; exactly one unsandboxed_run pending is up.
    pendings = handle.interceptor._pending_approvals
    assert "call_0" not in pendings
    assert len(pendings) == 1
    pending_id, pending = next(iter(pendings.items()))
    assert pending["kind"] == UNSANDBOXED_RUN_KIND
    assert pending["command"] == COMMAND
    assert pending["origin_tool_call_id"] == "call_0"

    cards = _card_requests(ws)
    assert len(cards) == 1
    assert cards[0]["tool_call_id"] == pending_id
    assert cards[0]["command"] == COMMAND

    # Paused on the card — the loop is NOT restarted.
    assert handle.session._paused_for_approval is True
    assert handle.session.is_paused()
    mgr._start_loop.assert_not_awaited()
    statuses = [c[0][1] for c in ws.broadcast.call_args_list
                if c[0][1].get("type") == "agent.status"]
    assert statuses and statuses[-1]["status"] == "pending_approval"


@pytest.mark.asyncio
async def test_answering_the_card_raised_by_approve_runs_it_and_resumes(tmp_path):
    project = _project(tmp_path)
    mgr, _ = _manager(project)
    handle = _plant(mgr, tmp_path, project, registry=_Registry())
    await mgr.approve(PROJECT, "call_0", session_id=SID)
    pending_id = next(iter(handle.interceptor._pending_approvals))

    ran: list[str] = []

    async def _run(project_id, command):
        ran.append(command)
        return "Exit code: 0\nsecret note"
    mgr._run_unsandboxed_command = _run

    await mgr.approve(PROJECT, pending_id, session_id=SID, response_payload="once")
    assert ran == [COMMAND]
    assert handle.interceptor._pending_approvals == {}
    assert handle.session._paused_for_approval is False
    mgr._start_loop.assert_awaited_once_with(PROJECT, session_id=SID)


@pytest.mark.asyncio
async def test_approved_shell_without_denial_restarts_the_loop(tmp_path):
    project = _project(tmp_path)
    mgr, ws = _manager(project)
    handle = _plant(mgr, tmp_path, project, registry=_Registry(denial=None))
    await mgr.approve(PROJECT, "call_0", session_id=SID)
    assert handle.interceptor._pending_approvals == {}
    assert _card_requests(ws) == []
    assert handle.session._paused_for_approval is False
    mgr._start_loop.assert_awaited_once_with(PROJECT, session_id=SID)


@pytest.mark.asyncio
async def test_queue_run_never_raises_the_card_from_approve(tmp_path):
    project = _project(tmp_path)
    mgr, ws = _manager(project)
    handle = _plant(mgr, tmp_path, project, registry=_Registry(), queue_state="running")
    await mgr.approve(PROJECT, "call_0", session_id=SID)
    assert handle.interceptor._pending_approvals == {}
    assert _card_requests(ws) == []
    mgr._start_loop.assert_awaited_once_with(PROJECT, session_id=SID)


@pytest.mark.asyncio
async def test_open_project_never_raises_the_card_from_approve(tmp_path):
    project = _project(tmp_path, safe_mode=False)
    mgr, ws = _manager(project)
    handle = _plant(mgr, tmp_path, project, registry=_Registry())
    handle.interceptor._safe_mode_cb = lambda: False
    await mgr.approve(PROJECT, "call_0", session_id=SID)
    assert _card_requests(ws) == []
    mgr._start_loop.assert_awaited_once_with(PROJECT, session_id=SID)


# ---------------------------------------------------------------------------
# The shared helper (used by both the loop and approve())
# ---------------------------------------------------------------------------


def test_helper_ignores_results_without_a_denial():
    from agent_os.agent.loop import maybe_raise_unsandboxed_run
    interceptor = MagicMock()
    tc = {"id": "c", "name": "shell", "arguments": {"command": "ls"}}
    assert maybe_raise_unsandboxed_run(
        interceptor, tc, ToolResult(content="ok"), lambda: []) is None
    interceptor.on_sandbox_denial.assert_not_called()


def test_helper_skips_queue_runs_and_interceptors_without_the_hook():
    from agent_os.agent.loop import maybe_raise_unsandboxed_run
    tc = {"id": "c", "name": "shell", "arguments": {"command": "ls"}}
    res = ToolResult(content="x", meta={"sandbox_denial": DENIAL})
    interceptor = MagicMock()
    assert maybe_raise_unsandboxed_run(
        interceptor, tc, res, lambda: [], queue_state="running") is None
    interceptor.on_sandbox_denial.assert_not_called()
    assert maybe_raise_unsandboxed_run(object(), tc, res, lambda: []) is None
    assert maybe_raise_unsandboxed_run(None, tc, res, lambda: []) is None


def test_helper_returns_the_pending_id_and_swallows_hook_errors():
    from agent_os.agent.loop import maybe_raise_unsandboxed_run
    tc = {"id": "c", "name": "shell", "arguments": {"command": "ls"}}
    res = ToolResult(content="x", meta={"sandbox_denial": DENIAL})
    interceptor = MagicMock()
    interceptor.on_sandbox_denial.return_value = "unsandboxed_abc"
    assert maybe_raise_unsandboxed_run(
        interceptor, tc, res, lambda: [{"role": "user"}]) == "unsandboxed_abc"
    interceptor.on_sandbox_denial.assert_called_once_with(tc, DENIAL, [{"role": "user"}])
    interceptor.on_sandbox_denial.side_effect = RuntimeError("boom")
    assert maybe_raise_unsandboxed_run(interceptor, tc, res, lambda: []) is None
