# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The compaction call sequence after the pre-compaction flush was removed
(2026-10-21): record turn (spec 112 A6), then the summarizer request, nothing
else. Drives a real ``AgentLoop.run()`` over a real workspace with the rig
from ``test_memory_record_turn``.
"""

from __future__ import annotations

import pytest

from agent_os.agent import compaction as compaction_mod
from agent_os.agent.providers.types import LLMResponse
from tests.unit.test_memory_record_turn import (
    _USAGE, _ScriptedLLM, _reply, _rig, _tool_calls,
)

_SUMMARY = "Summary: wrote docs/a.md; nothing else happened."


class _RecordingLLM(_ScriptedLLM):
    """``complete()`` serves the compaction summarizer and records what it
    was asked, in order with the streamed calls."""

    def __init__(self, script):
        super().__init__(script)
        self.sequence: list[str] = []
        self.completes: list[list[dict]] = []

    async def stream(self, messages, tools=None):
        self.sequence.append("stream")
        async for chunk in super().stream(messages, tools):
            yield chunk

    async def complete(self, messages, tools=None):
        self.sequence.append("complete")
        self.completes.append(list(messages))
        return LLMResponse(raw_message={"role": "assistant", "content": _SUMMARY},
                           text=_SUMMARY, tool_calls=[], has_tool_calls=False,
                           finish_reason="stop", status_text=None, usage=_USAGE)


@pytest.mark.asyncio
async def test_compaction_is_record_turn_then_summary_and_nothing_else(tmp_path):
    llm = _RecordingLLM([
        _tool_calls(("tc1", "write", {"path": "docs/a.md", "content": "a"})),   # call 0: work
        _reply("nothing to record"),                                            # call 1: record before compaction
        _reply("All done."),                                                    # call 2: after compaction
        _reply("nothing to record"),                                            # call 3: end-of-run record
    ])
    session, tools, loop = _rig(tmp_path, llm)
    cm = loop._context_manager
    flags = iter([True])
    cm.should_compact = lambda: next(flags, False)
    cm.flush_fits = lambda: True
    for i in range(6):
        session.append({"role": "user", "content": f"old {i} " + "x" * 500, "source": "user"})
        session.append({"role": "assistant", "content": f"ok {i} " + "y" * 500, "source": "management"})

    await loop.run()

    # Exactly one summarizer request, between the pre-compaction record turn
    # and the post-compaction call. No flush call anywhere.
    assert llm.sequence == ["stream", "stream", "complete", "stream", "stream"], llm.sequence
    assert llm.completes[0][0]["role"] == "system"
    assert llm.completes[0][0]["content"] == compaction_mod._SUMMARIZATION_PROMPT
    # The record turn's prompt is the one right before the summary.
    assert any("[MEMORY]" in str(m.get("content")) for m in llm.sent[1])
    # The summary landed as the compaction marker row.
    markers = [m for m in session._messages if m.get("_compaction")]
    assert [m["content"] for m in markers] == [_SUMMARY]
    # No row, on disk or in the prompts, carries the old flush prompt.
    for m in session._messages:
        assert "Pre-compaction memory flush" not in str(m.get("content"))
    for sent in llm.sent + llm.completes:
        assert not any("Pre-compaction memory flush" in str(m.get("content")) for m in sent)
    # Only the task's own tool ran (the record turns replied with text).
    assert [n for n, _ in tools.executed] == ["write"]


@pytest.mark.asyncio
async def test_record_turn_is_skipped_when_its_prompt_would_be_trimmed(tmp_path):
    """The gate that used to protect the flush now protects the record turn:
    when one more prompt does not fit untrimmed, the loop compacts at once
    (record turn only at run end)."""
    llm = _RecordingLLM([
        _tool_calls(("tc1", "write", {"path": "docs/a.md", "content": "a"})),   # call 0: work
        _reply("All done."),                                                    # call 1: after compaction
        _reply("nothing to record"),                                            # call 2: end-of-run record
    ])
    session, tools, loop = _rig(tmp_path, llm)
    cm = loop._context_manager
    flags = iter([True])
    cm.should_compact = lambda: next(flags, False)
    cm.flush_fits = lambda: False
    for i in range(6):
        session.append({"role": "user", "content": f"old {i} " + "x" * 500, "source": "user"})
        session.append({"role": "assistant", "content": f"ok {i} " + "y" * 500, "source": "management"})

    await loop.run()

    assert llm.sequence == ["stream", "complete", "stream", "stream"], llm.sequence
    assert [m["content"] for m in session._messages if m.get("_compaction")] == [_SUMMARY]
