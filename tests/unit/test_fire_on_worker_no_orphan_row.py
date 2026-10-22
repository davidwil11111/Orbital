# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 126 C — a worker fire that never starts leaves no orphan user row.

``_fire_on_worker`` writes the trigger's message into the session before the
send (bug #59: whoever injects persists first — the recap and the dispatch
marker both depend on the row being on disk before the prompt goes out). When
the send then fails before any worker ran, that row is retracted again, so a
refused resume does not leave a "[Triggered by schedule …]" bubble with no
reply and a misleading last-activity in the session list.
"""

import json
import os
import tempfile

import pytest
from unittest.mock import AsyncMock, MagicMock

from agent_os.agent.session import Session
from agent_os.daemon_v2.project_store import ProjectStore
from agent_os.daemon_v2.trigger_manager import TriggerManager

SID = "imp_codex_019f0000aaaa7bbb"


def _project(ws):
    store = ProjectStore(data_dir=tempfile.mkdtemp())
    pid = store.create_project({"name": "P", "workspace": ws, "model": "gpt-4",
                                "api_key": "sk-test"})
    store.update_project(pid, {"triggers": [{
        "id": "trg_imp", "name": "Radar", "type": "schedule", "enabled": True,
        "schedule": {"cron": "30 10 * * *", "human": "Daily at 10:30"},
        "task": "Reply PONG", "trigger_count": 0, "last_triggered": None,
        "agent": "codex", "session_id": SID,
    }]})
    return store, pid


def _session(ws, pid):
    session = Session.new(SID, ws, pid, session_id=SID, origin="imported")
    session.append({"role": "user", "content": "earlier question", "source": "user"})
    session.append({"role": "assistant", "content": "earlier answer"})
    session.set_sub_agent_thread("codex", session_id="thread-1", rollout_path="/x")
    return session


def _manager(session, send):
    sub = MagicMock()
    sub.send = send
    mgr = MagicMock()
    mgr.is_running = MagicMock(return_value=False)
    mgr.get_sub_agent_manager = MagicMock(return_value=sub)
    mgr.get_session = MagicMock(return_value=None)
    mgr._load_session_from_disk = MagicMock(return_value=session)
    mgr.materialize_imported_session = MagicMock(return_value="existing")
    return mgr


def _rows(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


@pytest.fixture
def ws(tmp_path):
    return os.path.realpath(str(tmp_path))


@pytest.mark.asyncio
async def test_a_failed_send_retracts_the_user_row_from_disk_and_memory(ws):
    store, pid = _project(ws)
    session = _session(ws, pid)
    before = _rows(session._filepath)
    mgr = _manager(session, AsyncMock(return_value=(
        "Error: adapter start failed: codex thread/resume failed: "
        "{'code': -32600, 'message': 'thread t already has an active writer'}")))
    ws_mgr = MagicMock()
    tm = TriggerManager(store, mgr, ws_manager=ws_mgr)

    await tm._fire_trigger(pid, "trg_imp")

    assert _rows(session._filepath) == before, "the orphan row must not survive"
    assert [m.get("content") for m in session.get_messages() if m.get("role") == "user"] \
        == ["earlier question"]
    # The failure still reaches the UI as an agent.status error.
    statuses = [c.args[1] for c in ws_mgr.broadcast.call_args_list
                if c.args[1].get("type") == "agent.status"]
    assert statuses and statuses[0]["status"] == "error"
    assert not [c for c in ws_mgr.broadcast.call_args_list
                if c.args[1].get("type") == "trigger.fired"]


@pytest.mark.asyncio
async def test_a_send_that_raises_retracts_too(ws):
    store, pid = _project(ws)
    session = _session(ws, pid)
    before = _rows(session._filepath)
    mgr = _manager(session, AsyncMock(side_effect=RuntimeError("socket closed")))
    tm = TriggerManager(store, mgr, ws_manager=MagicMock())
    await tm._fire_trigger(pid, "trg_imp")
    assert _rows(session._filepath) == before


@pytest.mark.asyncio
async def test_a_successful_send_keeps_the_row_in_place(ws):
    """Row-first ordering is unchanged on success: the trigger's message is
    on disk before the worker is dispatched, exactly where it was."""
    store, pid = _project(ws)
    session = _session(ws, pid)
    before = _rows(session._filepath)
    seen_on_disk_at_send = []

    async def send(*args, **kwargs):
        seen_on_disk_at_send.append(_rows(session._filepath)[-1])
        return "Message sent to codex."

    mgr = _manager(session, send)
    tm = TriggerManager(store, mgr, ws_manager=MagicMock())
    await tm._fire_trigger(pid, "trg_imp")
    rows = _rows(session._filepath)
    assert rows[:-1] == before
    assert rows[-1]["role"] == "user" and "Reply PONG" in rows[-1]["content"]
    assert seen_on_disk_at_send == [rows[-1]]


@pytest.mark.asyncio
async def test_retraction_only_removes_that_one_row(ws):
    """Two identical earlier user rows stay; only the row this fire wrote goes."""
    store, pid = _project(ws)
    session = _session(ws, pid)
    msg = "[Triggered by schedule 'Radar' (Daily at 10:30)]\n\nReply PONG"
    session.append({"role": "user", "content": msg, "source": "user"})
    before = _rows(session._filepath)
    mgr = _manager(session, AsyncMock(return_value="Error: agent 'codex' not running"))
    tm = TriggerManager(store, mgr, ws_manager=MagicMock())
    await tm._fire_trigger(pid, "trg_imp")
    assert _rows(session._filepath) == before
