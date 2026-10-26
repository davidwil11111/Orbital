# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 134 Part B §3.5 (and the B5 bug): a wake that arrives while ANOTHER
session holds the project's slot is owed, not lost. It is delivered when the
slot frees, after any queued user messages (spec 006 FIFO first). The same
path covers sub-agent terminals, which used to be persisted and never retried
(live: daemon.log 2026-10-25 10:32:28)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_os.agent.session import Session
from agent_os.daemon_v2 import agent_manager as am
from agent_os.daemon_v2.agent_manager import AgentManager, ProjectHandle
from agent_os.daemon_v2.models import make_session_key

PROJECT = "proj_owed"
A = "sess-owed-aaaa"
B = "sess-owed-bbbb"
C = "sess-owed-cccc"


@pytest.fixture
def manager(tmp_path):
    ws = MagicMock()
    ws.broadcast = MagicMock()
    sub = MagicMock()
    sub.list_active = MagicMock(return_value=[])
    mgr = AgentManager(project_store=MagicMock(), ws_manager=ws,
                       sub_agent_manager=sub, activity_translator=MagicMock(),
                       process_manager=MagicMock())
    sessions = {sid: Session.new(sid, str(tmp_path)) for sid in (A, B, C)}
    mgr.started: list[str] = []

    async def hydrate(project_id, session_id):
        return sessions.get(session_id)

    async def start_agent(project_id, config, initial_message=None, *, session_id=None,
                          session=None, **kw):
        sid = session.session_uuid if session is not None else session_id
        holder = mgr.current_holder_session_id(project_id)
        if holder is not None and holder != sid:
            raise ValueError(f"Slot held by session {holder}")
        mgr.started.append(sid)
        _hold_slot(mgr, sid)                    # a started loop holds the slot

    mgr._hydrate_session = hydrate
    mgr._abuild_agent_config_from_project = AsyncMock(return_value=MagicMock())
    mgr.start_agent = start_agent
    mgr._sessions = sessions
    return mgr


def _hold_slot(mgr, sid):
    """Plant a live handle whose loop task is running."""
    fut = asyncio.get_event_loop().create_future()
    handle = ProjectHandle(session=mgr._sessions[sid], loop=MagicMock(), provider=None,
                           registry=None, context_manager=None, interceptor=None,
                           task=fut)
    mgr._handles[make_session_key(PROJECT, sid)] = handle
    return fut


def _free_slot(mgr, sid, fut):
    fut.set_result(None)
    mgr._handles.pop(make_session_key(PROJECT, sid), None)
    mgr._release_slot(PROJECT)


async def _settle():
    for _ in range(10):
        await asyncio.sleep(0)


@pytest.mark.parametrize("event", ["job_terminal", "job_checkin", "sub_agent_terminal"])
async def test_a_wake_lost_to_another_sessions_slot_is_owed_and_delivered_later(
        manager, event):
    fut = _hold_slot(manager, B)
    status = await manager.inject_system_message(
        PROJECT, "job ended", session_id=A, meta={"event": event})
    assert status == "persisted"
    assert manager.started == []
    assert manager._sessions[A].get_messages()[-1]["content"] == "job ended"

    _free_slot(manager, B, fut)
    await _settle()
    assert manager.started == [A]


def test_job_events_wake():
    assert {"job_terminal", "job_checkin"} <= am._WAKE_EVENTS
    assert "job_note" not in am._WAKE_EVENTS and "job_started" not in am._WAKE_EVENTS


async def test_user_messages_queued_for_the_slot_go_first(manager):
    fut = _hold_slot(manager, B)
    await manager.inject_system_message(PROJECT, "job ended", session_id=A,
                                        meta={"event": "job_terminal"})
    manager.enqueue_pending_inject(PROJECT, C, "user's message")
    manager.inject_message = AsyncMock(side_effect=lambda *a, **k: _hold_slot(manager, C))
    _free_slot(manager, B, fut)
    await _settle()
    manager.inject_message.assert_awaited_once()
    assert manager.started == []              # C holds the slot now

    c_fut = manager._handles[make_session_key(PROJECT, C)].task
    _free_slot(manager, C, c_fut)
    await _settle()
    assert manager.started == [A]


async def test_an_owed_wake_already_read_is_dropped(manager):
    fut = _hold_slot(manager, B)
    await manager.inject_system_message(PROJECT, "job ended", session_id=A,
                                        meta={"event": "job_terminal"})
    manager._sessions[A].append({"role": "assistant", "content": "I read it"})
    _free_slot(manager, B, fut)
    await _settle()
    assert manager.started == []


async def test_a_suppressed_or_non_waking_row_is_never_owed(manager):
    fut = _hold_slot(manager, B)
    for meta in ({"event": "job_note", "suppress_wake": True},
                 {"event": "job_terminal", "suppress_wake": True},
                 {"event": "something_else"}):
        await manager.inject_system_message(PROJECT, "note", session_id=A, meta=meta)
    _free_slot(manager, B, fut)
    await _settle()
    assert manager.started == []


async def test_one_owed_wake_per_session_and_in_order(manager):
    fut = _hold_slot(manager, B)
    for sid in (A, C, A):
        await manager.inject_system_message(PROJECT, "x", session_id=sid,
                                            meta={"event": "job_terminal"})
    assert list(manager._owed_wakes[PROJECT]) == [A, C]
    _free_slot(manager, B, fut)
    await _settle()
    assert manager.started == [A]             # A now runs; C waits for it

    # A's loop ends → C's owed wake goes out next
    manager._handles[make_session_key(PROJECT, A)] = ProjectHandle(
        session=manager._sessions[A], loop=MagicMock(), provider=None, registry=None,
        context_manager=None, interceptor=None, task=None)
    manager._release_slot(PROJECT)
    await _settle()
    # A's handle is idle with nothing unread; C is hydrated and started
    assert manager.started == [A, C]
