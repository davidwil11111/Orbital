# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests: a complete overflow lifecycle for all four Layer-1 files.

Incident (orbital-marketing, 2026-07-27). Overflow trimming of PROJECT_STATE
kept every ``[user]``-flagged bullet and deleted the unflagged prose around
them. What survived was::

    - [user] **选方案 A / B / C**（默认 A）？

and what was destroyed was the briefing that made it answerable. The fix then
was section-level flag protection; spec 089 replaced it with an age-based
floor; spec 124 removed the floor altogether: the daemon never moves an
entry, only a Memory session does, by judgement. The injected view folds
what does not fit instead.

  1. Nothing is ever cut by the daemon, whatever its age.
  2. PROJECT_STATE's archive is ``PROJECT_STATE_ARCHIVE.md`` — never
     injected, never budgeted, read on demand — and INDEX carries one line
     per archive.

Plus: INDEX's archive lines make every archive discoverable, and its
``LESSONS_ARCHIVE`` line was the LAST line of the file — first to go under a
tail-dropping trim, which would strand the archive on disk. Those lines are
pinned.
"""

from __future__ import annotations

import pytest

from agent_os.agent import memory_entries as _mem
from agent_os.agent.workspace_files import FILE_NAMES, WorkspaceFileManager


# The shape that lost the data: a heading, prose and key facts, a blank line,
# then the flagged questions those facts exist to support.
BRIEFED_SECTION = """\
# Project State

## Hero GIF discussion (2026-07-27)
claude-code recommends option A: relay layout, 0-9 lines.

**Key facts:**
- all three options need no on-camera presence
- cost: hero 7.5-9.5h, video 11-14h, total 19-24h
- option B has a competitor-bashing risk and is not reproducible
- option C is downgraded to act three, not the hero

**Decisions needed:**

- [user] **pick option A / B / C** (default A)?
- [user] **video length 5 / 8 / 12 min** (default 8)?
"""


def _old_bullets(n: int, created: str = "2026-01-01") -> str:
    return "".join(
        f"- old item {i} " + "o" * 600 + f"\n  <!--mem id:o{i:05d} created:{created} touched:{created}-->\n"
        for i in range(n)
    )


# ---------------------------------------------------------------------------
# 1. A recent briefing is never cut — nor is prose, whatever its age
# ---------------------------------------------------------------------------

def test_recent_briefing_and_its_questions_survive_the_floor(tmp_path, monkeypatch):
    """The exact regression shape, written today: the floor may not touch it,
    however far over target the file is."""
    monkeypatch.setitem(_mem.FILE_BUDGETS, "state", {"soft": 5, "hard": 8})
    ws = WorkspaceFileManager(str(tmp_path))
    ws.write("state", BRIEFED_SECTION)       # the chokepoint stamps created=today
    content = ws.read("state")
    from agent_os.agent import workspace_files as wsf
    assert wsf._apply_hard_caps(ws) == ["state"]
    assert ws.read("state") == content
    assert "pick option A / B / C" in content


def test_old_bullets_prose_and_headings_are_never_floor_candidates(tmp_path, monkeypatch):
    monkeypatch.setitem(_mem.FILE_BUDGETS, "state", {"soft": 5, "hard": 8})
    ws = WorkspaceFileManager(str(tmp_path))
    ws.write("state", "# S\n\n## Old\n" + _old_bullets(20))
    content = ws.read("state")
    from agent_os.agent import workspace_files as wsf
    wsf._apply_hard_caps(ws)
    assert ws.read("state") == content
    assert not ws.exists("state_archive")


# ---------------------------------------------------------------------------
# 2. PROJECT_STATE has a Layer-2 archive — overflow MOVES, never deletes
# ---------------------------------------------------------------------------

def test_state_has_an_archive_destination():
    assert _mem.ARCHIVE_OF["state"] == "state_archive"
    assert FILE_NAMES["state_archive"] == "PROJECT_STATE_ARCHIVE.md"


def test_archives_are_layer2_never_injected():
    """Layer 2 = read-on-demand. An archive with a budget would be injected
    every turn, which defeats the point of demoting to it."""
    for archive_key in _mem.ARCHIVE_OF.values():
        assert archive_key not in _mem.FILE_BUDGETS


def test_state_overflow_only_folds_the_view_and_moves_nothing(tmp_path):
    ws = WorkspaceFileManager(str(tmp_path))
    ws.ensure_dir()
    doomed = "UNIQUE-SENTINEL-OLD-BULLET-THAT-MUST-SURVIVE " + "d" * 300
    ws.write("state", "# S\n\n## Old\n- " + doomed
             + "\n  <!--mem id:dm0001 created:2025-12-01 touched:2025-12-01-->\n"
             + _old_bullets(20))

    from agent_os.agent import workspace_files as wsf
    wsf._apply_hard_caps(ws)

    live = ws.read("state") or ""
    assert "- " + doomed in live and "id:dm0001" in live
    assert not ws.exists("state_archive")
    view = _mem.inject_view(live, "state", _mem.FILE_BUDGETS["state"]["hard"])
    assert "more entries in this section not shown" in view
    assert "## Old" in view


def test_state_archive_gets_an_index_line(tmp_path):
    ws = WorkspaceFileManager(str(tmp_path))
    ws.ensure_dir()
    from agent_os.agent import workspace_files as wsf
    wsf._append_archive_section(ws, "state_archive", "## [archived 2026-09-19]", _old_bullets(1))
    wsf.refresh_index_archive_lines(ws)
    index = ws.read("index") or ""
    assert "- orbital/PROJECT_STATE_ARCHIVE.md — 1 archived entry, 2026-09-19 to 2026-09-19: to be described." in index


# ---------------------------------------------------------------------------
# 3. INDEX pointers are pinned — an archive can never be orphaned
# ---------------------------------------------------------------------------

def test_archive_pointers_survive_a_tail_dropping_trim():
    """On the real project the LESSONS_ARCHIVE pointer was line 81 of 81 —
    the first thing a tail-drop destroys. Losing it leaves the archive on
    disk and invisible to the agent."""
    index = (
        "# INDEX\n"
        + "".join(f"- orbital/f{i}.md — filler entry {i}\n" for i in range(300))
        + "- DECISIONS_ARCHIVE.md — superseded decisions (read on demand).\n"
        + "- LESSONS_ARCHIVE.md — older entries demoted from the live file.\n"
    )
    out = _mem.trim_volatile(index, 100)
    assert len(out) < len(index)                     # it did trim
    assert "DECISIONS_ARCHIVE.md" in out
    assert "LESSONS_ARCHIVE.md" in out


def test_pointer_pinning_does_not_protect_ordinary_lines():
    index = "# INDEX\n" + "".join(f"- orbital/f{i}.md — filler {i}\n" for i in range(300))
    out = _mem.trim_volatile(index, 50)
    assert "orbital/f299.md" not in out


# ---------------------------------------------------------------------------
# 4. Every Layer-1 file ends up with a defined overflow destination
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["state", "decisions", "lessons", "index"])
def test_every_layer1_file_has_a_defined_overflow_behaviour(key):
    """INDEX is the one file with no archive — it is regenerable navigation,
    so stale pointers are noise rather than history. Every other file moves
    its overflow somewhere readable."""
    assert key in _mem.FILE_BUDGETS
    assert _mem.consolidation_target(key) < _mem.FILE_BUDGETS[key]["soft"]
    if key == "index":
        assert key not in _mem.ARCHIVE_OF
    else:
        assert _mem.ARCHIVE_OF[key] in FILE_NAMES


# ---------------------------------------------------------------------------
# 5. The <!--format--> contract is scaffolding, not content — it must not
#    consume the file's own budget.
#
#    The contract is code-owned: we inject it, the user never wrote it, and the
#    agent cannot remove it. Counting it against the budget means every rail we
#    add to the contract silently steals space from real project memory. The
#    PROJECT_STATE header reached 498 tokens — 46% of that file's 1080
#    consolidation target — which left 582 tokens for actual content and put a
#    real project permanently over budget with no way down.
#
#    This is a HYGIENE budget only. Compaction fires on the provider's reported
#    usage (ContextManager.should_compact -> _last_usage_pct), and context-window
#    math runs through budgets_for_window/inject_view, which measure the real
#    injected text. Excluding the header here cannot undercount either.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["state", "decisions", "lessons", "index"])
def test_format_header_does_not_consume_the_files_budget(key):
    header = _mem.FORMAT_HEADERS[key]
    body = "- a real line of project content\n"
    with_header = header + "\n" + body

    assert _mem.est_tokens(_mem._budget_text(with_header, key)) == pytest.approx(
        _mem.est_tokens(_mem._budget_text(body, key)), abs=1.0
    )


def test_header_exclusion_is_worth_a_real_headroom_gain():
    """Concretely: PROJECT_STATE gets its whole target back for content."""
    # Guards re-pinned when spec 089 moved the [user] grammar out of the
    # header (~500 -> ~260 tok): still a fifth of the target.
    header_tokens = _mem.est_tokens(_mem.FORMAT_HEADERS["state"])
    assert header_tokens > 200, "guard: the header is genuinely large"
    target = _mem.consolidation_target("state")
    assert header_tokens / target > 0.2, "guard: it was a large share of the target"

    padded = _mem.FORMAT_HEADERS["state"] + "\n" + ("- content line\n" * 50)
    counted = _mem.est_tokens(_mem._budget_text(padded, "state"))
    assert counted < target, "content of this size must now fit the target"


def test_mem_comments_are_still_excluded_for_state():
    """The pre-existing exclusion must survive the new one."""
    body = "- [user] a line\n  <!--mem id:abc created:2026-07-01 touched:2026-07-01-->\n"
    assert "abc" not in _mem._budget_text(body, "state")
