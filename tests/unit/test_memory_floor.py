# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The size floor after spec 124: it never moves an entry.

Spec 089's floor archived PROJECT_STATE bullets older than 14 days and the
coldest DECISIONS / LESSONS entries, each leaving a pointer line. On the
flagship project the pointers alone grew past the file's budget (spec 124
§1.4), and age never said whether a decision was still true. Archiving is
now a judgement that only the Memory session makes; the daemon measures,
logs, and lets the injected view fold what does not fit. INDEX keeps its
tail trim (no entries, no archive), with every ``*_ARCHIVE.md`` line pinned.
"""

import logging

from agent_os.agent import memory_entries as M
from agent_os.agent import state_blocks
from agent_os.agent.workspace_files import WorkspaceFileManager, _apply_hard_caps


TODAY = "2026-09-18"


def _bullet(text, eid, created):
    return f"- {text}\n  <!--mem id:{eid} created:{created} touched:{created}-->"


def _state(*bullets, heading="## Now"):
    return heading + "\n" + "\n".join(bullets) + "\n"


# ---------------------------------------------------------------------------
# legacy pointer lines are still recognised (rule N5 folds them)
# ---------------------------------------------------------------------------

def test_legacy_pointer_lines_are_recognised_with_and_without_ids():
    assert state_blocks.pointer_date(
        "[archived 2026-09-19 id:c9bf60] GOAI booth prep → PROJECT_STATE_ARCHIVE.md") == "2026-09-19"
    assert state_blocks.pointer_date(
        "[archived 2026-07-28] Old stance → DECISIONS_ARCHIVE.md") == "2026-07-28"
    assert state_blocks.pointer_date("- a bullet") is None


# ---------------------------------------------------------------------------
# the floor moves nothing — whatever the age, whatever the overshoot
# ---------------------------------------------------------------------------

def test_replay_recent_booth_prep_survives_and_so_does_everything_old(tmp_path, caplog, monkeypatch):
    """2026-09-18: the old position-based trim archived the GOAI booth prep
    four days before the event. Now nothing leaves a live file but by a
    Memory session's choice."""
    monkeypatch.setitem(M.FILE_BUDGETS, "state", {"soft": 40, "hard": 60})
    wf = WorkspaceFileManager(str(tmp_path))
    old = _bullet("old stuff " + "o" * 3000, "old777", "2026-01-01")
    booth = _bullet("GOAI booth prep — posters printed " + "b" * 400, "booth1", TODAY)
    wf.write("state", _state(old, booth))
    before = wf.read("state")
    with caplog.at_level(logging.INFO):
        over = _apply_hard_caps(wf)
    assert over == ["state"]
    assert wf.read("state") == before
    assert not wf.exists("state_archive")
    assert any("over its hard cap" in r.getMessage() for r in caplog.records)


def test_floor_leaves_idless_and_prose_files_alone_too(tmp_path, monkeypatch):
    monkeypatch.setitem(M.FILE_BUDGETS, "decisions", {"soft": 40, "hard": 60})
    wf = WorkspaceFileManager(str(tmp_path))
    wf.ensure_dir()
    with open(wf._file_path("decisions"), "w", encoding="utf-8") as f:   # an external writer
        f.write("# D\n\n## Old\n" + "".join(f"filler prose {i} " + "p" * 50 + "\n" for i in range(40)))
    before = wf.read("decisions")
    _apply_hard_caps(wf)
    assert wf.read("decisions") == before
    assert not wf.exists("decisions_archive")


def test_floor_returns_nothing_for_a_fitting_project(tmp_path):
    wf = WorkspaceFileManager(str(tmp_path))
    wf.write("state", _state(_bullet("short", "aaaaaa", "2026-01-01")))
    wf.write("decisions", "# D\n- a small decision.\n")
    assert _apply_hard_caps(wf) == []


# ---------------------------------------------------------------------------
# the state view strips comments (all three files do now)
# ---------------------------------------------------------------------------

def test_state_view_everything_fits_shows_everything_without_comments():
    content = _state(_bullet("a", "a1", "2026-09-01"), _bullet("b", "b2", "2026-09-02"))
    view = M.inject_view(content, "state", 1000)
    assert "<!--mem" not in view and "- a" in view and "- b" in view


def test_state_view_over_budget_keeps_every_bullet_line():
    content = _state(*[
        _bullet(f"line {i} " + "x" * 30, f"id{i:04d}", "2026-09-01") + "\n  detail " + "d" * 80
        for i in range(30)
    ])
    view = M.inject_view(content, "state", 400)
    assert view.count("- line ") == 30
    assert "<!--mem" not in view
    assert M.inject_view(content, "state", 400) == view


# ---------------------------------------------------------------------------
# INDEX trim (unchanged): archive lines are pinned
# ---------------------------------------------------------------------------

def test_trim_volatile_pins_archive_pointer_lines():
    index = "\n".join(f"- f{i}.md — x" for i in range(300))
    index += "\n- orbital/LESSONS_ARCHIVE.md — 12 archived entries: to be described. Contents table at the top; grep id:<id>."
    out = M.trim_volatile(index, 80)
    assert "LESSONS_ARCHIVE.md" in out
    assert "f299.md" not in out


def test_trim_volatile_without_pointers_is_the_legacy_head_trim():
    index = "\n".join(f"- f{i}.md — x" for i in range(300))
    out = M.trim_volatile(index, 50)
    assert out.endswith(M._TRIM_NOTE)
    assert "- f0.md" in out


def test_hard_caps_trim_index_only(tmp_path, monkeypatch):
    monkeypatch.setitem(M.FILE_BUDGETS, "index", {"soft": 100, "hard": 150})
    wf = WorkspaceFileManager(str(tmp_path))
    wf.write("index", "# INDEX\n" + "".join(f"- f{i}.md — x\n" for i in range(200))
             + "- orbital/DECISIONS_ARCHIVE.md — 3 archived entries: pricing. Contents table at the top; grep id:<id>.\n")
    _apply_hard_caps(wf)
    index = wf.read("index")
    assert "DECISIONS_ARCHIVE.md" in index and "f199.md" not in index
