# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the Layer-1 resume context (ContextManager) and session-end loop wiring (AgentLoop).

- The Layer-1 rows are the resume context; the separate first-call-only
  cold-resume row is gone (2026-10-20).
- on_session_end callback in AgentLoop.run() finally block
"""

import asyncio
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_os.agent.providers.types import (
    StreamChunk,
    LLMResponse,
    TokenUsage,
    LLMError,
)
from agent_os.agent.tools.base import ToolResult
from agent_os.agent.prompt_builder import PromptBuilder, PromptContext, Autonomy
from agent_os.agent.session import Session, persist_user_row
from agent_os.agent.loop import AgentLoop
from agent_os.agent.context import ContextManager
from agent_os.agent.workspace_files import WorkspaceFileManager


# ---------------------------------------------------------------------------
# Helpers (same patterns as test_component_a.py)
# ---------------------------------------------------------------------------

def _make_base_prompt_context(workspace: str) -> PromptContext:
    return PromptContext(
        workspace=workspace,
        model="test-model",
        autonomy=Autonomy.HANDS_OFF,
        enabled_agents=[],
        tool_names=["read", "write", "shell"],
        os_type="linux",
        datetime_now="2026-01-01T00:00:00",
        context_usage_pct=0.0,
    )


class MockPromptBuilder:
    def build(self, context: PromptContext) -> tuple[str, str, str]:
        return ("cached-system-prefix", "semi-stable-suffix", "dynamic-runtime")


def _make_text_response(text: str, input_tok: int = 100, output_tok: int = 50) -> LLMResponse:
    return LLMResponse(
        raw_message={"role": "assistant", "content": text},
        text=text,
        tool_calls=[],
        has_tool_calls=False,
        finish_reason="stop",
        status_text=None,
        usage=TokenUsage(input_tokens=input_tok, output_tokens=output_tok),
    )


class MockProvider:
    def __init__(self, responses=None):
        self._responses = list(responses or [])
        self._call_idx = 0

    async def stream(self, messages, tools=None):
        idx = self._call_idx
        self._call_idx += 1
        if self._responses and idx < len(self._responses):
            resp = self._responses[idx]
            if resp.text:
                yield StreamChunk(text=resp.text)
            yield StreamChunk(is_final=True, usage=resp.usage)
        else:
            yield StreamChunk(text="default", is_final=True,
                              usage=TokenUsage(10, 5))

    async def complete(self, messages, tools=None):
        idx = self._call_idx
        self._call_idx += 1
        if self._responses and idx < len(self._responses):
            return self._responses[idx]
        return _make_text_response("default")


class MockToolRegistry:
    def __init__(self):
        self.execute_calls = []

    def schemas(self):
        return []

    def execute(self, name, arguments):
        self.execute_calls.append((name, arguments))
        return ToolResult(content=f"result of {name}")

    def tool_names(self):
        return []

    def reset_run_state(self):
        pass


# ===========================================================================
# Cold Resume: ContextManager + WorkspaceFileManager
# ===========================================================================

class TestNoColdResumeInjection:
    """The first-call-only "[WORKSPACE MEMORY — Resume Context]" row was
    removed 2026-10-20: it re-injected the full, unbudgeted text of the same
    four Layer-1 files that are injected every turn anyway, then vanished on
    the second call and broke the prefix cache in every session."""

    def test_no_resume_row_on_any_call(self, tmp_path):
        wfm = WorkspaceFileManager(str(tmp_path))
        wfm.ensure_dir()
        wfm.write("state", "Project is in phase 2.")
        wfm.write("decisions", "Chose PostgreSQL.")
        session = Session.new("cold1", str(tmp_path))
        session.append({"role": "user", "content": "hi", "source": "user"})
        cm = ContextManager(session, MockPromptBuilder(),
                            _make_base_prompt_context(str(tmp_path)),
                            workspace_files=wfm)
        for _ in range(2):
            messages = cm.prepare()
            assert not [m for m in messages if "WORKSPACE MEMORY" in m.get("content", "")]
            layer1 = " ".join(m["content"] for m in messages if m["role"] == "system")
            assert "Project is in phase 2." in layer1   # the Layer-1 rows ARE the resume context
            assert "Chose PostgreSQL." in layer1
        assert not hasattr(cm, "_cold_resume_injected")
        assert not hasattr(wfm, "build_cold_resume_context")

    def test_second_call_extends_the_first_byte_for_byte(self, tmp_path):
        """What the removal buys: the second call's prefix is the first's."""
        wfm = WorkspaceFileManager(str(tmp_path))
        wfm.ensure_dir()
        wfm.write("state", "Project is in phase 2.")
        session = Session.new("cold2", str(tmp_path))
        session.append({"role": "user", "content": "hi", "source": "user"})
        cm = ContextManager(session, MockPromptBuilder(),
                            _make_base_prompt_context(str(tmp_path)),
                            workspace_files=wfm)
        cm.begin_run()
        first = cm.prepare()
        session.append({"role": "assistant", "content": "ok", "source": "management"})
        session.append({"role": "user", "content": "more", "source": "user"})
        second = cm.prepare()
        import json as _json
        assert [_json.dumps(m, sort_keys=True) for m in second[:len(first) - 1]] == \
            [_json.dumps(m, sort_keys=True) for m in first[:-1]]

    def test_context_budget_with_large_layer1_file(self, tmp_path):
        """Large workspace files should reduce sliding window proportionally."""
        ws_with = tmp_path / "with_files"
        ws_with.mkdir()
        ws_without = tmp_path / "without_files"
        ws_without.mkdir()

        wfm_with = WorkspaceFileManager(str(ws_with))
        wfm_with.ensure_dir()
        wfm_with.write("state", "X" * 100_000)

        builder = MockPromptBuilder()
        session_with = Session.new("cold5_with", str(ws_with))
        cm_with = ContextManager(
            session_with, builder, _make_base_prompt_context(str(ws_with)),
            model_context_limit=50_000, response_reserve=5_000,
            workspace_files=wfm_with,
        )
        session_without = Session.new("cold5_without", str(ws_without))
        cm_without = ContextManager(
            session_without, builder, _make_base_prompt_context(str(ws_without)),
            model_context_limit=50_000, response_reserve=5_000,
        )
        cm_with.prepare()
        cm_without.prepare()
        assert cm_with.usage_percentage > cm_without.usage_percentage

    def test_none_workspace_files(self, tmp_path):
        """When workspace_files=None (default), prepare() works normally."""
        session = Session.new("cold6", str(tmp_path))
        cm = ContextManager(session, MockPromptBuilder(),
                            _make_base_prompt_context(str(tmp_path)))
        messages = cm.prepare()
        assert not [m for m in messages if "WORKSPACE MEMORY" in m.get("content", "")]
        assert len(messages) >= 1
        assert messages[0]["role"] == "system"


# ===========================================================================
# Session-End: AgentLoop on_session_end callback
# ===========================================================================

class TestSessionEndCallback:

    # NOTE: test_session_end_triggered_on_graceful_stop was DELETED in
    # TASK-cancel-arch-04. The fire-and-forget asyncio.create_task(
    # self._on_session_end()) call site at the end of AgentLoop.run() was
    # removed; the synchronous session-end inside agent_manager.new_session()
    # is now the sole authoritative summarization path. The deleted test's
    # premise no longer holds.

    @pytest.mark.asyncio
    async def test_session_end_skipped_on_llm_failure(self, tmp_path):
        """LLM fails 3 times -> on_session_end skipped (provider unreachable)."""
        session = Session.new("end2", str(tmp_path))

        class ErrorProvider:
            _call_count = 0

            async def stream(self, messages, tools=None):
                self._call_count += 1
                # Fail 3 times to trigger the "3 retries" exit with system msg
                raise LLMError("Server error", status_code=500)
                yield  # make this an async generator

            async def complete(self, messages, tools=None):
                return _make_text_response("ok")

        provider = ErrorProvider()
        registry = MockToolRegistry()
        builder = MockPromptBuilder()
        ctx = _make_base_prompt_context(str(tmp_path))
        context_mgr = ContextManager(session, builder, ctx)

        session_end = AsyncMock()
        loop = AgentLoop(
            session, provider, registry, context_mgr,
            on_session_end=session_end,
        )
        persist_user_row(loop._session, "test")
        await loop.run()

        # session-end is skipped when LLM failed (provider unreachable)
        session_end.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_session_end_failure_logged_not_raised(self, tmp_path):
        """on_session_end callback throws -> fire-and-forget task, no crash in loop."""
        session = Session.new("end3", str(tmp_path))
        provider = MockProvider(responses=[_make_text_response("Done.")])
        registry = MockToolRegistry()
        builder = MockPromptBuilder()
        ctx = _make_base_prompt_context(str(tmp_path))
        context_mgr = ContextManager(session, builder, ctx)

        async def failing_callback():
            raise RuntimeError("Session-end exploded!")

        loop = AgentLoop(
            session, provider, registry, context_mgr,
            on_session_end=failing_callback,
        )

        # Should not raise — callback failure is contained in fire-and-forget task
        persist_user_row(loop._session, "hello")
        await loop.run()
        # Let the background task execute and fail silently
        await asyncio.sleep(0)
        assert not loop.is_running

    @pytest.mark.asyncio
    async def test_session_end_not_called_when_none(self, tmp_path):
        """When on_session_end is None (default), loop exits cleanly."""
        session = Session.new("end4", str(tmp_path))
        provider = MockProvider(responses=[_make_text_response("Done.")])
        registry = MockToolRegistry()
        builder = MockPromptBuilder()
        ctx = _make_base_prompt_context(str(tmp_path))
        context_mgr = ContextManager(session, builder, ctx)

        loop = AgentLoop(session, provider, registry, context_mgr)
        # Should not raise — no callback set
        persist_user_row(loop._session, "hello")
        await loop.run()
        assert not loop.is_running
