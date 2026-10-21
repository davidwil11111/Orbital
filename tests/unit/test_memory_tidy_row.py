# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 124 §3.7 follow-up (2026-10-21): the Memory session is hidden.

It is never listed, never broadcasts (so the project never looks busy and
the composer stays enabled), and every tidy pass leaves exactly ONE row —
``_kind: "memory_tidy"`` — in the session that triggered it. That row is
bookkeeping like the record turn's rows: out of the prompt window, the
recap, the reply badge and the live activity feed; the chat renders it as a
collapsed capsule.
"""

from __future__ import annotations

import asyncio
import logging
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_os.agent import memory_editor as E
from agent_os.agent import workspace_files as WF
from agent_os.agent.context import MEMORY_RECORD_KIND, MEMORY_TIDY_KIND
from agent_os.agent.session import Session
from agent_os.agent.workspace_files import WorkspaceFileManager, run_session_end_routine
from agent_os.daemon_v2 import session_index as si
from agent_os.daemon_v2.agent_manager import AgentManager, ProjectHandle, make_session_key
from agent_os.daemon_v2.recap import build_recap

TODAY = "2026-10-21"


def _bullet(text, eid, created="2026-09-01"):
    return f"- {text}\n  <!--mem id:{eid} created:{created} touched:{created}-->"


STATE = "## Now\n" + "\n".join([
    _bullet("GOAI booth prep — posters printed", "goai01", "2026-09-14"),
    _bullet("Cold-DM automation paused since 08-15", "cdm001", "2026-08-10"),
]) + "\n"
DECISIONS = "# DECISIONS\n\n" + _bullet("TabTin → watchlist", "tabtin") + "\n  **Chose:** A.\n\n"
LESSONS = "# LESSONS\n\n" + _bullet("Curl beats the browser for GitHub.", "curl") + "\n"
INDEX = "# INDEX\n\n- agent_output/ — reports\n"


@pytest.fixture
def wf(tmp_path):
    w = WorkspaceFileManager(str(tmp_path))
    w.write("state", STATE)
    w.write("decisions", DECISIONS)
    w.write("lessons", LESSONS)
    w.write("index", INDEX)
    return w


@pytest.fixture(autouse=True)
def _clear_single_flight():
    E._RUNNING.clear()
    WF._completed_session_ends.clear()
    yield
    E._RUNNING.clear()


def _turn(**fields) -> E.TurnResult:
    base = dict(mode="snapshot", llm_calls=1, tool_calls=0, input_tokens=100,
                cached_tokens=90, output_tokens=10, ended="text")
    base.update(fields)
    return E.TurnResult(**base)


def _manager(tmp_path):
    projects = {"p1": {"project_id": "p1", "name": "demo", "workspace": str(tmp_path),
                       "is_scratch": False}}
    project_store = MagicMock()
    project_store.get_project = MagicMock(side_effect=lambda pid: projects.get(pid))
    project_store.list_projects = MagicMock(return_value=list(projects.values()))
    ws = MagicMock()
    ws.broadcast = MagicMock()
    sub = MagicMock()
    sub.list_active = MagicMock(return_value=[])
    sub.stop = AsyncMock()
    sub.stop_all = AsyncMock()
    pm = MagicMock()
    pm.set_session = MagicMock()
    return AgentManager(project_store=project_store, ws_manager=ws, sub_agent_manager=sub,
                        activity_translator=MagicMock(), process_manager=pm)


def _handle(session, *, running):
    task = MagicMock()
    task.done = MagicMock(return_value=not running)
    return ProjectHandle(session=session, loop=MagicMock(), provider=MagicMock(), registry=MagicMock(),
                         context_manager=MagicMock(), interceptor=MagicMock(), task=task)


def _result(**fields) -> E.EditorResult:
    base = dict(ok=True, outcome="edited", calls=2, turn=_turn(llm_calls=2, tool_calls=3),
                written=["state", "index"], restored=[], archived=["cdm001"])
    base.update(fields)
    return E.EditorResult(**base)


# ---------------------------------------------------------------------------
# 1. never listed
# ---------------------------------------------------------------------------

def test_memory_session_is_hidden_from_the_list_but_the_daemon_still_finds_it(tmp_path):
    mgr = _manager(tmp_path)
    chat = Session.new("demo_chat00001", str(tmp_path))
    chat.append({"role": "user", "content": "hello"})
    mgr._handles[make_session_key("p1", chat.session_uuid)] = _handle(chat, running=False)
    mem = mgr.memory_session("p1")
    mem.append({"role": "user", "content": "Tidy this project's memory.", "source": "daemon"})
    mgr._handles[make_session_key("p1", mem.session_uuid)] = _handle(mem, running=True)

    listed = mgr.list_sessions("p1")
    assert [s["session_uuid"] for s in listed] == [chat.session_uuid]
    assert all(s.get("origin") != "memory" for s in listed)
    # the daemon's own lookup still sees it, live …
    assert mgr.memory_session("p1").session_uuid == mem.session_uuid
    full = {s["session_uuid"]: s for s in mgr.list_sessions("p1", include_memory=True)}
    assert full[mem.session_uuid]["origin"] == "memory" and full[mem.session_uuid]["status"] == "running"
    # … and from disk once the handle is gone (the JSONL stays)
    del mgr._handles[make_session_key("p1", mem.session_uuid)]
    assert mgr.memory_session("p1").session_uuid == mem.session_uuid
    assert all(s.get("origin") != "memory" for s in mgr.list_sessions("p1"))


# ---------------------------------------------------------------------------
# 2. never broadcasts
# ---------------------------------------------------------------------------

def test_every_broadcast_for_a_memory_session_is_dropped(tmp_path):
    mgr = _manager(tmp_path)
    mem = Session.new("demo_mem00001", str(tmp_path), origin="memory")
    mgr._register_memory_session("p1", mem.session_uuid)
    for status in ("running", "idle", "error"):
        mgr._broadcast("p1", {"type": "agent.status", "status": status}, session_id=mem.session_uuid)
    mgr._broadcast("p1", {"type": "agent.activity", "category": "tool_use"}, session_id=mem.session_uuid)
    assert mgr._ws.broadcast.call_count == 0
    # other sessions, and project-level payloads, are untouched
    mgr._broadcast("p1", {"type": "agent.status", "status": "running"}, session_id="demo_chat00001")
    mgr._broadcast("p1", {"type": "state_refresh.lifecycle", "status": "done"}, session_id="demo_chat00001")
    assert mgr._ws.broadcast.call_count == 2


@pytest.mark.asyncio
async def test_start_agent_registers_a_memory_session_so_its_running_status_never_leaves(tmp_path):
    from unittest.mock import patch
    from agent_os.daemon_v2.models import AgentConfig
    mgr = _manager(tmp_path)
    mem = Session.new("demo_mem00001", str(tmp_path), origin="memory")
    config = AgentConfig(workspace=str(tmp_path), model="gpt-4", api_key="sk-test")
    with patch("agent_os.daemon_v2.agent_manager.LLMProvider"), \
         patch("agent_os.daemon_v2.agent_manager.ToolRegistry") as MockReg, \
         patch("agent_os.daemon_v2.agent_manager.PromptBuilder"), \
         patch("agent_os.daemon_v2.agent_manager.ContextManager"), \
         patch("agent_os.daemon_v2.agent_manager.AgentLoop") as MockLoop, \
         patch("agent_os.daemon_v2.agent_manager.AutonomyInterceptor"):
        reg = MagicMock()
        reg.tool_names.return_value = ["read", "write", "edit"]
        MockReg.return_value = reg
        loop = MagicMock()
        loop.run = AsyncMock()
        MockLoop.return_value = loop
        await mgr.start_agent("p1", config, session=mem)
        await asyncio.sleep(0)
    statuses = [c.args[1] for c in mgr._ws.broadcast.call_args_list
                if c.args[1].get("type") == "agent.status"]
    assert statuses == []
    assert make_session_key("p1", mem.session_uuid) in mgr._memory_session_keys


# ---------------------------------------------------------------------------
# 3. one row per pass, in the triggering session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pass_counts_the_ids_that_left_the_live_files(wf):
    async def run_turn(task_text):
        block = _bullet("Cold-DM automation paused since 08-15", "cdm001", "2026-08-10")
        wf.write("state_archive", (wf.read("state_archive") or "# A\n") + f"\n## [archived {TODAY}]\n\n{block}\n")
        wf.write("state", wf.read("state").replace(block + "\n", ""))
        return _turn(tool_calls=3)
    result = await E.run_pass(wf, run_turn, today=TODAY, project_id="p1", over_keys=["state"])
    assert result.outcome == "edited" and result.archived == ["cdm001"] and result.restored == []


@pytest.mark.asyncio
async def test_routine_hands_the_pass_result_to_on_pass_done(wf):
    big = "\n".join(_bullet(f"recent item {i} " + "r" * 400, f"big{i:03d}", "2026-09-18") for i in range(25))
    wf.write("state", STATE + big + "\n")
    seen = []

    async def run_turn(task_text):
        return _turn()
    out = await run_session_end_routine(None, None, wf, session_uuid="s", bypass_idempotency=True,
                                        run_turn=run_turn, trigger="over_budget",
                                        on_pass_done=seen.append)
    assert out == "no_change" and len(seen) == 1
    assert isinstance(seen[0], E.EditorResult) and seen[0].outcome == "no_change"
    # the gates before the pass hand nothing over
    assert await run_session_end_routine(None, None, wf, session_uuid="s", bypass_idempotency=True,
                                         run_turn=run_turn, on_pass_done=seen.append) == "no_delta"
    assert len(seen) == 1


def test_tidy_row_lands_once_in_the_triggering_session_with_the_counts(tmp_path):
    mgr = _manager(tmp_path)
    chat = Session.new("demo_chat00001", str(tmp_path))
    chat.append({"role": "user", "content": "hello"})
    mgr._append_memory_tidy_row(chat, trigger="over_budget", result=_result(restored=["tabtin"]))
    rows = chat.get_messages()
    row = rows[-1]
    assert row["role"] == "system" and row["source"] == "daemon" and row["_kind"] == MEMORY_TIDY_KIND
    assert row["content"] == "Memory tidied: 1 archived, 1 restored"
    meta = row["_meta"]
    assert meta["event"] == "memory_tidy" and meta["trigger"] == "over_budget"
    assert meta["outcome"] == "edited" and meta["archived"] == 1 and meta["restored"] == 1
    assert meta["files"] == ["orbital/PROJECT_STATE.md", "orbital/INDEX.md"]
    assert meta["llm_calls"] == 2 and meta["ended"] == "text"
    assert sum(1 for r in rows if r.get("_kind") == MEMORY_TIDY_KIND) == 1
    # a pass that ran and changed nothing says so; a pass that never ran leaves no row
    mgr._append_memory_tidy_row(chat, trigger="over_budget",
                                result=_result(outcome="no_change", written=[], archived=[]))
    assert chat.get_messages()[-1]["content"] == "Memory tidied: nothing to change"
    before = len(chat.get_messages())
    mgr._append_memory_tidy_row(chat, trigger="over_budget",
                                result=E.EditorResult(ok=True, outcome="skipped"))
    mgr._append_memory_tidy_row(chat, trigger="over_budget",
                                result=E.EditorResult(ok=False, outcome="failed", error="turn timed out"))
    assert len(chat.get_messages()) == before + 1
    assert chat.get_messages()[-1]["content"] == "Memory tidy failed: turn timed out"


def test_tidy_row_never_lands_in_a_memory_session(tmp_path):
    mgr = _manager(tmp_path)
    mem = Session.new("demo_mem00001", str(tmp_path), origin="memory")
    mgr._append_memory_tidy_row(mem, trigger="over_budget", result=_result())
    assert mem.get_messages() == []


# ---------------------------------------------------------------------------
# 4. bookkeeping everywhere the record turn's rows are
# ---------------------------------------------------------------------------

def _tidy_row():
    return {"role": "system", "source": "daemon", "content": "Memory tidied: 1 archived, 0 restored",
            "_meta": {"event": "memory_tidy", "trigger": "over_budget", "outcome": "edited",
                      "archived": 1, "restored": 0, "files": ["orbital/PROJECT_STATE.md"]},
            "_kind": MEMORY_TIDY_KIND}


def _assistant(content, **extra):
    return {"role": "assistant", "content": content, "source": "management", **extra}


def _user(content, **extra):
    return {"role": "user", "content": content, "source": "user", **extra}


def test_tidy_row_is_not_a_reply_and_not_in_the_recap():
    assert si.is_reply_row(_tidy_row()) is False
    block = build_recap([_user("plan it"), _assistant("Here is the plan."), _tidy_row(),
                         _user("now the copy")], "claude-code", fresh=True)
    assert "Memory tidied" not in block and "assistant: Here is the plan." in block


def test_tidy_row_never_reaches_the_live_activity_feed(tmp_path):
    mgr = _manager(tmp_path)
    cb = mgr._on_message("proj", "sess")
    cb(_assistant("Here is the plan."))
    cb(_tidy_row())
    calls = mgr._activity_translator.on_message.call_args_list
    assert len(calls) == 1 and calls[0].args[0]["content"] == "Here is the plan."


def test_tidy_rows_leave_the_prompt_window_with_the_record_rows(tmp_path):
    s = Session.new("demo_chat00001", str(tmp_path))
    s.append(_user("plan it"))
    s.append(_assistant("Here is the plan."))
    s.append({"role": "system", "source": "daemon", "content": "[MEMORY] ask",
              "_meta": {"event": "memory_record"}, "_kind": MEMORY_RECORD_KIND})
    s.append(_tidy_row())
    s.append(_user("now the copy"))
    kept = s.get_recent(100_000, exclude_kind={MEMORY_RECORD_KIND, MEMORY_TIDY_KIND})
    assert [m.get("content") for m in kept] == ["plan it", "Here is the plan.", "now the copy"]
    # the single-kind form keeps working
    kept = s.get_recent(100_000, exclude_kind=MEMORY_RECORD_KIND)
    assert any(m.get("_kind") == MEMORY_TIDY_KIND for m in kept)


def test_context_manager_excludes_both_kinds(tmp_path):
    from agent_os.agent.context import ContextManager
    from agent_os.agent.prompt_builder import Autonomy, PromptContext

    class _PromptBuilder:
        def build(self, context):
            return ("cached-system-prefix", "semi-stable-suffix", "dynamic-runtime")

    ctx = PromptContext(
        workspace=str(tmp_path), model="test-model", autonomy=Autonomy.HANDS_OFF,
        enabled_agents=[], tool_names=["read", "edit", "write"], os_type="linux",
        datetime_now="2026-01-01T00:00:00", context_usage_pct=0.0,
    )
    s = Session.new("demo_chat00001", str(tmp_path))
    s.append(_user("plan it"))
    s.append(_assistant("Here is the plan."))
    s.append({"role": "system", "source": "daemon", "content": "[MEMORY] ask",
              "_meta": {"event": "memory_record"}, "_kind": MEMORY_RECORD_KIND})
    s.append(_tidy_row())
    s.append(_user("now the copy"))
    cm = ContextManager(s, _PromptBuilder(), ctx, model_context_limit=200_000)
    cm.begin_run()
    contents = [m.get("content") for m in cm.prepare()]
    assert not any(isinstance(c, str) and ("Memory tidied" in c or "[MEMORY] ask" in c) for c in contents)
    assert any(isinstance(c, str) and "now the copy" in c for c in contents)


def test_a_memory_session_gets_silent_observers(tmp_path):
    """Live chat events (stream deltas, activity, user-row echoes) for the
    hidden session never reach a client either — the translator is bypassed
    by the observers themselves, not just by the status broadcast."""
    mgr = _manager(tmp_path)
    mem = Session.new("demo_mem00001", str(tmp_path), origin="memory")
    mgr._wire_session_observers(mem, "p1", mem.session_uuid)
    assert make_session_key("p1", mem.session_uuid) in mgr._memory_session_keys
    mem.append({"role": "user", "content": "Tidy this project's memory.", "source": "daemon"})
    mem.on_stream(MagicMock())
    mem.on_queue_drained(["n1"])
    assert mgr._activity_translator.on_message.call_count == 0
    assert mgr._activity_translator.on_stream_chunk.call_count == 0
    assert mgr._ws.broadcast.call_count == 0
    # a chat session keeps its live observers
    chat = Session.new("demo_chat00001", str(tmp_path))
    mgr._wire_session_observers(chat, "p1", chat.session_uuid)
    chat.append({"role": "user", "content": "hello", "source": "user"})
    assert mgr._activity_translator.on_message.call_count == 1
