# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 112 A4: one ``[CACHE_AUDIT]`` line per streamed response, tagged with
the caller.

The OpenAI streaming path logged the audit twice for one response — once on
the finish_reason chunk that carried usage and again on the trailing
usage-only chunk — so every offline attribution script had to dedupe. The
``caller`` field lets spec 124's Memory session be counted separately.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from agent_os.agent.providers.openai_compat import LLMProvider


def _usage():
    return SimpleNamespace(prompt_tokens=1000, completion_tokens=20,
                           prompt_cache_hit_tokens=900,
                           prompt_tokens_details=None)


def _delta_chunk(text: str, finish_reason=None, usage=None):
    delta = SimpleNamespace(content=text, tool_calls=None, reasoning_content=None)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def _usage_only_chunk():
    return SimpleNamespace(choices=[], usage=_usage())


class _FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    async def create(self, **_kwargs):
        chunks = self._chunks

        async def _iter():
            for c in chunks:
                yield c
        return _iter()


def _provider(chunks) -> LLMProvider:
    p = LLMProvider(model="deepseek-chat", api_key="sk-test-0000", base_url="http://x")
    p._openai_client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FakeCompletions(chunks)))
    return p


async def _drain(provider):
    return [c async for c in provider.stream([{"role": "user", "content": "hi"}])]


def _audit_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "[CACHE_AUDIT]" in r.getMessage()]


@pytest.mark.asyncio
async def test_streamed_response_with_usage_on_finish_and_trailing_chunk_audits_once(caplog):
    caplog.set_level(logging.INFO, logger="orbital.cache_audit")
    chunks = [
        _delta_chunk("Hel"),
        _delta_chunk("lo", finish_reason="stop", usage=_usage()),
        _usage_only_chunk(),
    ]
    out = await _drain(_provider(chunks))
    assert any(c.is_final for c in out)
    lines = _audit_lines(caplog)
    assert len(lines) == 1, lines
    assert "caller=main" in lines[0]
    assert "cached=900" in lines[0]


@pytest.mark.asyncio
async def test_streamed_response_with_only_a_trailing_usage_chunk_audits_once(caplog):
    caplog.set_level(logging.INFO, logger="orbital.cache_audit")
    chunks = [_delta_chunk("Hi"), _delta_chunk("", finish_reason="stop"), _usage_only_chunk()]
    await _drain(_provider(chunks))
    assert len(_audit_lines(caplog)) == 1


@pytest.mark.asyncio
async def test_caller_tag_follows_the_provider_attribute(caplog):
    caplog.set_level(logging.INFO, logger="orbital.cache_audit")
    p = _provider([_delta_chunk("x", finish_reason="stop", usage=_usage())])
    p.cache_audit_caller = "memory"
    await _drain(p)
    lines = _audit_lines(caplog)
    assert len(lines) == 1 and "caller=memory" in lines[0]
