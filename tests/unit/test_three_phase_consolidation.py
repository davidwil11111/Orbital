# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests: the memory pass is two phases, and moving is by the agent's
own edit through the write chokepoint.

History. Measured on orbital-marketing (2026-07-27): asking one call to merge
AND archive made its response BIGGER, because archived entries had to come
back VERBATIM. That led to three phases (whole-file merge, archive by id,
deterministic floor). Spec 089 dropped the first: the whole-file merge
regenerated files from a size-capped view and silently lost what it never
saw. Spec 124 §3.7 replaced the editor's JSON choices with the Memory
session: a real session of the project that moves entries itself.

The pipeline now:

  P1 TURN   (LLM, the Memory session) sees the task message with measured
                                       sizes and ids, moves whole entries
                                       into the archive with edit/write (the
                                       daemon regenerates the contents table
                                       and the INDEX archive line).
  P2 FLOOR  (deterministic, always last) conservation + measure/log; it
                                       never moves an entry (spec 124 §3.5).
                                       INDEX only is tail-trimmed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent_os.agent import memory_editor
from agent_os.agent import memory_entries as _mem
from agent_os.agent import state_blocks
from agent_os.agent import workspace_files as wsf
from agent_os.agent.workspace_files import WorkspaceFileManager, run_session_end_routine

TODAY = "2026-10-21"


def _session(sid="s_phase"):
    s = MagicMock()
    s.session_id = sid
    s.session_uuid = sid
    s.get_messages.return_value = []
    return s


def _entries(n, *, key="decisions", pad=1100, tag=""):
    """n stamped entries, each big enough to matter against the budget."""
    out = []
    for i in range(1, n + 1):
        meta = f'<!--mem id:e{i:02d} created:2026-07-{i:02d} touched:2026-07-{i:02d}{" tag:" + tag if tag else ""}-->'
        if key == "decisions":
            out.append(f"- 2026-07-{i:02d}: Decision {i}\n  {meta}\n  **Chose:** {'x' * pad}\n\n")
        else:
            out.append(f"- Lesson {i}. {'y' * pad}\n  {meta}\n")
    return "".join(out)


def _over_budget_ws(tmp_path) -> WorkspaceFileManager:
    ws = WorkspaceFileManager(str(tmp_path))
    ws.ensure_dir()
    ws.write("decisions", _entries(26))
    ws.write("lessons", _entries(18, key="lessons"))
    return ws


def _turn(**fields) -> memory_editor.TurnResult:
    base = dict(mode="disk", llm_calls=1, tool_calls=1, ended="text_complete")
    base.update(fields)
    return memory_editor.TurnResult(**base)


def _archiver(ws, *ids, key="decisions"):
    """A Memory turn that archives ``ids`` the way the agent does: the whole
    block appended to the archive first, then the live file without it."""
    tasks = []

    async def run_turn(task_text):
        tasks.append(task_text)
        live = ws.read(key)
        blocks = {b.id: b for b in state_blocks.parse(live)[1] if b.id}
        for eid in ids:
            b = blocks.get(eid)
            if b is None:
                continue
            wsf._append_archive_section(ws, _mem.ARCHIVE_OF[key], f"## [archived {TODAY}]", b.raw)
            live = live.replace(b.raw + "\n", "")
        ws.write(key, live)
        return _turn(tool_calls=2 * len(ids))
    run_turn.tasks = tasks
    return run_turn


@pytest.fixture(autouse=True)
def _reset():
    wsf._completed_session_ends.clear()
    memory_editor._RUNNING.clear()
    yield
    wsf._completed_session_ends.clear()


# ---------------------------------------------------------------------------
# P1: the task message carries measured sizes; the turn moves whole entries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_message_carries_measured_sizes_and_the_over_budget_file(tmp_path):
    ws = _over_budget_ws(tmp_path)
    now = int(_mem.est_tokens(_mem._budget_text(ws.read("decisions"), "decisions")))
    run_turn = _archiver(ws)
    await run_session_end_routine(_session("s_ids"), None, ws, session_uuid="s_ids", run_turn=run_turn)
    task = run_turn.tasks[0]
    # the padding sits in indented detail, so DECISIONS' bullet lines are
    # within budget and the task says so with the measured count; LESSONS
    # (padding on the bullet line) is the over-budget file
    assert f"- DECISIONS.md: {now:,} (soft {_mem.FILE_BUDGETS['decisions']['soft']:,}) — within" in task
    lessons_now = int(_mem.est_tokens(_mem._budget_text(ws.read("lessons"), "lessons")))
    assert f"- LESSONS.md: {lessons_now:,} tokens (soft" in task and "— OVER" in task
    assert "LESSONS.md is over" in task and "DECISIONS.md is over" not in task


@pytest.mark.asyncio
async def test_the_turn_moves_bytes_exactly(tmp_path):
    ws = _over_budget_ws(tmp_path)
    before = ws.read("decisions")
    await run_session_end_routine(_session("s_bytes"), None, ws, session_uuid="s_bytes",
                                  run_turn=_archiver(ws, "e05"))
    archive = ws.read("decisions_archive") or ""
    entry = "- 2026-07-05: Decision 5" + before.split("- 2026-07-05: Decision 5", 1)[1].split("- 2026-07-06", 1)[0]
    assert entry.rstrip("\n") in archive, "archived text must be byte-identical"


@pytest.mark.asyncio
async def test_the_turn_leaves_nothing_in_the_live_file_and_the_daemon_names_the_archive_in_index(tmp_path):
    ws = _over_budget_ws(tmp_path)
    out = await run_session_end_routine(_session("s_stub"), None, ws, session_uuid="s_stub",
                                        run_turn=_archiver(ws, "e05"))
    assert out == "edited"
    live = ws.read("decisions")
    assert "id:e05" not in live and "[archived" not in live
    assert "- id:e05 · archived " in ws.read("decisions_archive")
    assert "- orbital/DECISIONS_ARCHIVE.md — 1 archived entry" in ws.read("index")


@pytest.mark.asyncio
async def test_a_deletion_that_never_reached_the_archive_comes_back(tmp_path):
    ws = _over_budget_ws(tmp_path)
    before_entries = ws.read("decisions").count("- 2026-07-")

    async def deleter(task_text):
        live = ws.read("decisions")
        b = [b for b in state_blocks.parse(live)[1] if b.id == "e05"][0]
        with open(ws._file_path("decisions"), "w", encoding="utf-8") as f:
            f.write(live.replace(b.raw + "\n", ""))
        return _turn()

    out = await run_session_end_routine(_session("s_bad"), None, ws, session_uuid="s_bad", run_turn=deleter)
    assert out == "restored"
    live = (ws.read("decisions") or "").count("- 2026-07-")
    assert live == before_entries, "a bare deletion must not lose entries"


# ---------------------------------------------------------------------------
# P2: the floor measures; it never moves an entry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_turn_failure_leaves_the_file_whole(tmp_path):
    ws = _over_budget_ws(tmp_path)
    before = ws.read("decisions")

    async def boom(task_text):
        raise RuntimeError("provider 500")
    outcome = await run_session_end_routine(_session("s_fail"), None, ws, session_uuid="s_fail", run_turn=boom)
    assert outcome == "backstop_only"
    assert ws.read("decisions") == before
    assert not ws.exists("decisions_archive")


@pytest.mark.asyncio
async def test_only_the_turns_moves_land_and_index_is_trimmed(tmp_path):
    """The turn's archive moves land; the floor moves nothing else (the
    old state bullets stay, however old); INDEX alone is tail-trimmed."""
    ws = _over_budget_ws(tmp_path)
    ws.write("state", "# S\n\n## Old\n" + "".join(
        f"- old item {i} " + "o" * 600
        + f"\n  <!--mem id:s{i:05d} created:2026-01-01 touched:2026-01-01-->\n"
        for i in range(20)
    ))
    ws.write("index", "# INDEX\n" + "".join(f"- f{i}.md — x\n" for i in range(900)))
    state_before = ws.read("state")

    await run_session_end_routine(_session("s_e2e"), None, ws, session_uuid="s_e2e",
                                  run_turn=_archiver(ws, *[f"e{i:02d}" for i in range(4, 12)]))

    live = ws.read("decisions")
    for i in range(4, 12):
        assert f"id:e{i:02d}" not in live
    assert ws.read("decisions_archive").count("- 2026-07-") == 8
    assert ws.read("state") == state_before
    assert not ws.exists("state_archive")
    index = ws.read("index")
    assert _mem.est_tokens(index) <= _mem.consolidation_target("index") + 60
    assert "DECISIONS_ARCHIVE.md" in index


@pytest.mark.asyncio
async def test_archive_write_failure_does_not_crash_or_lose_content(tmp_path, monkeypatch):
    """A failing archive write is a quality loss, never a crash and never a
    content loss: the turn's move fails before the live file shrinks."""
    ws = _over_budget_ws(tmp_path)
    before = ws.read("decisions")
    real_write = WorkspaceFileManager.write

    def _explode_on_archive(self, file_key, content):
        if file_key.endswith("_archive"):
            raise OSError("disk full")
        return real_write(self, file_key, content)

    monkeypatch.setattr(WorkspaceFileManager, "write", _explode_on_archive)
    outcome = await run_session_end_routine(_session("s_oserr"), None, ws, session_uuid="s_oserr",
                                            run_turn=_archiver(ws, "e05"))
    assert outcome == "backstop_only"
    after = ws.read("decisions") or ""
    assert after.count("- 2026-07-") == before.count("- 2026-07-")
    for i in range(1, 27):
        assert f"id:e{i:02d}" in after
