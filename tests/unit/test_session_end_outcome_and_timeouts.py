# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests: run_session_end_routine outcome reporting + a bounded pass,
and LLMProvider.reasoning_locked_on.

Incident context (orbital-marketing, 2026-07-03..09 and 07-27): the old
whole-file merge regenerated every Layer-1 file in one response, so its
deadline had to scale with the files and still timed out; every failure fell
back to the deterministic backstop and the agent could not tell. Spec 089
replaced it with the memory editor; spec 124 §3.7 with the Memory session,
whose turn is bounded by the daemon (tool cap) and by the pass's own total
timeout.

Invariants:
  1. run_session_end_routine RETURNS an outcome string so the loop can surface
     it to the agent: "edited" | "index_only" | "restored" | "no_change" |
     "skipped" | "backstop_only" | "no_delta" | "not_needed" | "in_flight" |
     "skipped_idempotent".
  2. A hung or failing turn never blocks the floor, and never marks the
     files clean (the next pass retries).
  3. LLMProvider.reasoning_locked_on is True exactly when the model reasons
     and no request param can turn it off.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from agent_os.agent import memory_editor
from agent_os.agent import memory_entries as _mem
from agent_os.agent import state_blocks
from agent_os.agent import workspace_files as wsf_module
from agent_os.agent.workspace_files import (
    WorkspaceFileManager,
    run_session_end_routine,
)
from agent_os.agent.providers.openai_compat import LLMProvider
from agent_os.config.provider_registry import ReasoningInfo

TODAY = "2026-10-21"


def _mock_session(session_id="sess_outcome_test"):
    session = MagicMock()
    session.session_id = session_id
    session.session_uuid = session_id
    session.get_messages.return_value = []
    return session


def _workspace(tmp_path) -> WorkspaceFileManager:
    ws = WorkspaceFileManager(str(tmp_path))
    ws.write("state", "## Now\n- Launch prep.\n- Old note.\n")
    return ws


def _turn(**fields) -> memory_editor.TurnResult:
    base = dict(mode="disk", llm_calls=1, tool_calls=0, ended="text_complete")
    base.update(fields)
    return memory_editor.TurnResult(**base)


def _counting(turn_result=None):
    """A Memory turn runner that counts its calls and changes nothing."""
    calls = []

    async def run_turn(task_text):
        calls.append(task_text)
        return turn_result if turn_result is not None else _turn()
    run_turn.calls = calls
    return run_turn


def _archive_first_state_line(ws):
    async def run_turn(task_text):
        live = ws.read("state")
        b = state_blocks.parse(live)[1][0]
        wsf_module._append_archive_section(ws, _mem.ARCHIVE_OF["state"], f"## [archived {TODAY}]", b.raw)
        ws.write("state", live.replace(b.raw + "\n", ""))
        return _turn(tool_calls=2)
    return run_turn


@pytest.fixture(autouse=True)
def _reset_completion_set():
    wsf_module._completed_session_ends.clear()
    memory_editor._RUNNING.clear()
    yield
    wsf_module._completed_session_ends.clear()


# ---------------------------------------------------------------------------
# Outcome return values
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_returns_edited_on_an_applied_move(tmp_path):
    ws = _workspace(tmp_path)
    outcome = await run_session_end_routine(
        _mock_session("s_out_ok"), None, ws, session_uuid="s_out_ok", force=True,
        run_turn=_archive_first_state_line(ws))
    assert outcome == "edited"


@pytest.mark.asyncio
async def test_returns_no_change_when_the_turn_changes_nothing(tmp_path):
    ws = _workspace(tmp_path)
    outcome = await run_session_end_routine(
        _mock_session("s_nc"), None, ws, session_uuid="s_nc", force=True, run_turn=_counting())
    assert outcome == "no_change"


@pytest.mark.asyncio
async def test_returns_backstop_only_on_timeout(tmp_path):
    ws = _workspace(tmp_path)

    async def timeout(task_text):
        raise asyncio.TimeoutError()
    outcome = await run_session_end_routine(
        _mock_session("s_out_to"), None, ws, session_uuid="s_out_to", force=True, run_turn=timeout)
    assert outcome == "backstop_only"


@pytest.mark.asyncio
async def test_returns_backstop_only_on_non_timeout_error(tmp_path):
    ws = _workspace(tmp_path)

    async def bad(task_text):
        raise ValueError("bad input")
    outcome = await run_session_end_routine(
        _mock_session("s_out_err"), None, ws, session_uuid="s_out_err", force=True, run_turn=bad)
    assert outcome == "backstop_only"


@pytest.mark.asyncio
async def test_returns_no_delta_when_nothing_changed(tmp_path):
    ws = _workspace(tmp_path)
    run_turn = _counting()
    await run_session_end_routine(
        _mock_session("s_nd_1"), None, ws, session_uuid="s_nd_1", force=True, run_turn=run_turn)
    calls_after_first = len(run_turn.calls)
    outcome = await run_session_end_routine(
        _mock_session("s_nd_2"), None, ws,
        session_uuid="s_nd_2", bypass_idempotency=True, force=True, run_turn=run_turn,
    )
    assert outcome == "no_delta"
    assert len(run_turn.calls) == calls_after_first


@pytest.mark.asyncio
async def test_returns_not_needed_when_under_budget_and_not_forced(tmp_path):
    ws = _workspace(tmp_path)
    run_turn = _counting()
    outcome = await run_session_end_routine(
        _mock_session("s_nn"), None, ws, session_uuid="s_nn", run_turn=run_turn)
    assert outcome == "not_needed"
    assert run_turn.calls == []


@pytest.mark.asyncio
async def test_returns_skipped_idempotent_on_repeat_session(tmp_path):
    ws = _workspace(tmp_path)
    run_turn = _counting()
    await run_session_end_routine(
        _mock_session("s_idem"), None, ws, session_uuid="s_idem", force=True, run_turn=run_turn)
    outcome = await run_session_end_routine(
        _mock_session("s_idem"), None, ws, session_uuid="s_idem", force=True, run_turn=run_turn)
    assert outcome == "skipped_idempotent"


@pytest.mark.asyncio
async def test_returns_in_flight_while_another_pass_holds_the_project(tmp_path):
    ws = _workspace(tmp_path)
    memory_editor.claim(ws.workspace)
    outcome = await run_session_end_routine(
        _mock_session("s_if"), None, ws, session_uuid="s_if", force=True, run_turn=_counting())
    assert outcome == "in_flight"


@pytest.mark.asyncio
async def test_returns_skipped_when_the_memory_session_declines(tmp_path):
    ws = _workspace(tmp_path)

    async def declined(task_text):
        return None
    outcome = await run_session_end_routine(
        _mock_session("s_sk"), None, ws, session_uuid="s_sk", force=True, run_turn=declined)
    assert outcome == "skipped"


# ---------------------------------------------------------------------------
# Bounded runs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_hung_turn_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_editor, "EDITOR_TOTAL_TIMEOUT_S", 0.05)
    ws = _workspace(tmp_path)

    async def _hang(task_text):
        await asyncio.sleep(3600)
    outcome = await run_session_end_routine(
        _mock_session("s_hang"), None, ws, session_uuid="s_hang", force=True, run_turn=_hang)
    assert outcome == "backstop_only"


def test_the_turn_is_bounded_by_the_daemons_tool_cap():
    """The cap the loop enforces is the one the task message states."""
    text = memory_editor.build_task_message(
        sizes={k: (1, 10, 5) for k in memory_editor.LIVE_KEYS}, folded=[], over=[], forced=True,
        pointer_paths=[], last_pass=None, sessions=[], today=TODAY, tool_cap=memory_editor.EDITOR_MAX_CALLS,
    )
    assert text.endswith(f"At most {memory_editor.EDITOR_MAX_CALLS} tool calls. Finish with one line "
                         "saying what moved and where.")


# ---------------------------------------------------------------------------
# Retry is not disarmed by a failed pass.
#
# The cleanup marker used to be written unconditionally — including on the
# timeout path — so every failure stamped all four files "clean" and the next
# checkpoint_state short-circuited to no_delta without trying.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failed_pass_leaves_files_dirty_so_next_pass_retries(tmp_path):
    ws = _workspace(tmp_path)

    async def timeout(task_text):
        raise asyncio.TimeoutError()
    first = await run_session_end_routine(
        _mock_session("s_fail_1"), None, ws,
        session_uuid="s_fail_1", bypass_idempotency=True, force=True, run_turn=timeout,
    )
    assert first == "backstop_only"

    second = await run_session_end_routine(
        _mock_session("s_fail_2"), None, ws,
        session_uuid="s_fail_2", bypass_idempotency=True, force=True,
        run_turn=_archive_first_state_line(ws),
    )
    assert second == "edited"


@pytest.mark.asyncio
async def test_successful_pass_still_writes_marker(tmp_path):
    ws = _workspace(tmp_path)
    run_turn = _counting()
    assert await run_session_end_routine(
        _mock_session("s_ok_1"), None, ws,
        session_uuid="s_ok_1", bypass_idempotency=True, force=True, run_turn=run_turn,
    ) == "no_change"
    calls = len(run_turn.calls)
    assert await run_session_end_routine(
        _mock_session("s_ok_2"), None, ws,
        session_uuid="s_ok_2", bypass_idempotency=True, force=True, run_turn=run_turn,
    ) == "no_delta"
    assert len(run_turn.calls) == calls


# ---------------------------------------------------------------------------
# LLMProvider.reasoning_locked_on
# ---------------------------------------------------------------------------

def _provider(reasoning) -> LLMProvider:
    return LLMProvider("test-model", "key", None, sdk="openai", reasoning=reasoning)


def test_locked_on_true_for_model_only():
    """The MiniMax-M3 shape from the incident."""
    r = ReasoningInfo(supported=True, enable="model_only")
    assert _provider(r).reasoning_locked_on is True


def test_locked_on_false_when_param_disables():
    r = ReasoningInfo(supported=True, enable="param:thinking.type=enabled")
    assert _provider(r).reasoning_locked_on is False


def test_locked_on_true_for_unrecognized_param():
    r = ReasoningInfo(supported=True, enable="param:mystery.knob=1")
    assert _provider(r).reasoning_locked_on is True


def test_locked_on_false_for_non_reasoning_model():
    # ReasoningInfo defaults: supported=False, enable="model_only" — the
    # default enable value must NOT read as locked-on when the model
    # doesn't reason at all.
    assert _provider(ReasoningInfo()).reasoning_locked_on is False
    assert _provider(None).reasoning_locked_on is False
