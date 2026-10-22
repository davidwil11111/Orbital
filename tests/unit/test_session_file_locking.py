# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 129 §5E: bounded retry on the daemon-internal session-file paths.

Traceback 2026-10-22 12:21:49: the memory reminder's
``persist_mention_message`` hydrated a session 28 ms after
``inject_system_message`` hydrated the same one for the terminal event. Two
``DirectFileLock`` descriptors in one process, ``flock(LOCK_NB)`` → EAGAIN
→ ``FileLockError`` → the reminder was dropped with a 12-frame traceback.

Two layers: a per-session-key ``asyncio.Lock`` around hydration in
``AgentManager`` (the daemon never contends with itself) and the routes'
exponential retry, moved to ``agent_os.utils.file_lock`` and applied to
every daemon-internal load/append (cross-process contention, e.g. a dev
daemon on the same workspace).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_os.agent.project_paths import ProjectPaths
from agent_os.agent.session import Session, persist_user_row
from agent_os.daemon_v2.agent_manager import AgentManager
from agent_os.utils import file_lock
from agent_os.utils.file_lock import FileLockError, retry_session_lock, session_lock

PROJECT = "proj_lock"
SID = "sess-lock-0001"
HANDLE = "claude-code"
TERMINAL_META = {"event": "sub_agent_terminal", "kind": "completed",
                 "handle": HANDLE, "suppress_wake": True}


def _manager(tmp_path) -> AgentManager:
    ws = MagicMock()
    ws.broadcast = MagicMock()
    sub = MagicMock()
    sub.send = AsyncMock(return_value="Message sent")
    sub.list_active = MagicMock(return_value=[])
    sub._adapters = {}
    sub._transcripts = {}
    project_store = MagicMock()
    project_store.get_project = MagicMock(return_value={"workspace": str(tmp_path)})
    return AgentManager(
        project_store=project_store, ws_manager=ws, sub_agent_manager=sub,
        activity_translator=MagicMock(), process_manager=MagicMock(),
    )


def _disk_session(tmp_path) -> str:
    """A session on disk with no live handle (the pinned-chat shape)."""
    session = Session.new(SID, str(tmp_path))
    persist_user_row(session, "run it")
    return ProjectPaths(str(tmp_path)).session_file(SID)


def _rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _hold(path: str, seconds: float):
    """Hold the session's advisory lock from another descriptor (what a
    concurrent hydrate or a second process does), released by a timer."""
    lock = session_lock(path)
    lock.acquire()
    timer = threading.Timer(seconds, lock.release)
    timer.start()
    return lock, timer


# ---------------------------------------------------------------------------
# The helper
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_session_lock_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(file_lock, "LOCK_RETRY_DELAYS", (0.001, 0.002, 0.004))
    attempts = []

    def fn():
        attempts.append(1)
        if len(attempts) < 3:
            raise FileLockError("Session file is locked by another process: /x.jsonl")
        return "ok"

    assert await retry_session_lock(fn) == "ok"
    assert len(attempts) == 3


@pytest.mark.asyncio
async def test_retry_session_lock_awaits_awaitables_and_gives_up_after_the_budget(monkeypatch, caplog):
    monkeypatch.setattr(file_lock, "LOCK_RETRY_DELAYS", (0.001, 0.002))
    attempts = []

    def fn():
        attempts.append(1)
        return asyncio.to_thread(
            lambda: (_ for _ in ()).throw(
                FileLockError("Session file is locked by another process: /locked.jsonl")))

    with caplog.at_level(logging.WARNING, logger="agent_os.utils.file_lock"):
        with pytest.raises(FileLockError):
            await retry_session_lock(fn)
    assert len(attempts) == 3  # one per delay, plus the final attempt
    assert any("/locked.jsonl" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# §6.12 — same-process hydrate of a locked session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hydrate_on_inject_succeeds_once_the_holder_releases(tmp_path):
    mgr = _manager(tmp_path)
    path = _disk_session(tmp_path)
    lock, timer = _hold(path, 0.15)
    try:
        status = await mgr.inject_system_message(
            PROJECT, "[Sub-agent] claude-code completed. Summary: done.",
            session_id=SID, meta=TERMINAL_META)
    finally:
        timer.cancel()
        lock.release()
    assert status == "suppressed"
    rows = _rows(path)
    assert rows[-1]["role"] == "system"
    assert rows[-1]["_meta"]["kind"] == "completed"


@pytest.mark.asyncio
async def test_hydrate_on_inject_fails_with_file_lock_error_after_exhaustion(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(file_lock, "LOCK_RETRY_DELAYS", (0.001, 0.002))
    mgr = _manager(tmp_path)
    path = _disk_session(tmp_path)
    lock = session_lock(path)
    lock.acquire()
    try:
        with caplog.at_level(logging.WARNING):
            with pytest.raises(FileLockError):
                await mgr.inject_system_message(
                    PROJECT, "[Sub-agent] claude-code completed. Summary: done.",
                    session_id=SID, meta=TERMINAL_META)
    finally:
        lock.release()
    assert any(path in r.getMessage() for r in caplog.records
               if r.levelno >= logging.WARNING), [r.getMessage() for r in caplog.records]
    assert all(r.get("role") != "system" for r in _rows(path))


# ---------------------------------------------------------------------------
# §6.13 — persist_mention_message under contention keeps the row
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persist_mention_message_under_contention_does_not_drop_the_row(tmp_path):
    mgr = _manager(tmp_path)
    path = _disk_session(tmp_path)
    lock, timer = _hold(path, 0.15)
    try:
        resolved = await mgr.persist_mention_message(
            PROJECT, SID, {"role": "user", "content": "[Orbital memory] …",
                           "source": "daemon", "target": HANDLE})
    finally:
        timer.cancel()
        lock.release()
    assert resolved == SID
    assert _rows(path)[-1]["content"] == "[Orbital memory] …"


@pytest.mark.asyncio
async def test_terminal_inject_and_reminder_persist_in_the_same_tick_both_land(tmp_path):
    """§8 step 4 in unit form: the terminal-event inject and the reminder's
    persist hydrate the same disk-only session in one event-loop tick. The
    per-key hydration lock serializes them; neither sees EAGAIN."""
    mgr = _manager(tmp_path)
    path = _disk_session(tmp_path)
    results = await asyncio.gather(
        mgr.inject_system_message(
            PROJECT, "[Sub-agent] claude-code completed. Summary: done.",
            session_id=SID, meta=TERMINAL_META),
        mgr.persist_mention_message(
            PROJECT, SID, {"role": "user", "content": "[Orbital memory] record",
                           "source": "daemon", "target": HANDLE}),
    )
    assert results == ["suppressed", SID]
    contents = [r.get("content") for r in _rows(path)]
    assert "[Sub-agent] claude-code completed. Summary: done." in contents
    assert "[Orbital memory] record" in contents
    assert os.path.exists(path)
