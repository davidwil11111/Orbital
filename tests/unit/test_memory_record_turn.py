# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 112 A6: at every normal run end (and before a compaction) the daemon
appends one ``[MEMORY]`` row and lets the loop run a short, bounded record
turn.

Evidence (§1.7): of 362 substantive runs since October, 186 wrote no memory
file at all, 103 of them after changing workspace files. Nothing caught it —
the prompt only said "update proactively". The trigger is deterministic (the
run ending); whether anything is worth recording is the model's call, with
the current PROJECT_STATE text and the three append anchors in hand so it
needs no read first.

The reply to the user is fixed at the moment the run would have ended: every
row the record turn produces carries ``_kind: "memory_record"`` so the chat
renders it as a collapsed activity line (never a bubble), the recap / reply
badge / manager handoff ignore it, and a record turn that made no tool call
is silent.

These tests drive a real ``AgentLoop.run()`` with a scripted LLM and a tiny
tool registry over a real workspace, the same way
``test_wake_on_deferred_terminal.py`` does.
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_os.agent import loop as loop_mod
from agent_os.agent.context import ContextManager
from agent_os.agent.loop import (
    AgentLoop,
    MEMORY_RECORD_KIND,
    MEMORY_RECORD_MAX_LLM_CALLS,
    MEMORY_RECORD_MAX_TOOL_CALLS,
    build_memory_record_text,
    build_worker_memory_record_text,
    classify_written_path,
)
from agent_os.agent.prompt_builder import Autonomy, PromptContext
from agent_os.agent.providers.types import (
    LLMError, LLMResponse, StreamChunk, TokenUsage,
)
from agent_os.agent.session import Session, persist_user_row
from agent_os.agent.tools.base import ToolResult


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _PromptBuilder:
    def build(self, context):
        return ("cached-system-prefix", "semi-stable-suffix", "dynamic-runtime")


def _prompt_context(workspace: str, **overrides) -> PromptContext:
    fields = dict(
        workspace=workspace,
        model="test-model",
        autonomy=Autonomy.HANDS_OFF,
        enabled_agents=[],
        tool_names=["read", "edit", "write"],
        os_type="linux",
        datetime_now="2026-01-01T00:00:00",
        context_usage_pct=0.0,
    )
    fields.update(overrides)
    return PromptContext(**fields)


_USAGE = TokenUsage(input_tokens=10, output_tokens=5)


def _reply(text: str) -> list[StreamChunk]:
    return [StreamChunk(text=text),
            StreamChunk(is_final=True, usage=_USAGE, finish_reason="stop")]


def _tool_calls(*calls: tuple[str, str, dict]) -> list[StreamChunk]:
    """``(call_id, name, arguments)`` tuples → one streamed tool-call turn."""
    deltas = []
    for idx, (call_id, name, args) in enumerate(calls):
        deltas.append(StreamChunk(tool_calls_delta=[{
            "index": idx, "id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }]))
    deltas.append(StreamChunk(is_final=True, usage=_USAGE, finish_reason="tool_calls"))
    return deltas


class _ScriptedLLM:
    """Replays one scripted response per ``stream`` call (the last one
    repeats). ``complete`` (compaction summary / flush) answers with text."""

    provider = "test"
    model = "test-model"
    sdk = "openai"

    def __init__(self, script, *, gate_call=None, raise_on_call=None):
        self._script = script
        self._gate_call = gate_call
        self._raise_on_call = raise_on_call
        self.calls = 0
        self.sent: list[list[dict]] = []
        self.in_flight = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(self, messages, tools=None):
        idx = self.calls
        self.calls += 1
        self.sent.append(list(messages))
        if idx == self._gate_call:
            self.in_flight.set()
            await self.release.wait()
        if idx == self._raise_on_call:
            raise LLMError("invalid key", status_code=401)
        for chunk in self._script[min(idx, len(self._script) - 1)]:
            yield chunk

    async def complete(self, messages, tools=None):
        return LLMResponse(raw_message={"role": "assistant", "content": "<silent>"},
                           text="<silent>", tool_calls=[], usage=_USAGE)


class _Tools:
    """read / edit / write over the real workspace, plus whatever extra
    names a test registers (``yield_tool`` ends the turn like a dispatch)."""

    def __init__(self, workspace: str):
        self._ws = workspace
        self.executed: list[tuple[str, dict]] = []

    def schemas(self) -> list[dict]:
        return [{"type": "function", "function": {
            "name": n, "description": n,
            "parameters": {"type": "object", "properties": {}}}}
            for n in ("read", "edit", "write", "yield_tool", "mark_task_complete")]

    def tool_names(self) -> list[str]:
        return ["read", "edit", "write", "yield_tool", "mark_task_complete"]

    def is_async(self, name: str) -> bool:
        return False

    def reset_run_state(self) -> None:
        pass

    def execute(self, name: str, arguments: dict) -> ToolResult:
        self.executed.append((name, dict(arguments)))
        path = os.path.join(self._ws, arguments.get("path", "")) if arguments.get("path") else None
        if name == "write":
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(arguments.get("content", ""))
            return ToolResult(content=f"Wrote {arguments.get('path')}")
        if name == "edit":
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            old, new = arguments.get("old_text", ""), arguments.get("new_text", "")
            if old not in text:
                return ToolResult(content="Error: old_text not found", meta={"recoverable": True})
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text.replace(old, new, 1))
            return ToolResult(content=f"Edited {arguments.get('path')}")
        if name == "read":
            with open(path, "r", encoding="utf-8") as fh:
                return ToolResult(content=fh.read())
        if name == "yield_tool":
            return ToolResult(content="dispatched", meta={"yield_turn": True})
        if name == "mark_task_complete":
            return ToolResult(content="ok")
        return ToolResult(content=f"Error: unknown tool {name}")


def _seed_memory(workspace: str) -> None:
    orb = os.path.join(workspace, "orbital")
    os.makedirs(orb, exist_ok=True)
    files = {
        "PROJECT_STATE.md": "# Project State\n- current focus: launch page\n  <!--mem id:a1 created:2026-01-01 touched:2026-01-01-->\n- next: write copy\n",
        "DECISIONS.md": "# Decisions\n- chose Vite over CRA because it is faster\n",
        "LESSONS.md": "# Lessons\n- never trust a green local build\n",
        "INDEX.md": "# Index\n- docs/ — design notes\n",
    }
    for name, text in files.items():
        with open(os.path.join(orb, name), "w", encoding="utf-8") as fh:
            fh.write(text)


def _rig(tmp_path, llm, *, seed=True, origin="chat", enabled=True,
         interceptor=None, memory_dir=True):
    workspace = str(tmp_path)
    if seed and memory_dir:
        _seed_memory(workspace)
    session = Session.new("sess-rec-0001", workspace)
    session.origin = origin
    tools = _Tools(workspace)
    ctx = ContextManager(session, _PromptBuilder(), _prompt_context(workspace),
                         model_context_limit=200_000)
    loop = AgentLoop(session, llm, tools, ctx, interceptor=interceptor,
                     project_dir=workspace, max_iterations=20,
                     memory_record_enabled=enabled)
    persist_user_row(session, "Please do the thing.")
    return session, tools, loop


def _memory_rows(session) -> list[dict]:
    return [m for m in session.get_messages()
            if (m.get("_meta") or {}).get("event") == "memory_record"]


def _kinds(session) -> list[str | None]:
    return [m.get("_kind") for m in session.get_messages()]


def _edit_state_call(call_id="tc_rec_1"):
    return _tool_calls((call_id, "edit", {
        "path": "orbital/PROJECT_STATE.md",
        "old_text": "- next: write copy",
        "new_text": "- next: review the copy the user approved",
    }))


# ---------------------------------------------------------------------------
# Text builders and path classification
# ---------------------------------------------------------------------------


def test_record_text_carries_the_fixed_wording_file_list_and_anchors(tmp_path):
    _seed_memory(str(tmp_path))
    text = build_memory_record_text(
        str(tmp_path), ["content-bank/x.md", "agent_output/y.md"], ["codex (completed)"])
    assert text.startswith("[MEMORY] This run is ending. Decide whether anything durable happened in it.\n")
    assert "Files this run changed: content-bank/x.md, agent_output/y.md\n" in text
    assert "Sub-agent results received: codex (completed)\n" in text
    assert "If nothing durable happened, reply exactly: nothing to record" in text
    assert "do not reply to the user" in text
    assert "--- orbital/PROJECT_STATE.md as on disk now ---\n# Project State\n- current focus: launch page\n  <!--mem id:a1" in text
    assert "--- last line of orbital/DECISIONS.md ---\n- chose Vite over CRA because it is faster\n" in text
    assert "--- last line of orbital/LESSONS.md ---\n- never trust a green local build\n" in text
    assert "--- last line of orbital/INDEX.md ---\n- docs/ — design notes" in text


def test_record_text_omits_the_file_line_when_nothing_changed(tmp_path):
    _seed_memory(str(tmp_path))
    text = build_memory_record_text(str(tmp_path), [], [])
    assert "Files this run changed" not in text
    assert "Sub-agent results received: none\n" in text


def test_record_text_handles_missing_memory_files(tmp_path):
    text = build_memory_record_text(str(tmp_path), [], [])
    assert "--- orbital/PROJECT_STATE.md as on disk now ---\n(the file does not exist yet" in text
    assert "--- last line of orbital/DECISIONS.md ---\n(the file does not exist yet" in text


def test_worker_text_is_the_fixed_worker_variant():
    text = build_worker_memory_record_text(["src/api/routes.py", "tests/test_routes.py"])
    assert text.startswith("[Orbital memory] Your last turn is ending. Decide whether anything durable\nhappened in it. Files this turn changed: src/api/routes.py, tests/test_routes.py\n")
    assert text.endswith("If nothing durable happened, reply exactly: nothing to record")
    assert "then stop without further reply" in text
    assert "Files this turn changed: none" in build_worker_memory_record_text([])


@pytest.mark.parametrize("path,expected", [
    ("orbital/PROJECT_STATE.md", "memory"),
    ("./orbital/DECISIONS.md", "memory"),
    ("orbital/LESSONS.md", "memory"),
    ("orbital/INDEX.md", "memory"),
    ("orbital/ASKS.md", None),
    ("orbital/sessions/x.jsonl", None),
    ("src/app.py", "workspace"),
    ("docs/notes.md", "workspace"),
    ("", None),
])
def test_classify_written_path(tmp_path, path, expected):
    assert classify_written_path(str(tmp_path), path) == expected
    if expected == "memory":
        assert classify_written_path(str(tmp_path), os.path.join(str(tmp_path), path)) == "memory"


# ---------------------------------------------------------------------------
# Trigger: a normally ending run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_text_reply_end_appends_the_memory_row_and_the_loop_records(tmp_path):
    llm = _ScriptedLLM([
        _tool_calls(("tc1", "write", {"path": "docs/plan.md", "content": "plan"})),
        _reply("Here is the plan."),
        _edit_state_call(),
        _reply("Done, updated state."),
    ])
    session, tools, loop = _rig(tmp_path, llm)
    loop._context_manager.begin_run = MagicMock(wraps=loop._context_manager.begin_run)

    await loop.run()

    rows = session.get_messages()
    mem = _memory_rows(session)
    assert len(mem) == 1
    row = mem[0]
    assert row["role"] == "system" and row["source"] == "daemon"
    assert row["_kind"] == MEMORY_RECORD_KIND
    assert row["content"].startswith("[MEMORY] This run is ending.")
    assert "Files this run changed: docs/plan.md\n" in row["content"]
    assert "--- orbital/PROJECT_STATE.md as on disk now ---\n# Project State" in row["content"]
    # It sits right after the reply to the user, which stays untagged.
    reply_idx = next(i for i, m in enumerate(rows) if m.get("content") == "Here is the plan.")
    assert rows[reply_idx].get("_kind") is None
    assert rows.index(row) == reply_idx + 1
    # Everything after it is tagged: the edit, its result, the closing text.
    after = rows[reply_idx + 1:]
    assert all(m.get("_kind") == MEMORY_RECORD_KIND for m in after), [m.get("role") for m in after]
    assert [m["role"] for m in after] == ["system", "assistant", "tool", "assistant"]
    assert llm.calls == 4
    assert ("edit", {"path": "orbital/PROJECT_STATE.md",
                     "old_text": "- next: write copy",
                     "new_text": "- next: review the copy the user approved"}) in tools.executed
    with open(os.path.join(str(tmp_path), "orbital", "PROJECT_STATE.md")) as fh:
        assert "review the copy the user approved" in fh.read()
    # The loop reads what it needs and nothing else: the run began once.
    assert loop._context_manager.begin_run.call_count == 1
    assert loop.get_completion_state()[0] == "text"


@pytest.mark.asyncio
async def test_nothing_to_record_costs_exactly_one_extra_call(tmp_path):
    llm = _ScriptedLLM([_reply("thanks!"), _reply("nothing to record")])
    session, tools, loop = _rig(tmp_path, llm)
    await loop.run()
    assert llm.calls == 2
    assert len(_memory_rows(session)) == 1
    assert tools.executed == []
    closing = session.get_messages()[-1]
    assert closing["role"] == "assistant" and closing["content"] == "nothing to record"
    assert closing["_kind"] == MEMORY_RECORD_KIND


@pytest.mark.asyncio
async def test_record_turn_prompt_extends_the_previous_prefix_byte_for_byte(tmp_path):
    """7c: the record turn's build is the previous call's prompt plus the
    appended tail — the snapshot is not refreshed."""
    llm = _ScriptedLLM([
        _tool_calls(("tc_r", "read", {"path": "orbital/INDEX.md"})),  # the first call carries the one-off cold-resume row
        _reply("the reply"),
        _reply("nothing to record"),
    ])
    session, tools, loop = _rig(tmp_path, llm)
    await loop.run()
    before, during = llm.sent[1], llm.sent[2]
    # Everything up to the first prompt's final (runtime-carrying) row is identical.
    for i in range(len(before) - 1):
        assert json.dumps(before[i], sort_keys=True) == json.dumps(during[i], sort_keys=True), i
    # And the [MEMORY] row rides as a positional user turn, never role:system.
    memory_rows = [m for m in during if "[MEMORY] This run is ending" in str(m.get("content"))]
    assert len(memory_rows) == 1 and memory_rows[0]["role"] == "user"


@pytest.mark.asyncio
async def test_mark_task_complete_end_also_records_and_keeps_the_exit_reason(tmp_path):
    llm = _ScriptedLLM([
        _tool_calls(("tc_done", "mark_task_complete", {"summary": "shipped"})),
        _reply("Done, updated state."),
    ])
    session, tools, loop = _rig(tmp_path, llm)
    await loop.run()
    assert len(_memory_rows(session)) == 1
    assert llm.calls == 2
    reason, summary, _ = loop.get_completion_state()
    assert reason == "complete" and summary == "shipped"
    # The record turn's closing text is tagged, so the dispatcher's verdict
    # row ("Task completed: shipped") is still the last untagged row.
    untagged = [m for m in session.get_messages() if m.get("_kind") is None]
    assert untagged[-1]["content"] == "Task completed: shipped"


@pytest.mark.asyncio
async def test_sub_agent_results_received_lists_the_terminal_rows_of_the_run(tmp_path):
    llm = _ScriptedLLM([_reply("codex finished the migration."), _reply("nothing to record")])
    session, tools, loop = _rig(tmp_path, llm)
    # The wake run's trigger: a terminal row appended before run() starts.
    session.append({
        "role": "system", "source": "daemon",
        "content": "[Sub-agent] codex completed. Summary: migrated. Transcript: t.jsonl.",
        "_meta": {"event": "sub_agent_terminal", "kind": "completed",
                  "display_content": "[Sub-agent] codex completed. Summary: migrated. Transcript: t.jsonl."},
    })
    await loop.run()
    row = _memory_rows(session)[0]
    assert "Sub-agent results received: codex (completed)\n" in row["content"]


# ---------------------------------------------------------------------------
# Skips
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_that_already_wrote_a_memory_file_is_not_asked(tmp_path):
    llm = _ScriptedLLM([_edit_state_call("tc_main"), _reply("Updated the state file.")])
    session, tools, loop = _rig(tmp_path, llm)
    await loop.run()
    assert _memory_rows(session) == []
    assert llm.calls == 2


@pytest.mark.asyncio
async def test_run_that_wrote_a_workspace_file_only_is_asked(tmp_path):
    llm = _ScriptedLLM([
        _tool_calls(("tc1", "write", {"path": "notes/today.md", "content": "x"})),
        _reply("Wrote the notes."),
        _reply("nothing to record"),
    ])
    session, tools, loop = _rig(tmp_path, llm)
    await loop.run()
    assert len(_memory_rows(session)) == 1
    assert "Files this run changed: notes/today.md\n" in _memory_rows(session)[0]["content"]


@pytest.mark.asyncio
async def test_llm_error_exit_is_not_asked(tmp_path):
    llm = _ScriptedLLM([_reply("unreached")], raise_on_call=0)
    session, tools, loop = _rig(tmp_path, llm)
    await loop.run()
    assert _memory_rows(session) == []
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_dispatch_yield_exit_is_not_asked(tmp_path):
    llm = _ScriptedLLM([_tool_calls(("tc_y", "yield_tool", {})), _reply("unreached")])
    session, tools, loop = _rig(tmp_path, llm)
    await loop.run()
    assert _memory_rows(session) == []
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_approval_pause_exit_is_not_asked(tmp_path):
    interceptor = MagicMock()
    interceptor.should_intercept = MagicMock(return_value=True)
    interceptor.on_intercept = MagicMock()
    llm = _ScriptedLLM([
        _tool_calls(("tc_w", "write", {"path": "a.md", "content": "x"})),
        _reply("unreached"),
    ])
    session, tools, loop = _rig(tmp_path, llm, interceptor=interceptor)
    await loop.run()
    assert session._paused_for_approval is True
    assert _memory_rows(session) == []


@pytest.mark.asyncio
async def test_cancelled_run_is_not_asked(tmp_path):
    llm = _ScriptedLLM([_reply("unreached")], gate_call=0)
    session, tools, loop = _rig(tmp_path, llm)
    task = asyncio.create_task(loop.run())
    await asyncio.wait_for(llm.in_flight.wait(), timeout=2)
    await loop.cancel_turn()
    llm.release.set()
    await task
    assert _memory_rows(session) == []
    assert loop.get_completion_state()[0] == "cancelled"


@pytest.mark.asyncio
async def test_memory_session_is_never_asked(tmp_path):
    llm = _ScriptedLLM([_reply("tidied."), _reply("unreached")])
    session, tools, loop = _rig(tmp_path, llm, origin="memory")
    await loop.run()
    assert _memory_rows(session) == []
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_disabled_loop_is_never_asked(tmp_path):
    llm = _ScriptedLLM([_reply("ok."), _reply("unreached")])
    session, tools, loop = _rig(tmp_path, llm, enabled=False)
    await loop.run()
    assert _memory_rows(session) == []
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_scratch_project_is_not_asked(tmp_path):
    llm = _ScriptedLLM([_reply("ok."), _reply("unreached")])
    session, tools, loop = _rig(tmp_path, llm)
    loop._context_manager._base_ctx = _prompt_context(str(tmp_path), is_scratch=True)
    await loop.run()
    assert _memory_rows(session) == []


# ---------------------------------------------------------------------------
# Bounds — by construction, not by the model
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_turn_never_triggers_itself_again(tmp_path):
    """The record turn ends with text — the run then ends; no second row."""
    llm = _ScriptedLLM([_reply("reply"), _edit_state_call(), _reply("Done.")])
    session, tools, loop = _rig(tmp_path, llm)
    await loop.run()
    assert len(_memory_rows(session)) == 1
    assert llm.calls == 3


@pytest.mark.asyncio
async def test_llm_call_cap_ends_the_run(tmp_path):
    """A model that keeps asking for tools gets at most three calls."""
    llm = _ScriptedLLM([_reply("reply"),
                        _tool_calls(("tc_r", "read", {"path": "orbital/PROJECT_STATE.md"}))])
    session, tools, loop = _rig(tmp_path, llm)
    await loop.run()
    assert llm.calls == 1 + MEMORY_RECORD_MAX_LLM_CALLS
    assert len(_memory_rows(session)) == 1
    assert [n for n, _ in tools.executed] == ["read"] * MEMORY_RECORD_MAX_LLM_CALLS


@pytest.mark.asyncio
async def test_tool_call_cap_skips_the_extras_and_ends_the_run(tmp_path):
    many = tuple((f"tc_{i}", "read", {"path": "orbital/INDEX.md"})
                 for i in range(MEMORY_RECORD_MAX_TOOL_CALLS + 2))
    llm = _ScriptedLLM([_reply("reply"), _tool_calls(*many), _reply("unreached")])
    session, tools, loop = _rig(tmp_path, llm)
    await loop.run()
    assert len(tools.executed) == MEMORY_RECORD_MAX_TOOL_CALLS
    assert llm.calls == 2  # the batch hit the cap, so no further call
    results = [m for m in session.get_messages() if m.get("role") == "tool"]
    assert len(results) == MEMORY_RECORD_MAX_TOOL_CALLS + 2
    assert all(m.get("_kind") == MEMORY_RECORD_KIND for m in results)
    assert "Skipped" in results[-1]["content"]


@pytest.mark.asyncio
async def test_record_turn_only_runs_memory_file_tools(tmp_path):
    """The flush-turn precedent: scoped to memory persistence, so it can run
    without the approval interceptor. Anything else is refused, not run."""
    llm = _ScriptedLLM([
        _reply("reply"),
        _tool_calls(("tc_a", "write", {"path": "src/app.py", "content": "oops"}),
                    ("tc_b", "yield_tool", {}),
                    ("tc_c", "edit", {"path": "orbital/LESSONS.md",
                                      "old_text": "- never trust a green local build",
                                      "new_text": "- never trust a green local build\n- CI installers differ from local ones"})),
        _reply("Done."),
    ])
    session, tools, loop = _rig(tmp_path, llm)
    await loop.run()
    assert [n for n, _ in tools.executed] == ["edit"]
    results = {m["tool_call_id"]: m["content"] for m in session.get_messages() if m.get("role") == "tool"}
    assert results["tc_a"].startswith("Error")
    assert results["tc_b"].startswith("Error")
    assert results["tc_c"].startswith("Edited")
    assert not os.path.exists(os.path.join(str(tmp_path), "src", "app.py"))


@pytest.mark.asyncio
async def test_a_failing_record_turn_never_fails_the_run(tmp_path):
    llm = _ScriptedLLM([_reply("reply")], raise_on_call=1)
    session, tools, loop = _rig(tmp_path, llm)
    await loop.run()  # must not raise
    assert len(_memory_rows(session)) == 1
    assert loop.last_llm_error is None
    assert loop.get_completion_state()[0] == "text"


# ---------------------------------------------------------------------------
# Ordering with the other run-end events (7c)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deferred_terminal_lands_after_the_record_turn(tmp_path):
    """A worker terminal deferred while the record turn runs is appended by
    the loop's finally — after the record rows, untagged, and unread."""
    llm = _ScriptedLLM([_reply("reply"), _reply("nothing to record")], gate_call=1)
    session, tools, loop = _rig(tmp_path, llm)
    task = asyncio.create_task(loop.run())
    await asyncio.wait_for(llm.in_flight.wait(), timeout=2)
    session.defer_message("[Sub-agent] codex completed", role="system", source="daemon",
                          meta={"event": "sub_agent_terminal", "kind": "completed"})
    llm.release.set()
    await task
    rows = session.get_messages()
    assert rows[-1]["content"] == "[Sub-agent] codex completed"
    assert rows[-1].get("_kind") is None
    assert rows[-2]["content"] == "nothing to record"
    assert llm.calls == 2


@pytest.mark.asyncio
async def test_queued_user_message_waits_for_the_record_turn(tmp_path):
    llm = _ScriptedLLM([_reply("reply"), _reply("nothing to record")], gate_call=1)
    session, tools, loop = _rig(tmp_path, llm)
    task = asyncio.create_task(loop.run())
    await asyncio.wait_for(llm.in_flight.wait(), timeout=2)
    session.queue_message("and another thing", nonce="n1")
    llm.release.set()
    await task
    # Not drained into the record turn: it is still queued for the next run.
    assert session.list_queued() == [("and another thing", "n1")]
    assert llm.calls == 2
    assert session.get_messages()[-1]["content"] == "nothing to record"


# ---------------------------------------------------------------------------
# Before a compaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_is_preceded_by_a_record_turn_then_the_snapshot_refreshes(tmp_path):
    llm = _ScriptedLLM([
        _tool_calls(("tc1", "write", {"path": "docs/a.md", "content": "a"})),   # call 0: work
        _reply("nothing to record"),                                            # call 1: record before compaction
        _reply("All done."),                                                    # call 2: after compaction, run end
        _reply("nothing to record"),                                            # call 3: end-of-run record
    ])
    session, tools, loop = _rig(tmp_path, llm)
    cm = loop._context_manager
    cm.begin_run = MagicMock(wraps=cm.begin_run)
    flags = iter([True])
    cm.should_compact = lambda: next(flags, False)
    cm.flush_fits = lambda: True
    # Make compaction summarize something: pad the history first.
    for i in range(6):
        session.append({"role": "user", "content": f"old {i} " + "x" * 500, "source": "user"})
        session.append({"role": "assistant", "content": f"ok {i} " + "y" * 500, "source": "management"})

    await loop.run()

    rows = session.get_messages()
    mem_idx = [i for i, m in enumerate(rows) if (m.get("_meta") or {}).get("event") == "memory_record"]
    compaction_idx = [i for i, m in enumerate(session._messages) if m.get("_compaction")]
    assert len(mem_idx) == 2, mem_idx
    assert compaction_idx, "compaction did not happen"
    # The first record turn happened BEFORE the compaction marker was written.
    first_record_reply = next(i for i, m in enumerate(rows) if m.get("content") == "nothing to record")
    assert mem_idx[0] < first_record_reply
    assert session._messages.index(rows[first_record_reply]) < compaction_idx[0]
    # begin_run: once at run start, once after compaction.
    assert cm.begin_run.call_count == 2
    assert llm.calls == 4
