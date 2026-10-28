"""Spec 141 — non-UTF-8 bytes in prompt-bound files must never fail a run.

Loop level: a session whose orbital/PROJECT_STATE.md carries a GBK tail (the
shape a zh-CN Windows shell append produces) completes turns with a stub
provider. Context level: a GBK instructions .md is decoded into the prompt and
is NOT rewritten (user-owned). Plus the JSONL readers the loop depends on.
"""

from __future__ import annotations

import json
import os

import pytest

from agent_os.agent.context import ContextManager
from agent_os.agent.loop import AgentLoop
from agent_os.agent.prompt_builder import Autonomy, PromptContext
from agent_os.agent.providers.types import LLMResponse, StreamChunk, TokenUsage
from agent_os.agent.session import Session, persist_user_row
from agent_os.agent.tools.base import ToolResult
from agent_os.utils import text_io

HEAD = "# Project State\n" + "状态：进行中 — all good so far.\n" * 60
TAIL_TEXT = "大量中文\n"


@pytest.fixture(autouse=True)
def _fresh_warnings():
    text_io._reset_warned_for_tests()
    yield
    text_io._reset_warned_for_tests()


@pytest.fixture
def windows_cp936(monkeypatch):
    monkeypatch.setattr(text_io, "_is_windows", lambda: True)
    monkeypatch.setattr(text_io, "_preferred_encoding", lambda: "cp936")


def _ctx(workspace: str) -> PromptContext:
    return PromptContext(
        workspace=workspace, model="test-model", autonomy=Autonomy.HANDS_OFF,
        enabled_agents=[], tool_names=["read", "write", "shell"], os_type="linux",
        datetime_now="2026-01-01T00:00:00", context_usage_pct=0.0,
    )


class _Builder:
    def build(self, context):
        return ("cached-system-prefix", "semi-stable-suffix", "dynamic-runtime")


class _Provider:
    def __init__(self):
        self.calls = 0
        self.seen: list[list[dict]] = []

    async def stream(self, messages, tools=None):
        self.calls += 1
        self.seen.append(messages)
        yield StreamChunk(text=f"reply {self.calls}")
        yield StreamChunk(is_final=True, usage=TokenUsage(10, 5))

    async def complete(self, messages, tools=None):
        self.calls += 1
        return LLMResponse(
            raw_message={"role": "assistant", "content": "ok"}, text="ok",
            tool_calls=[], has_tool_calls=False, finish_reason="stop",
            status_text=None, usage=TokenUsage(10, 5),
        )


class _Registry:
    def schemas(self):
        return []

    def is_async(self, name):
        return False

    def execute(self, name, arguments):
        return ToolResult(content="")

    def tool_names(self):
        return []

    def reset_run_state(self):
        pass


def _seed_state_with_gbk_tail(workspace) -> str:
    orbital = os.path.join(str(workspace), "orbital")
    os.makedirs(orbital, exist_ok=True)
    path = os.path.join(orbital, "PROJECT_STATE.md")
    with open(path, "wb") as f:
        f.write(HEAD.encode("utf-8") + TAIL_TEXT.encode("gbk"))
    return path


def _flatten(messages) -> str:
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            out.extend(p.get("text", "") for p in c if isinstance(p, dict))
    return "\n".join(out)


@pytest.mark.asyncio
async def test_gbk_tail_in_project_state_two_turns_complete(tmp_path):
    _seed_state_with_gbk_tail(tmp_path)
    session = Session.new("gbk_loop", str(tmp_path))
    provider = _Provider()
    loop = AgentLoop(session, provider, _Registry(),
                     ContextManager(session, _Builder(), _ctx(str(tmp_path))))

    persist_user_row(loop._session, "first")
    await loop.run()
    assert loop._loop_exit_path != "internal_error"
    persist_user_row(loop._session, "second")
    await loop.run()
    assert loop._loop_exit_path != "internal_error"

    # Each run makes the user-turn call (+ the spec-112 record turn); the
    # second run's user-turn call is the first call after the first run.
    rows = session.get_messages()
    users = [i for i, m in enumerate(rows) if m["role"] == "user" and m.get("content") in ("first", "second")]
    assert len(users) == 2
    for i in users:
        assert rows[i + 1]["role"] == "assistant" and rows[i + 1]["content"].startswith("reply ")
    assert "状态：进行中" in _flatten(provider.seen[0])


@pytest.mark.asyncio
async def test_gbk_tail_on_windows_reaches_the_prompt_decoded(tmp_path, windows_cp936):
    path = _seed_state_with_gbk_tail(tmp_path)
    session = Session.new("gbk_loop_win", str(tmp_path))
    provider = _Provider()
    loop = AgentLoop(session, provider, _Registry(),
                     ContextManager(session, _Builder(), _ctx(str(tmp_path))))
    persist_user_row(loop._session, "hi")
    await loop.run()
    assert provider.calls >= 1
    assert "大量中文" in _flatten(provider.seen[0])
    with open(path, "rb") as f:
        f.read().decode("utf-8")  # healed (B)


def test_gbk_instructions_md_is_decoded_and_not_rewritten(tmp_path, windows_cp936):
    instructions = tmp_path / "orbital" / "instructions"
    instructions.mkdir(parents=True)
    md = instructions / "style.md"
    raw = "回答请用简体中文。\n".encode("gbk")
    md.write_bytes(raw)

    session = Session.new("gbk_instr", str(tmp_path))
    cm = ContextManager(session, _Builder(), _ctx(str(tmp_path)))
    result = cm.prepare()

    assert "回答请用简体中文。" in _flatten(result)
    assert md.read_bytes() == raw  # user-owned: never rewritten


def test_gbk_instructions_md_off_windows_does_not_raise(tmp_path):
    instructions = tmp_path / "orbital" / "instructions"
    instructions.mkdir(parents=True)
    md = instructions / "style.md"
    raw = "ok\n".encode() + "回答请用简体中文。\n".encode("gbk")
    md.write_bytes(raw)
    session = Session.new("gbk_instr2", str(tmp_path))
    result = ContextManager(session, _Builder(), _ctx(str(tmp_path))).prepare()
    assert "[Project Instructions]\nok\n" in _flatten(result)
    assert md.read_bytes() == raw


def test_session_load_survives_non_utf8_line(tmp_path):
    session = Session.new("jsonl_gbk", str(tmp_path))
    session.append({"role": "user", "content": "hello"})
    with open(session._filepath, "ab") as f:
        f.write(b'{"role": "user", "content": "\xb4\xf3"}\n')
    loaded = Session.load(session._filepath)
    contents = [m.get("content") for m in loaded.get_messages()]
    assert "hello" in contents
    assert len(contents) == 2


def test_recovery_context_reads_non_utf8_previous_session(tmp_path):
    orbital = tmp_path / "orbital"
    sessions = orbital / "sessions"
    sessions.mkdir(parents=True)
    (orbital / "PROJECT_STATE.md").write_text("state", encoding="utf-8")
    rows = (json.dumps({"role": "user", "content": "build a CLI"}) + "\n").encode()
    rows += b'{"role": "assistant", "content": "ok \xb4\xf3"}\n'
    (sessions / "old_session.jsonl").write_bytes(rows)

    session = Session.new("fresh", str(tmp_path))
    result = ContextManager(session, _Builder(), _ctx(str(tmp_path))).prepare()
    assert "build a CLI" in _flatten(result)
