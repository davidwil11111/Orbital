# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 117 §3.3 — ask-on-denial.

A safe-mode shell result carrying ``meta["sandbox_denial"]`` raises exactly
one pending approval of kind ``unsandboxed_run`` (the loop pauses on it the
way it pauses on an intercepted call); under HANDS_OFF the ask auto-denies
for the run after the TOFU timeout and is parked in
``pending_unsandboxed_runs``. The three answers: ``once`` runs exactly that
command through the open path and injects a terminal event; ``always``
appends the executable first; ``disable_safe_mode`` flips the project and
tears the network down first. A denial through ``/deny`` writes a system
row, never an orphan tool result. An open project never raises the pending
(the open shell path never emits the meta — pinned in test_safe_mode_shell).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_os.agent.context import ContextManager
from agent_os.agent.loop import AgentLoop
from agent_os.agent.prompt_builder import Autonomy, PromptContext
from agent_os.agent.providers.types import StreamChunk, TokenUsage
from agent_os.agent.session import Session, persist_user_row
from agent_os.agent.tools.base import ToolResult
from agent_os.daemon_v2.agent_manager import AgentManager, ProjectHandle
from agent_os.daemon_v2.autonomy import UNSANDBOXED_RUN_KIND, AutonomyInterceptor
from agent_os.daemon_v2.models import make_session_key

PROJECT = "proj_sm"
SID = "sess-safe-mode-0001"
DENIAL = {"command": "ls ~/.ssh", "reason": "file-read-data /Users/me/.ssh"}


# ---------------------------------------------------------------------------
# Interceptor
# ---------------------------------------------------------------------------


def test_on_sandbox_denial_registers_one_pending_and_broadcasts_the_card():
    ws = MagicMock()
    interceptor = AutonomyInterceptor(Autonomy.CHECK_IN, ws, PROJECT, session_id=SID)
    tc = {"id": "call_1", "name": "shell", "arguments": {"command": "ls ~/.ssh"}}
    pending_id = interceptor.on_sandbox_denial(tc, DENIAL, [])
    assert pending_id and pending_id != "call_1"
    pending = interceptor.get_pending(pending_id)
    assert pending["kind"] == UNSANDBOXED_RUN_KIND == "unsandboxed_run"
    assert pending["command"] == "ls ~/.ssh"
    assert pending["reason"] == DENIAL["reason"]
    assert pending["tool_name"] == "shell"
    assert pending["tool_call_id"] == pending_id
    assert pending["session_id"] == SID
    payload = ws.broadcast.call_args[0][1]
    assert payload["type"] == "approval.request"
    assert payload["kind"] == "unsandboxed_run"
    assert payload["command"] == "ls ~/.ssh"
    assert payload["reason"] == DENIAL["reason"]
    assert payload["tool_call_id"] == pending_id
    assert payload["session_id"] == SID
    assert len(interceptor._pending_approvals) == 1


# ---------------------------------------------------------------------------
# Real loop: the pause
# ---------------------------------------------------------------------------


_USAGE = TokenUsage(input_tokens=10, output_tokens=5)


def _reply(text):
    return [StreamChunk(text=text),
            StreamChunk(is_final=True, usage=_USAGE, finish_reason="stop")]


def _tool_calls(*names):
    chunks = []
    for i, name in enumerate(names):
        chunks.append(StreamChunk(tool_calls_delta=[{
            "index": i, "id": f"call_{i}", "type": "function",
            "function": {"name": name, "arguments": "{}"},
        }]))
    chunks.append(StreamChunk(is_final=True, usage=_USAGE, finish_reason="tool_calls"))
    return chunks


class _ScriptedLLM:
    provider = "test"
    model = "test-model"
    sdk = "openai"

    def __init__(self, script):
        self._script = script
        self.calls = 0

    async def stream(self, messages, tools=None):
        idx = self.calls
        self.calls += 1
        for chunk in self._script[min(idx, len(self._script) - 1)]:
            yield chunk


class _Registry:
    """``shell`` returns a safe-mode denial; ``other`` is a plain tool."""

    def __init__(self, denial):
        self._denial = denial
        self.executed: list[str] = []

    def schemas(self):
        return [{"type": "function", "function": {
            "name": n, "description": n, "parameters": {"type": "object", "properties": {}},
        }} for n in ("shell", "other")]

    def tool_names(self):
        return ["shell", "other"]

    def is_async(self, name):
        return False

    def execute(self, name, arguments):
        self.executed.append(name)
        if name == "shell":
            return ToolResult(content="Exit code: 1\n[sandbox] blocked",
                              meta={"network": False, "sandbox_denial": self._denial})
        return ToolResult(content="other done")

    def reset_run_state(self):
        pass


class _PromptBuilder:
    def build(self, context):
        return ("p", "s", "d")


def _ctx(ws):
    return PromptContext(workspace=ws, model="m", autonomy=Autonomy.CHECK_IN,
                         enabled_agents=[], tool_names=[], os_type="linux",
                         datetime_now="2026-01-01T00:00:00", context_usage_pct=0.0)


@pytest.mark.asyncio
async def test_loop_pauses_on_a_denial_and_cancels_the_rest_of_the_batch(tmp_path):
    session = Session.new(SID, str(tmp_path))
    registry = _Registry(DENIAL)
    ws = MagicMock()
    interceptor = AutonomyInterceptor(Autonomy.HANDS_OFF, ws, PROJECT, session_id=SID)
    ctx = ContextManager(session, _PromptBuilder(), _ctx(str(tmp_path)))
    loop = AgentLoop(session, _ScriptedLLM([_tool_calls("shell", "other"), _reply("done")]),
                     registry, ctx, project_dir=str(tmp_path), max_iterations=5,
                     interceptor=interceptor)
    persist_user_row(session, "hello")
    await loop.run()
    assert registry.executed == ["shell"]           # the sibling never ran
    assert session._paused_for_approval is True
    assert session.is_paused()
    assert len(interceptor._pending_approvals) == 1
    pending = next(iter(interceptor._pending_approvals.values()))
    assert pending["kind"] == "unsandboxed_run"
    rows = session.get_messages()
    tool_rows = [m for m in rows if m.get("role") == "tool"]
    assert {m["tool_call_id"] for m in tool_rows} == {"call_0", "call_1"}
    assert "CANCELLED" in next(m["content"] for m in tool_rows if m["tool_call_id"] == "call_1")


@pytest.mark.asyncio
async def test_loop_without_an_interceptor_ignores_the_meta(tmp_path):
    session = Session.new(SID, str(tmp_path))
    registry = _Registry(DENIAL)
    ctx = ContextManager(session, _PromptBuilder(), _ctx(str(tmp_path)))
    loop = AgentLoop(session, _ScriptedLLM([_tool_calls("shell"), _reply("done")]),
                     registry, ctx, project_dir=str(tmp_path), max_iterations=5)
    persist_user_row(session, "hello")
    await loop.run()
    assert session._paused_for_approval is False
    assert any(m.get("content") == "done" for m in session.get_messages())


# ---------------------------------------------------------------------------
# Manager: approve / deny / timeout / parked
# ---------------------------------------------------------------------------


def _manager(project):
    store = MagicMock()
    store.get_project.side_effect = lambda pid: project if pid == PROJECT else None

    def _update(pid, updates):
        project.update(updates)
    store.update_project.side_effect = _update
    ws = MagicMock()
    mgr = AgentManager(
        project_store=store, ws_manager=ws, sub_agent_manager=MagicMock(),
        activity_translator=MagicMock(), process_manager=MagicMock(),
        platform_provider=MagicMock(),
    )
    mgr._platform_provider.clear_network = AsyncMock()
    mgr._start_loop = AsyncMock()
    return mgr, ws


class _ShellSpy:
    """Stands in for ``AgentManager._run_unsandboxed_command`` — records the
    exact command the open path was asked to run."""

    def __init__(self):
        self.commands: list[str] = []

    async def __call__(self, project_id, command):
        self.commands.append(command)
        return f"Exit code: 0\nran {command}"


def _plant(mgr, tmp_path, project, *, preset=Autonomy.CHECK_IN):
    session = Session.new(SID, str(tmp_path))
    persist_user_row(session, "hello")
    session.append({"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_0", "type": "function",
         "function": {"name": "shell", "arguments": '{"command": "ls ~/.ssh"}'}}]})
    session.append_tool_result("call_0", "Exit code: 1\n[sandbox] blocked")
    interceptor = AutonomyInterceptor(preset, mgr._ws, PROJECT, session_id=SID)
    pending_id = interceptor.on_sandbox_denial(
        {"id": "call_0", "name": "shell", "arguments": {"command": "ls ~/.ssh"}}, DENIAL, [])
    session._paused_for_approval = True
    session.pause()
    shell = _ShellSpy()
    mgr._run_unsandboxed_command = shell
    handle = ProjectHandle(session=session, loop=MagicMock(), provider=MagicMock(),
                           registry=MagicMock(), context_manager=MagicMock(),
                           interceptor=interceptor, task=None,
                           config_snapshot={"workspace": project["workspace"]})
    mgr._handles[make_session_key(PROJECT, SID)] = handle
    return handle, shell, pending_id


def _project(tmp_path, **over):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    base = {"project_id": PROJECT, "name": "P", "workspace": str(ws), "safe_mode": True,
            "unsandboxed_executables": [], "pending_unsandboxed_runs": [],
            "autonomy": "check_in"}
    base.update(over)
    return base


def _terminal_rows(session):
    return [m for m in session.get_messages()
            if (m.get("_meta") or {}).get("event") == "unsandboxed_run"]


@pytest.mark.asyncio
async def test_once_runs_exactly_that_command_and_injects_a_terminal_event(tmp_path):
    project = _project(tmp_path)
    mgr, ws = _manager(project)
    handle, shell, pending_id = _plant(mgr, tmp_path, project)
    await mgr.approve(PROJECT, pending_id, session_id=SID, response_payload="once")
    assert shell.commands == ["ls ~/.ssh"]
    rows = _terminal_rows(handle.session)
    assert len(rows) == 1
    assert rows[0]["role"] == "system" and rows[0]["source"] == "daemon"
    assert "ran ls ~/.ssh" in rows[0]["content"]
    assert rows[0]["_meta"]["decision"] == "once"
    assert rows[0]["_meta"]["command"] == "ls ~/.ssh"
    # No orphan tool row for the synthetic pending id.
    assert not any(m.get("tool_call_id") == pending_id for m in handle.session.get_messages())
    assert project["unsandboxed_executables"] == []
    assert project["safe_mode"] is True
    assert handle.interceptor.get_pending(pending_id) is None
    assert handle.session._paused_for_approval is False
    mgr._start_loop.assert_awaited_once_with(PROJECT, session_id=SID)


@pytest.mark.asyncio
async def test_always_appends_the_executable_then_runs(tmp_path):
    project = _project(tmp_path)
    mgr, _ = _manager(project)
    handle, shell, pending_id = _plant(mgr, tmp_path, project)
    await mgr.approve(PROJECT, pending_id, session_id=SID, response_payload="always")
    assert project["unsandboxed_executables"] == ["ls"]
    assert shell.commands == ["ls ~/.ssh"]
    assert _terminal_rows(handle.session)[0]["_meta"]["decision"] == "always"


@pytest.mark.asyncio
async def test_disable_safe_mode_flips_the_project_tears_down_network_then_runs(tmp_path):
    project = _project(tmp_path, approved_domains=["x.com"])
    mgr, _ = _manager(project)
    handle, shell, pending_id = _plant(mgr, tmp_path, project)
    await mgr.approve(PROJECT, pending_id, session_id=SID,
                      response_payload="disable_safe_mode")
    assert project["safe_mode"] is False
    assert project["approved_domains"] == ["x.com"]     # kept on disk untouched
    mgr._platform_provider.clear_network.assert_awaited_once_with(PROJECT)
    assert shell.commands == ["ls ~/.ssh"]


@pytest.mark.asyncio
async def test_approve_without_a_payload_means_once(tmp_path):
    project = _project(tmp_path)
    mgr, _ = _manager(project)
    handle, shell, pending_id = _plant(mgr, tmp_path, project)
    await mgr.approve(PROJECT, pending_id, session_id=SID)
    assert shell.commands == ["ls ~/.ssh"]
    assert project["unsandboxed_executables"] == []


@pytest.mark.asyncio
async def test_deny_writes_a_system_row_not_an_orphan_tool_result(tmp_path):
    project = _project(tmp_path)
    mgr, _ = _manager(project)
    handle, shell, pending_id = _plant(mgr, tmp_path, project)
    await mgr.deny(PROJECT, pending_id, "no", session_id=SID)
    assert shell.commands == []
    assert not any(m.get("tool_call_id") == pending_id for m in handle.session.get_messages())
    denied = [m for m in handle.session.get_messages()
              if m.get("role") == "system" and "DENIED" in (m.get("content") or "")]
    assert len(denied) == 1 and "ls ~/.ssh" in denied[0]["content"]
    assert handle.interceptor.get_pending(pending_id) is None
    mgr._start_loop.assert_awaited_once()


@pytest.mark.asyncio
async def test_hands_off_auto_denies_for_the_run_and_parks_the_request(tmp_path):
    project = _project(tmp_path, autonomy="hands_off")
    mgr, ws = _manager(project)
    handle, shell, pending_id = _plant(mgr, tmp_path, project, preset=Autonomy.HANDS_OFF)
    task = MagicMock()
    task.exception.return_value = None
    with patch("agent_os.daemon_v2.autonomy.NETWORK_APPROVAL_TIMEOUT_S", 0.05):
        mgr._on_loop_done(PROJECT, session_id=SID)(task)
        assert pending_id in mgr._network_approval_timers
        await asyncio.sleep(0.3)
    assert shell.commands == []
    parked = project["pending_unsandboxed_runs"]
    assert len(parked) == 1
    assert parked[0]["command"] == "ls ~/.ssh"
    assert parked[0]["reason"] == DENIAL["reason"]
    assert parked[0]["session_id"] == SID
    assert parked[0]["id"] and parked[0]["created_at"]
    assert handle.interceptor.get_pending(pending_id) is None
    rows = [m for m in handle.session.get_messages()
            if m.get("role") == "system" and "Denied for this run" in (m.get("content") or "")]
    assert len(rows) == 1 and "Safe mode" in rows[0]["content"]
    assert not any(m.get("tool_call_id") == pending_id for m in handle.session.get_messages())
    resolved = [c[0][1] for c in ws.broadcast.call_args_list
                if c[0][1].get("type") == "approval.resolved"]
    assert resolved and resolved[-1]["auto_denied"] is True
    assert handle.session._paused_for_approval is False
    mgr._start_loop.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_in_gets_no_timer(tmp_path):
    project = _project(tmp_path)
    mgr, _ = _manager(project)
    handle, _, pending_id = _plant(mgr, tmp_path, project, preset=Autonomy.CHECK_IN)
    task = MagicMock()
    task.exception.return_value = None
    mgr._on_loop_done(PROJECT, session_id=SID)(task)
    assert pending_id not in mgr._network_approval_timers


@pytest.mark.asyncio
async def test_resolve_parked_once_runs_and_injects_into_the_stored_session(tmp_path):
    project = _project(tmp_path, pending_unsandboxed_runs=[{
        "id": "ur_1", "command": "echo parked-run", "reason": "r",
        "created_at": "2026-10-18T00:00:00+00:00", "session_id": SID,
    }])
    mgr, _ = _manager(project)
    mgr.inject_system_message = AsyncMock(return_value="delivered")
    result = await mgr.resolve_parked_unsandboxed_run(PROJECT, "ur_1", "once")
    assert result["pending_unsandboxed_runs"] == []
    mgr.inject_system_message.assert_awaited_once()
    args, kwargs = mgr.inject_system_message.call_args
    assert args[0] == PROJECT
    assert "parked-run" in args[1]
    assert kwargs["session_id"] == SID
    assert kwargs["meta"]["event"] == "unsandboxed_run"
    assert kwargs["meta"]["decision"] == "once"


@pytest.mark.asyncio
async def test_resolve_parked_always_records_the_executable(tmp_path):
    project = _project(tmp_path, pending_unsandboxed_runs=[{
        "id": "ur_1", "command": "git push origin main", "reason": "r",
        "created_at": "2026-10-18T00:00:00+00:00", "session_id": SID,
    }])
    mgr, _ = _manager(project)
    mgr.inject_system_message = AsyncMock(return_value="delivered")
    mgr._run_unsandboxed_command = AsyncMock(return_value="Exit code: 0\nok")
    await mgr.resolve_parked_unsandboxed_run(PROJECT, "ur_1", "always")
    assert project["unsandboxed_executables"] == ["git"]
    assert project["pending_unsandboxed_runs"] == []


@pytest.mark.asyncio
async def test_resolve_parked_disable_safe_mode_flips_and_tears_down(tmp_path):
    project = _project(tmp_path, pending_unsandboxed_runs=[{
        "id": "ur_1", "command": "echo x", "reason": "r",
        "created_at": "2026-10-18T00:00:00+00:00", "session_id": None,
    }])
    mgr, _ = _manager(project)
    mgr.inject_system_message = AsyncMock(return_value="delivered")
    await mgr.resolve_parked_unsandboxed_run(PROJECT, "ur_1", "disable_safe_mode")
    assert project["safe_mode"] is False
    mgr._platform_provider.clear_network.assert_awaited_once_with(PROJECT)
    assert project["pending_unsandboxed_runs"] == []


@pytest.mark.asyncio
async def test_resolve_parked_deny_only_drops_it(tmp_path):
    project = _project(tmp_path, pending_unsandboxed_runs=[{
        "id": "ur_1", "command": "echo x", "reason": "r",
        "created_at": "2026-10-18T00:00:00+00:00", "session_id": None,
    }])
    mgr, _ = _manager(project)
    mgr.inject_system_message = AsyncMock()
    await mgr.resolve_parked_unsandboxed_run(PROJECT, "ur_1", "deny")
    mgr.inject_system_message.assert_not_awaited()
    assert project["pending_unsandboxed_runs"] == []


@pytest.mark.asyncio
async def test_resolve_parked_unknown_id_raises_key_error(tmp_path):
    project = _project(tmp_path)
    mgr, _ = _manager(project)
    with pytest.raises(KeyError):
        await mgr.resolve_parked_unsandboxed_run(PROJECT, "nope", "deny")
