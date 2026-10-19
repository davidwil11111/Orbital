# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""A sub-agent turn that fails with an authentication error marks the agent's
sign-in expired on the setup engine; a successful turn clears it (1018 bench
item 9: the Claude Code card said "signed in" with an expired OAuth token).

The hook is ``SubAgentManager._on_prompt_turn_closed``: the process manager
calls it after writing the turn's rows (error chunk, then the turn boundary)
to the sub-agent transcript, with the transport's honest ``cause``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_os.daemon_v2.sub_agent_manager import SubAgentManager
from agent_os.daemon_v2.sub_agent_transcript import SubAgentTranscript


class _ProcessManager:
    def set_turn_closed_callback(self, callback):
        pass

    def set_permission_request_callback(self, callback):
        pass


class _Engine:
    def __init__(self):
        from agent_os.agents.setup_engine import is_auth_failure_text
        self._match = is_auth_failure_text
        self.noted: list[tuple[str, str]] = []
        self.cleared: list[str] = []

    def note_auth_failure(self, slug, text):
        self.noted.append((slug, text))
        return self._match(text)

    def clear_auth_failure(self, slug):
        self.cleared.append(slug)


def _manager(tmp_path, rows: list[dict], handle="claude-code"):
    engine = _Engine()
    manager = SubAgentManager(_ProcessManager(), setup_engine=engine)
    transcript = SubAgentTranscript(str(tmp_path), handle, "t1")
    for row in rows:
        transcript.append(dict(row))
    manager._transcripts[("p1", "s1", handle)] = transcript
    return manager, engine


def _row(chunk_type, content):
    return {"source": "claude-code", "content": content, "chunk_type": chunk_type}


BOUNDARY = _row("turn_complete", "")
AUTH = ("Failed to authenticate. API Error: 401 {\"type\":\"error\",\"error\":"
        "{\"type\":\"authentication_error\",\"message\":\"OAuth token has expired.\"}}")


@pytest.mark.asyncio
async def test_auth_error_turn_is_recorded(tmp_path):
    manager, engine = _manager(tmp_path, [
        _row("response", "earlier answer"), BOUNDARY,
        _row("response", AUTH), _row("error", f"Error: {AUTH}"), BOUNDARY,
    ])
    await manager._on_prompt_turn_closed("p1", "claude-code", session_id="s1",
                                         cause="error")
    assert len(engine.noted) == 1
    slug, text = engine.noted[0]
    assert slug == "claude-code"
    assert "OAuth token has expired" in text
    assert "earlier answer" not in text  # only this turn's rows
    assert engine.cleared == []


@pytest.mark.asyncio
async def test_stream_death_is_checked_too(tmp_path):
    # No boundary is written when the stream dies without one.
    manager, engine = _manager(tmp_path, [
        _row("error", "Error: OAuth session expired"),
    ])
    await manager._on_prompt_turn_closed("p1", "claude-code", session_id="s1",
                                         cause="stream_ended")
    assert engine.noted and "OAuth session expired" in engine.noted[0][1]


@pytest.mark.asyncio
async def test_successful_turn_clears_the_record(tmp_path):
    manager, engine = _manager(tmp_path, [_row("response", "done"), BOUNDARY])
    await manager._on_prompt_turn_closed("p1", "claude-code", session_id="s1",
                                         cause="success")
    assert engine.cleared == ["claude-code"]
    assert engine.noted == []


@pytest.mark.asyncio
async def test_stopped_turn_neither_records_nor_clears(tmp_path):
    manager, engine = _manager(tmp_path, [_row("error", AUTH), BOUNDARY])
    await manager._on_prompt_turn_closed("p1", "claude-code", session_id="s1",
                                         cause="stopped")
    assert engine.noted == [] and engine.cleared == []


@pytest.mark.asyncio
async def test_no_transcript_and_no_engine_hooks_are_harmless(tmp_path):
    manager = SubAgentManager(_ProcessManager(), setup_engine=SimpleNamespace())
    await manager._on_prompt_turn_closed("p1", "claude-code", session_id="s1",
                                         cause="error")
    manager2, engine = _manager(tmp_path, [])
    manager2._transcripts.clear()
    await manager2._on_prompt_turn_closed("p1", "claude-code", session_id="s1",
                                          cause="error")
    assert engine.noted == []
