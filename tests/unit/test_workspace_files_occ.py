# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests: concurrent writes during a memory pass.

Under spec 124 §3.7 the pass shell writes no live file itself: the Memory
session edits with the exact-match ``edit`` tool (OCC by construction — a
file that changed under it makes the edit fail, and the agent re-reads), and
the shell only puts back entries that left a live file without reaching the
archive and keeps INDEX's archive lines. So a user's or another writer's
edit that lands while the turn runs is still on disk afterwards.

``_occ_write_metadata`` (the mtime-gated metadata write) is still a helper
for callers that write whole files; its abort message must say WHO wrote.
"""

import logging
import os
from unittest.mock import MagicMock

import pytest

from agent_os.agent import memory_editor
from agent_os.agent import memory_entries as _mem
from agent_os.agent import state_blocks
from agent_os.agent import workspace_files as wsf_module
from agent_os.agent.workspace_files import (
    WorkspaceFileManager,
    run_session_end_routine,
)

TODAY = "2026-10-21"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_session(session_id="sess_occ"):
    session = MagicMock()
    session.session_id = session_id
    session.session_uuid = session_id
    session.get_messages.return_value = []
    return session


def _seed(ws) -> None:
    """Four Layer-1 files, each with one entry the turn will archive."""
    ws.write("state", "## Now\n- state entry to archive\n- state entry to keep\n")
    ws.write("decisions", (
        "- Keep A\n  <!--mem id:keep-a created:2026-08-01 touched:2026-08-01-->\n  x\n\n"
        "- Keep B\n  <!--mem id:keep-b created:2026-08-01 touched:2026-08-01-->\n  x\n\n"
        "- Archive me\n  <!--mem id:dec-go created:2026-08-01 touched:2026-08-01-->\n  old\n\n"
    ))
    ws.write("lessons", (
        "- Keep this.\n  <!--mem id:les-keep created:2026-08-01 touched:2026-08-01-->\n"
        "- Archive this.\n  <!--mem id:les-go created:2026-08-01 touched:2026-08-01-->\n"
    ))
    ws.write("index", "# INDEX\n- a.md — a\n")


def _archive(ws, key, eid):
    live = ws.read(key)
    b = [b for b in state_blocks.parse(live)[1] if b.id == eid][0]
    wsf_module._append_archive_section(ws, _mem.ARCHIVE_OF[key], f"## [archived {TODAY}]", b.raw)
    ws.write(key, live.replace(b.raw + "\n", ""))


def _turn_that(ws, during=None):
    """A Memory turn that archives one entry per file and rewrites INDEX,
    after ``during`` (the concurrent writer) has run."""
    async def run_turn(task_text):
        if during is not None:
            during()
        state_id = ws.read("state").split("id:")[1].split()[0]
        _archive(ws, "state", state_id)
        _archive(ws, "decisions", "dec-go")
        _archive(ws, "lessons", "les-go")
        ws.write("index", "# INDEX\n- b.md — b\n")
        return memory_editor.TurnResult(mode="disk", llm_calls=2, tool_calls=7, ended="text_complete")
    return run_turn


@pytest.fixture(autouse=True)
def _reset_module_state():
    wsf_module._completed_session_ends.clear()
    memory_editor._RUNNING.clear()
    yield
    wsf_module._completed_session_ends.clear()


async def _run(ws, run_turn, sid, project_id):
    return await run_session_end_routine(
        _mock_session(sid), None, ws,
        session_uuid=sid, project_id=project_id, force=True, run_turn=run_turn,
    )


# ---------------------------------------------------------------------------
# 1. clean baseline → every move lands
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clean_baseline_writes_all_layer1_files(tmp_path):
    ws = WorkspaceFileManager(str(tmp_path))
    _seed(ws)
    out = await _run(ws, _turn_that(ws), "sess_clean", "proj_clean")
    assert out == "edited"
    assert "state entry to archive" not in ws.read("state")
    assert "- Archive me" not in ws.read("decisions")
    assert "Archive this." not in ws.read("lessons")
    assert "- b.md — b" in ws.read("index")
    for key in ("state", "decisions", "lessons"):
        assert "- id:" in (ws.read(_mem.ARCHIVE_OF[key]) or "")


# ---------------------------------------------------------------------------
# 2. a user edit during the turn, on a file the turn then edits exactly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_user_edit_during_the_turn_survives_on_a_file_the_turn_edits(tmp_path):
    """The turn's moves are exact-match edits (here: replace the block), so
    a line the user added meanwhile stays; nothing is half-moved."""
    ws = WorkspaceFileManager(str(tmp_path))
    _seed(ws)

    def user_edit():
        with open(ws._file_path("state"), "a", encoding="utf-8") as f:
            f.write("## USER ADDED\nhand-written note\n")

    out = await _run(ws, _turn_that(ws, during=user_edit), "sess_edit_state", "proj_edit")
    assert out == "edited"
    state = ws.read("state")
    assert "hand-written note" in state and "state entry to archive" not in state
    assert "- id:" in ws.read("state_archive")


# ---------------------------------------------------------------------------
# 3. a user edit on a file the turn leaves alone is untouched by the shell
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_file_the_turn_does_not_touch_keeps_the_users_edit(tmp_path):
    ws = WorkspaceFileManager(str(tmp_path))
    _seed(ws)
    user_lessons = ws.read("lessons") + "- user-additional-lesson-during-turn\n"

    async def run_turn(task_text):
        with open(ws._file_path("lessons"), "w", encoding="utf-8") as f:
            f.write(user_lessons)
        _archive(ws, "decisions", "dec-go")
        return memory_editor.TurnResult(mode="disk", llm_calls=1, tool_calls=2, ended="text_complete")

    out = await _run(ws, run_turn, "sess_multi", "proj_multi")
    assert out == "edited"
    assert ws.read("lessons") == user_lessons
    assert "- Archive me" not in ws.read("decisions")


# ---------------------------------------------------------------------------
# 4. a file the user rewrote during the turn is not undone by the shell
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_user_rewrites_index_during_the_turn_and_keeps_it(tmp_path):
    ws = WorkspaceFileManager(str(tmp_path))
    ws.write("state", "## Now\n- a\n")
    user_content = "# INDEX\n- user-wrote-this-while-the-pass-was-running\n"

    async def run_turn(task_text):
        with open(ws._file_path("index"), "w", encoding="utf-8") as f:
            f.write(user_content)
        return memory_editor.TurnResult(mode="disk", llm_calls=1, tool_calls=0, ended="text_complete")

    out = await _run(ws, run_turn, "sess_create_race", "proj_create")
    assert out == "index_only"
    assert "user-wrote-this-while-the-pass-was-running" in ws.read("index")


# ---------------------------------------------------------------------------
# 5. a bare deletion mid-turn (no archive) is put back and logged
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_mid_turn_deletion_without_archive_is_restored_and_logged(tmp_path, caplog):
    ws = WorkspaceFileManager(str(tmp_path))
    _seed(ws)

    async def run_turn(task_text):
        live = ws.read("decisions")
        b = [b for b in state_blocks.parse(live)[1] if b.id == "keep-b"][0]
        with open(ws._file_path("decisions"), "w", encoding="utf-8") as f:
            f.write(live.replace(b.raw + "\n", ""))
        return memory_editor.TurnResult(mode="disk", llm_calls=1, tool_calls=1, ended="text_complete")

    with caplog.at_level(logging.WARNING, logger="agent_os.agent.workspace_files"):
        out = await _run(ws, run_turn, "sess_struct", "proj_struct_log")
    assert out == "restored"
    assert "id:keep-b" in ws.read("decisions")
    matching = [r for r in caplog.records if "restored=['keep-b']" in r.getMessage()]
    assert matching and matching[0].levelno == logging.WARNING


# ---------------------------------------------------------------------------
# Truth in the abort message (orbital-marketing, 2026-07-28/29).
#
# Every OCC abort blamed "user mid-edit detected". In all four observed cases
# the observed_mtime was the timestamp of a CONCURRENT CONSOLIDATION PASS's own
# write — no user touched anything. The distinction matters: a real mid-edit is
# the feature working, a self-collision is a bug, and they were indistinguishable
# in the log.
# ---------------------------------------------------------------------------

def test_occ_abort_names_a_concurrent_pass_when_the_daemon_made_the_write(tmp_path, caplog):
    wf = WorkspaceFileManager(str(tmp_path))
    wf.write("decisions", "## 2026-07-28: A\n")
    stale_baseline = 1  # anything that is not the current mtime

    with caplog.at_level(logging.WARNING):
        ok = wsf_module._occ_write_metadata(
            wf, "decisions", "## 2026-07-28: B\n", stale_baseline,
            project_id="proj_x",
        )

    assert ok is False
    msg = caplog.text
    assert "concurrent consolidation pass" in msg.lower(), msg
    assert "user mid-edit" not in msg.lower(), (
        "a daemon-authored write must not be reported as a user edit"
    )


def test_occ_abort_still_names_the_user_for_a_real_outside_edit(tmp_path, caplog):
    wf = WorkspaceFileManager(str(tmp_path))
    wf.write("decisions", "## 2026-07-28: A\n")
    # A writer that is NOT the daemon's write path touches the file. Bump the
    # mtime explicitly: on NTFS the append can land on the same timestamp tick
    # as the daemon write above, which reads as a self-collision.
    path = wf._file_path("decisions")
    with open(path, "a", encoding="utf-8") as f:
        f.write("## hand edit\n")
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))

    with caplog.at_level(logging.WARNING):
        ok = wsf_module._occ_write_metadata(
            wf, "decisions", "## 2026-07-28: B\n", 1, project_id="proj_x",
        )

    assert ok is False
    assert "user mid-edit" in caplog.text.lower(), caplog.text
