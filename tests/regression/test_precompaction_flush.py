# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression: compaction runs NO pre-compaction "memory flush" turn.

History: when compaction fired at >80% context, the loop first appended a
system row ("Pre-compaction memory flush …") and bought one extra LLM
response, at the utility model when one was configured, asking the agent to
write its working state to PROJECT_STATE.md. Spec 112's record turn now runs
before every compaction with the project-state text and anchors in hand, so
the flush asked the same question a second time, as a cold full-window call.
It was removed on 2026-10-21.

This file keeps the inverse guarantees: a compaction never appends the old
flush prompt, never makes an extra ``complete()`` call for it, and sessions
written by earlier versions (which still carry the flush row and its reply)
are still split correctly by ``_task_rows``.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_os.agent import compaction as compaction_mod
from agent_os.agent.compaction import _LEGACY_FLUSH_PROMPT, is_silent_response
from agent_os.agent.session import Session, persist_user_row


@pytest.fixture
def session(tmp_path):
    return Session.new("test-flush", str(tmp_path))


class TestSilentResponseHelper:
    """``is_silent_response`` survives: the summarizer's usable-reply check
    uses it."""

    def test_silent_response_exact(self):
        assert is_silent_response("<silent>") is True

    def test_silent_response_with_whitespace(self):
        assert is_silent_response("  <silent>  \n") is True

    def test_silent_response_empty(self):
        assert is_silent_response("") is True

    def test_silent_response_normal_text(self):
        assert is_silent_response("I will save my state now.") is False

    def test_silent_response_partial(self):
        assert is_silent_response("<silent> but also some text") is False


class TestNoFlushTurnInLoop:
    """When should_compact() is True the loop compacts; it does not buy a
    flush response first."""

    @pytest.mark.asyncio
    async def test_compaction_makes_no_flush_call(self, session, tmp_path):
        from agent_os.agent.loop import AgentLoop
        from agent_os.agent.providers.types import LLMResponse, TokenUsage

        def _resp(text="", tool_calls=None, raw=None):
            tcs = tool_calls or []
            return LLMResponse(
                text=text,
                tool_calls=tcs,
                raw_message=raw or {"role": "assistant", "content": text},
                has_tool_calls=bool(tcs),
                finish_reason="tool_calls" if tcs else "stop",
                status_text=None,
                usage=TokenUsage(input_tokens=50, output_tokens=10),
            )

        call_order = []

        tc_list = [{"id": "tc_f1", "function": {"name": "read", "arguments": "{}"}}]
        tool_response = _resp(
            tool_calls=tc_list,
            raw={"role": "assistant", "content": "", "tool_calls": tc_list},
        )
        final_response = _resp(text="Done.")

        provider = AsyncMock()

        async def mock_complete(messages, tools=None):
            call_order.append("complete")
            return _resp(text="<silent>")

        provider.complete = mock_complete

        context_manager = MagicMock()
        context_manager.prepare.return_value = [{"role": "system", "content": "test"}]
        context_manager.model_context_limit = 128_000
        compact_call_count = {"n": 0}

        def should_compact_side_effect():
            compact_call_count["n"] += 1
            return compact_call_count["n"] == 1

        context_manager.should_compact = MagicMock(side_effect=should_compact_side_effect)

        from agent_os.agent.tools.base import ToolResult
        tool_registry = MagicMock()
        tool_registry.schemas.return_value = []
        tool_registry.is_async.return_value = False
        tool_registry.execute.return_value = ToolResult(content="file contents")
        tool_registry.reset_run_state = MagicMock()

        loop = AgentLoop(
            session=session,
            provider=provider,
            tool_registry=tool_registry,
            context_manager=context_manager,
        )

        responses = iter([tool_response, final_response])

        async def mock_stream(context, tool_schemas):
            return next(responses)

        loop._stream_response = mock_stream

        async def mock_compact_run(sess, prov, utility_provider=None, **kwargs):
            call_order.append("compaction")

        with patch("agent_os.agent.compaction.run", new=mock_compact_run):
            persist_user_row(loop._session, "Do the task")
            await loop.run()

        assert call_order == ["compaction"], call_order
        assert tool_registry.execute.call_count == 1  # the task's own read only
        flush_rows = [m for m in session.get_messages()
                      if "Pre-compaction memory flush" in str(m.get("content"))]
        assert flush_rows == []

    def test_old_prompt_is_never_a_public_constant(self):
        """Nothing may append it again: the only remaining reference is the
        private legacy marker used to read old sessions."""
        assert not hasattr(compaction_mod, "MEMORY_FLUSH_PROMPT")
        assert _LEGACY_FLUSH_PROMPT.startswith("Pre-compaction memory flush.")


class TestLegacySessionsStillSplit:
    """A session written before 2026-10-21 carries the flush row followed by
    the agent's reply. That reply is not the end of a turn: the task rows
    pinned by ``_task_rows`` must still reach back to the real turn start."""

    def test_reply_to_legacy_flush_does_not_start_a_turn(self):
        messages = [
            {"role": "user", "content": "earlier task"},
            {"role": "assistant", "content": "earlier done"},
            {"role": "user", "content": "the real task"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "t1", "function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "t1", "content": "x"},
            {"role": "system", "content": _LEGACY_FLUSH_PROMPT},
            {"role": "assistant", "content": "Saved state."},   # reply to the flush
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "t2", "function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "t2", "content": "y"},
        ]
        rows = compaction_mod._task_rows(messages, split=len(messages) - 1)
        assert [messages[i]["content"] for i in rows] == ["the real task"]

    def test_plain_assistant_reply_does_start_a_turn(self):
        messages = [
            {"role": "user", "content": "earlier task"},
            {"role": "assistant", "content": "earlier done"},
            {"role": "user", "content": "the real task"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "t2", "function": {"name": "read", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "t2", "content": "y"},
        ]
        rows = compaction_mod._task_rows(messages, split=len(messages) - 1)
        assert [messages[i]["content"] for i in rows] == ["the real task"]


class TestCompactionDoesNotIncrementIteration:
    """Compaction is infrastructure, not a task iteration."""

    @pytest.mark.asyncio
    async def test_iteration_counter_unchanged_by_compaction(self):
        from agent_os.agent.loop import AgentLoop
        from agent_os.agent.providers.types import LLMResponse, TokenUsage

        session = MagicMock()
        session.is_paused.return_value = False
        session.is_stopped.return_value = False
        session._paused_for_approval = False
        session.pending_tool_calls = set()
        session.pop_queued_messages.return_value = []
        session.resolve_pending_tool_calls = MagicMock()
        session.append = MagicMock()
        session.append_system = MagicMock()
        session.append_tool_result = MagicMock()
        session.recent_activity.return_value = []
        session.get_messages.return_value = []

        provider = AsyncMock()
        provider.complete = AsyncMock()

        context_manager = MagicMock()
        context_manager.prepare.return_value = [{"role": "system", "content": "test"}]
        context_manager.model_context_limit = 128_000
        context_manager.should_compact.return_value = False

        tool_registry = MagicMock()
        tool_registry.schemas.return_value = []
        tool_registry.reset_run_state = MagicMock()

        text_response = LLMResponse(
            text="Done.",
            tool_calls=[],
            raw_message={"role": "assistant", "content": "Done."},
            has_tool_calls=False,
            finish_reason="stop",
            status_text=None,
            usage=TokenUsage(input_tokens=50, output_tokens=10),
        )

        loop = AgentLoop(
            session=session,
            provider=provider,
            tool_registry=tool_registry,
            context_manager=context_manager,
            max_iterations=1,
        )

        async def mock_stream(context, tool_schemas):
            return text_response

        loop._stream_response = mock_stream

        persist_user_row(loop._session, "test")
        await loop.run()

        session.append.assert_called()
        provider.complete.assert_not_called()
