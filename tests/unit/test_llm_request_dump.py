# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 140 I1: ``AGENT_OS_DUMP_LLM_REQUESTS=<dir>`` writes the complete
OpenAI-compatible request body to ``<dir>/<session>_<n>.json``.

Diagnostic only: off by default, no UI. The dump is what a byte-level
cross-session diff (scripts/diag/first_divergence.py) reads, so it must carry
every field that affects tokenization (messages, tools, model, stream flags)
and must never carry the API key.
"""

import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_os.agent.providers.openai_compat import LLMProvider

ENV = "AGENT_OS_DUMP_LLM_REQUESTS"
KEY = "sk-live-0123456789abcdefSECRET"
TOOLS = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]


def _provider(captured: dict, *, stream: bool) -> LLMProvider:
    provider = LLMProvider("deepseek-v4.1-flash", KEY, "https://gw.example/v1", sdk="openai",
                           provider="opencode-go", session_header="x-opencode-session")
    provider.bind_session_id(lambda: "sess_abc")

    async def create(**kwargs):
        captured.update(kwargs)
        if stream:
            class _It:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    raise StopAsyncIteration
            return _It()
        msg = MagicMock()
        msg.content = "ok"
        msg.tool_calls = None
        msg.model_dump.return_value = {"role": "assistant", "content": "ok"}
        choice = MagicMock()
        choice.message = msg
        choice.finish_reason = "stop"
        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = None
        return resp

    provider._openai_client = MagicMock()
    provider._openai_client.chat.completions.create = AsyncMock(side_effect=create)
    return provider


async def _stream(provider, messages, tools=None):
    async for _ in provider.stream(messages, tools=tools):
        pass


MESSAGES = [
    {"role": "system", "content": "You are Orbital."},
    {"role": "user", "content": "hello", "timestamp": "2026-10-27T00:00:00Z"},
]


@pytest.mark.asyncio
async def test_off_by_default_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    captured: dict = {}
    await _stream(_provider(captured, stream=True), MESSAGES, TOOLS)
    assert captured["messages"]  # the request itself still went out
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_on_writes_complete_stream_body_per_session_and_counts(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV, str(tmp_path / "dumps"))
    captured: dict = {}
    provider = _provider(captured, stream=True)
    await _stream(provider, MESSAGES, TOOLS)
    await _stream(provider, MESSAGES, TOOLS)

    files = sorted(p.name for p in (tmp_path / "dumps").iterdir())
    assert files == ["sess_abc_1.json", "sess_abc_2.json"]
    doc = json.loads((tmp_path / "dumps" / "sess_abc_1.json").read_text(encoding="utf-8"))
    body = doc["body"]
    # Exactly what went on the wire (internal fields stripped), not the raw session rows.
    assert body["messages"] == captured["messages"]
    assert body["tools"] == TOOLS
    assert body["model"] == "deepseek-v4.1-flash"
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert "timestamp" not in body["messages"][1]
    meta = doc["meta"]
    assert meta["session_id"] == "sess_abc"
    assert meta["path"] == "stream"
    assert meta["provider"] == "opencode-go"
    assert meta["headers"] == {"x-opencode-session": "sess_abc"}


@pytest.mark.asyncio
async def test_complete_path_dumps_too(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV, str(tmp_path))
    captured: dict = {}
    await _provider(captured, stream=False).complete(MESSAGES)
    (f,) = list(tmp_path.iterdir())
    doc = json.loads(f.read_text(encoding="utf-8"))
    assert doc["meta"]["path"] == "complete"
    assert doc["body"]["stream"] is False
    assert "tools" not in doc["body"]


@pytest.mark.asyncio
async def test_secrets_are_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV, str(tmp_path))
    captured: dict = {}
    leaky = [
        {"role": "system", "content": f"key is {KEY}"},
        {"role": "user", "content": "other key sk-proj-ZZZZyyyyXXXXwwww1234 and Bearer abc.def-ghi"},
    ]
    await _stream(_provider(captured, stream=True), leaky)
    raw = next(tmp_path.iterdir()).read_text(encoding="utf-8")
    assert KEY not in raw
    assert KEY[:12] not in raw
    assert "sk-proj-ZZZZyyyyXXXXwwww1234" not in raw
    assert "abc.def-ghi" not in raw
    # the wire request itself is untouched
    assert KEY in captured["messages"][0]["content"]


@pytest.mark.asyncio
async def test_dump_failure_never_breaks_the_request(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv(ENV, str(blocker / "sub"))  # cannot mkdir under a file
    captured: dict = {}
    await _stream(_provider(captured, stream=True), MESSAGES)
    assert captured["messages"]


@pytest.mark.asyncio
async def test_unbound_session_uses_instance_id_and_safe_filename(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV, str(tmp_path))
    captured: dict = {}
    provider = _provider(captured, stream=True)
    provider.bind_session_id(lambda: "worker:ab12-0/../x")
    await _stream(provider, MESSAGES)
    (f,) = list(tmp_path.iterdir())
    assert f.parent == tmp_path
    assert os.sep not in f.name and ":" not in f.name
