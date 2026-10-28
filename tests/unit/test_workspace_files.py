# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Unit tests for WorkspaceFileManager and session-end routine.

Covers:
  - File CRUD operations (ensure_dir, read, write, append, read_all, exists)
  - Cold resume context assembly (all files, minimal, truncation)
  - Session-end routine = memory editor + floor (choices by id, bad JSON,
    whole-file replies ignored, utility provider)
"""

import json
import logging
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.testutils import streamable

from agent_os.agent.workspace_files import (
    FILE_NAMES,
    WorkspaceFileManager,
    run_session_end_routine,
)



from agent_os.agent import memory_entries as _mem


def _hdr(key: str, content: str) -> str:
    """Expected on-disk form: write() self-heals the <!--format--> header."""
    return _mem.FORMAT_HEADERS[key] + "\n" + content

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ws(tmp_path):
    """Return a WorkspaceFileManager rooted at tmp_path."""
    return WorkspaceFileManager(str(tmp_path))


@pytest.fixture
def ws_dir(tmp_path):
    """Return the orbital directory path (not yet created)."""
    return os.path.join(str(tmp_path), "orbital")


def _mock_session(messages=None, session_id="sess_test123", session_uuid=None):
    """Build a mock session with get_messages() returning the given list.

    Both ``session_id`` (F1) and ``session_uuid`` (F2) are set; the
    workspace-files routine now keys idempotency on ``session_uuid`` per the
    F7 canonical rename. ``session_uuid`` defaults to ``session_id`` so old
    tests that only care about a unique key keep working unchanged.
    """
    session = MagicMock()
    session.session_id = session_id
    session.session_uuid = session_uuid if session_uuid is not None else session_id
    session.get_messages.return_value = messages or []
    return session


def _mock_provider(response_text):
    """Build a mock LLM provider whose complete() returns an object with .text."""
    provider = streamable(AsyncMock())
    resp = MagicMock()
    resp.text = response_text
    provider.complete.return_value = resp
    return provider


# ---------------------------------------------------------------------------
# 1. test_ensure_dir_creates
# ---------------------------------------------------------------------------

def test_ensure_dir_creates(ws, ws_dir):
    """Workspace dir created on first call."""
    assert not os.path.isdir(ws_dir)
    ws.ensure_dir()
    assert os.path.isdir(ws_dir)


# ---------------------------------------------------------------------------
# 2. test_read_nonexistent_returns_none
# ---------------------------------------------------------------------------

def test_read_nonexistent_returns_none(ws):
    """Reading a missing file returns None."""
    assert ws.read("state") is None
    assert ws.read("decisions") is None


# ---------------------------------------------------------------------------
# 3. test_write_and_read
# ---------------------------------------------------------------------------

def test_write_and_read(ws):
    """Write state, read it back, content matches."""
    content = "# Project State\n\nAll good."
    ws.write("state", content)
    assert ws.read("state") == _hdr("state", content)


# ---------------------------------------------------------------------------
# 4. test_append_creates_then_appends
# ---------------------------------------------------------------------------

def test_append_creates_then_appends(ws):
    """Append to nonexistent creates file; second append adds content."""
    ws.append("decisions", "Decision 1\n")
    assert ws.read("decisions") == "Decision 1\n"

    ws.append("decisions", "Decision 2\n")
    assert ws.read("decisions") == "Decision 1\nDecision 2\n"


# ---------------------------------------------------------------------------
# 5. test_read_all_mixed
# ---------------------------------------------------------------------------

def test_read_all_mixed(ws):
    """Some files exist, some don't -- correct dict with None for missing.

    The roster is now the Layer-1 redesign set: state, decisions, lessons,
    index plus the two archives. "session_log"/"context" are gone; read_all
    spans all six keys (archives included).
    """
    ws.write("state", "state content")
    ws.write("lessons", "lessons content")

    result = ws.read_all()

    assert result["state"] == _hdr("state", "state content")
    assert result["lessons"] == _hdr("lessons", "lessons content")
    assert result["decisions"] is None
    # index replaces the retired "context" key.
    assert result["index"] is None
    # read_all now spans the archives too.
    assert result["decisions_archive"] is None
    assert result["lessons_archive"] is None
    assert result["state_archive"] is None
    assert set(result) == {
        "state", "decisions", "lessons",
        "index", "decisions_archive", "lessons_archive", "state_archive",
    }
    assert len(result) == 7


# ---------------------------------------------------------------------------
# 6./7. build_cold_resume_context tests — DELETED 2026-10-20 with the method:
# the Layer-1 rows injected every turn are the resume context.
#
# 8. test_session_log_truncation — DELETED.
#
# SESSION_LOG.md and its last-3-sessions resume truncation are a genuinely
# removed feature in the Layer-1 memory redesign (no "session_log" key, no
# _truncate_session_log, build_cold_resume_context no longer special-cases it).
# Nothing about this test's intent survives the removal, so it is dropped
# rather than re-expressed. The roster/resume-no-session-log invariants are
# covered by tests/regression/test_layer1_memory_redesign.py.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 9-12. The session-end routine is the Memory session's turn (spec 124 §3.7)
# ---------------------------------------------------------------------------

def _turn_result():
    from agent_os.agent import memory_editor
    return memory_editor.TurnResult(mode="disk", llm_calls=1, tool_calls=1, ended="text_complete")


@pytest.mark.asyncio
async def test_session_end_routine_lands_the_turns_archive_move(tmp_path):
    from agent_os.agent import state_blocks
    from agent_os.agent import workspace_files as wsf
    ws = WorkspaceFileManager(str(tmp_path))
    ws.write("state", "## Now\n- Old launch plan.\n- Current focus.\n")
    old_id = [ln for ln in ws.read("state").split("\n") if "<!--mem id:" in ln][0].split("id:")[1].split()[0]
    session = _mock_session([], session_id="sess_editor")

    async def run_turn(task_text):
        live = ws.read("state")
        b = [b for b in state_blocks.parse(live)[1] if b.id == old_id][0]
        wsf._append_archive_section(ws, "state_archive", "## [archived 2026-10-21]", b.raw)
        ws.write("state", live.replace(b.raw + "\n", ""))
        return _turn_result()

    out = await run_session_end_routine(
        session, None, ws, session_uuid=session.session_uuid, force=True, run_turn=run_turn)

    assert out == "edited"
    state = ws.read("state")
    assert "- Old launch plan." not in state and "- Current focus." in state
    assert "[archived " not in state and f"id:{old_id}" not in state
    archive = ws.read("state_archive")
    assert "- Old launch plan." in archive and f"id:{old_id}" in archive
    assert f"- id:{old_id} · archived " in archive        # contents table
    assert "PROJECT_STATE_ARCHIVE.md — 1 archived entry" in ws.read("index")
    assert "session_log" not in FILE_NAMES


@pytest.mark.asyncio
async def test_session_end_routine_turn_failure(tmp_path, caplog):
    """A failing turn: nothing is edited, the floor still runs, and the
    outcome says only the backstop ran."""
    ws = WorkspaceFileManager(str(tmp_path))
    ws.write("state", "original state")
    session = _mock_session([{"role": "user", "content": "Hello"}], session_id="sess_bad_json")

    async def run_turn(task_text):
        raise RuntimeError("the turn failed")

    with caplog.at_level(logging.WARNING):
        out = await run_session_end_routine(
            session, None, ws, session_uuid=session.session_uuid, force=True, run_turn=run_turn)

    assert out == "backstop_only"
    assert ws.read("state") == _hdr("state", "original state")
    assert ws.read("decisions") is None
    assert ws.read("lessons") is None
    assert "the turn failed" in caplog.text


@pytest.mark.asyncio
async def test_session_end_routine_never_regenerates_files(tmp_path):
    """No reply shape can replace a whole file: the turn's only way to change
    memory is the agent's own edit/write; a turn that writes nothing changes
    nothing."""
    ws = WorkspaceFileManager(str(tmp_path))
    ws.write("state", "# State\nDoing well.")
    session = _mock_session([{"role": "user", "content": "Go"}], session_id="sess_whole")

    async def run_turn(task_text):
        assert "Tidy this project's memory." in task_text
        return _turn_result()

    out = await run_session_end_routine(
        session, None, ws, session_uuid=session.session_uuid, force=True, run_turn=run_turn)

    assert out == "no_change"
    assert ws.read("state") == _hdr("state", "# State\nDoing well.")
    assert ws.read("decisions") is None
    assert ws.read("lessons") is None


# ---------------------------------------------------------------------------
# Extra: test exists
# ---------------------------------------------------------------------------

def test_exists(ws):
    """exists() returns True only for files that are present."""
    assert ws.exists("state") is False
    ws.write("state", "content")
    assert ws.exists("state") is True


# ---------------------------------------------------------------------------
# Extra: the providers passed to the routine are not used — the turn runs on
# the project's own provider inside the Memory session (spec 124 §3.7, no
# utility-provider split).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_end_runs_on_the_memory_session_not_the_passed_providers(tmp_path):
    ws = WorkspaceFileManager(str(tmp_path))
    ws.write("state", "state")

    session = _mock_session([{"role": "user", "content": "Hi"}], session_id="sess_util_prov")
    main_provider = _mock_provider("should not be called")
    utility_provider = _mock_provider("should not be called either")
    turns = []

    async def run_turn(task_text):
        turns.append(task_text)
        return _turn_result()

    await run_session_end_routine(
        session, main_provider, ws,
        utility_provider=utility_provider,
        session_uuid=session.session_uuid,
        force=True,
        run_turn=run_turn,
    )

    assert len(turns) == 1
    utility_provider.complete.assert_not_called()
    main_provider.complete.assert_not_called()
    assert ws.read("state") == _hdr("state", "state")


# ---------------------------------------------------------------------------
# Extra: test invalid file_key
# ---------------------------------------------------------------------------

def test_read_invalid_key(ws):
    """Invalid file_key raises ValueError."""
    with pytest.raises(ValueError, match="Unknown file_key"):
        ws.read("nonexistent_key")


def test_write_invalid_key(ws):
    """Invalid file_key raises ValueError."""
    with pytest.raises(ValueError, match="Unknown file_key"):
        ws.write("bad_key", "content")


def test_no_cold_resume_builder(ws):
    """build_cold_resume_context was removed with the cold-resume row (2026-10-20)."""
    assert not hasattr(ws, "build_cold_resume_context")


# ---------------------------------------------------------------------------
# Spec 141 §1 A + B — a GBK tail must not brick reads; Orbital's own memory
# files self-heal to UTF-8 after one read.
# ---------------------------------------------------------------------------

_141_HEAD = "# Project State\n" + "状态：进行中 — all good so far.\n" * 60
_141_TAIL = "大量中文\n"


@pytest.fixture
def _windows_cp936(monkeypatch):
    from agent_os.utils import text_io
    text_io._reset_warned_for_tests()
    monkeypatch.setattr(text_io, "_is_windows", lambda: True)
    monkeypatch.setattr(text_io, "_preferred_encoding", lambda: "cp936")
    yield
    text_io._reset_warned_for_tests()


def _write_mixed(path):
    head = _141_HEAD.encode("utf-8")
    with open(path, "wb") as f:
        f.write(head + _141_TAIL.encode("gbk"))


def test_read_mixed_gbk_tail_decodes_both_halves_and_heals(ws, ws_dir, _windows_cp936):
    os.makedirs(ws_dir, exist_ok=True)
    path = os.path.join(ws_dir, "PROJECT_STATE.md")
    _write_mixed(path)

    text = ws.read("state")

    assert text == _141_HEAD + _141_TAIL
    # B: the file on disk is valid UTF-8 afterwards, with the same text.
    # Line endings follow ``write`` (text-mode ``open``: CRLF on Windows).
    with open(path, "rb") as f:
        healed = f.read().decode("utf-8").replace("\r\n", "\n")
    assert healed == _141_HEAD + _141_TAIL
    assert not os.path.exists(path + ".tmp")


def test_read_heal_happens_once_then_fast_path(ws, ws_dir, _windows_cp936, caplog):
    os.makedirs(ws_dir, exist_ok=True)
    path = os.path.join(ws_dir, "DECISIONS.md")
    _write_mixed(path)
    with caplog.at_level(logging.WARNING, logger="agent_os.utils.text_io"):
        first = ws.read("decisions")
        second = ws.read("decisions")
    assert first == second == _141_HEAD + _141_TAIL
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_read_utf8_file_is_not_rewritten(ws, ws_dir):
    os.makedirs(ws_dir, exist_ok=True)
    path = os.path.join(ws_dir, "LESSONS.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("1. lesson\n")
    before = os.stat(path).st_mtime_ns
    assert ws.read("lessons") == "1. lesson\n"
    assert os.stat(path).st_mtime_ns == before
