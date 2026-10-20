# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 124 §6 items 1–4, 9: ``normalise`` (rules N1–N5) and ``stamp`` (ids).

Fixtures under ``fixtures/memory_v2/`` are trimmed copies of five real
projects' memory files (each shape the audit found): marketing's numbered
LESSONS and ``## title <!--mem-->`` DECISIONS, orbital-test's ``## 2. Title``
LESSONS, Agent-collaboration's ``### date — slug`` LESSONS, launch-vid's
bullet LESSONS, Interactive-desktop's bullet DECISIONS, and PROJECT_STATE
files mixing paragraphs, pointer lines, bullets and orphan comments.
"""

from __future__ import annotations

import os
import re
from collections import Counter

import pytest

from agent_os.agent import memory_entries as M
from agent_os.agent import state_blocks
from agent_os.agent.workspace_files import WorkspaceFileManager

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "memory_v2")
TODAY = "2026-10-20"
_COMMENT = re.compile(r"<!--\s*mem\b.*?-->")


def _fixture(project: str, name: str) -> str:
    with open(os.path.join(FIXTURES, project, name), encoding="utf-8") as f:
        return f.read()


def _content_multiset(text: str) -> Counter:
    """Every non-blank line's content with markers, numbering, a leading
    bold span, indentation and mem-comments removed — the lossless invariant.
    Pointer lines are excluded (N5 moves them to the archive) and so is the
    pasted view note that one real file carried."""
    out: Counter = Counter()
    for ln in text.split("\n"):
        s = _COMMENT.sub("", ln).strip()
        if not s or s.startswith("<!--format") or state_blocks.pointer_date(s):
            continue
        if s.startswith("[... ") and " ...]" in s:
            s = s.split(" ...]", 1)[1].strip()
            if not s:
                continue
        s = re.sub(r"^(?:-|\d+[.)])\s+", "", s)
        s = re.sub(r"^#{1,6}\s+", "", s)
        s = re.sub(r"^\d+[.)]\s+", "", s)
        s = s.replace("**", "")
        out[" ".join(s.split())] += 1
    return out


def _stamped(project: str, name: str, key: str) -> tuple[str, M.NormaliseResult]:
    raw = _fixture(project, name)
    res = M.normalise(raw, key)
    if key == "state":
        from agent_os.agent import flag_chokepoint
        text, _ = flag_chokepoint.reconcile_flags(raw, res.text, TODAY)
    else:
        text, _ = M.stamp(res.text, raw, key, today=TODAY)
    return text, res


# Hand counts of the entries in each trimmed fixture (one bullet per entry
# after normalisation). orbital-test LESSONS: its third heading is numbered
# but carries no comment, and the two trailing top-level bullets follow its
# body without a blank line, so they stay its detail — 3 entries, not 5.
SHAPES = [
    ("orbital-marketing", "LESSONS.md", "lessons", 13),
    ("orbital-marketing", "DECISIONS.md", "decisions", 16),
    ("orbital-marketing", "PROJECT_STATE.md", "state", 9),
    ("orbital-test", "LESSONS.md", "lessons", 3),
    ("orbital-test", "DECISIONS.md", "decisions", 6),
    ("orbital-test", "PROJECT_STATE.md", "state", 7),
    ("Agent-collaboration", "LESSONS.md", "lessons", 14),
    ("Agent-collaboration", "DECISIONS.md", "decisions", 15),
    ("Agent-collaboration", "PROJECT_STATE.md", "state", 13),
    ("launch-vid", "LESSONS.md", "lessons", 19),
    ("launch-vid", "DECISIONS.md", "decisions", 17),
    ("launch-vid", "PROJECT_STATE.md", "state", 6),
    ("Interactive-desktop", "LESSONS.md", "lessons", 13),
    ("Interactive-desktop", "DECISIONS.md", "decisions", 18),
    ("Interactive-desktop", "PROJECT_STATE.md", "state", 15),
]


@pytest.mark.parametrize("project,name,key,expected", SHAPES, ids=[f"{p}/{n}" for p, n, _k, _e in SHAPES])
def test_real_shape_normalises_losslessly_idempotently_with_unique_ids(project, name, key, expected):
    raw = _fixture(project, name)
    text, res = _stamped(project, name, key)
    blocks = state_blocks.parse(text)[1]
    # one bullet per entry, hand-counted
    assert len(blocks) == expected
    # every entry has a unique id, never a bare "entry"
    ids = [b.id for b in blocks]
    assert all(ids) and len(set(ids)) == len(ids)
    assert "entry" not in ids
    # lossless: every line's content is still there (markers aside)
    assert _content_multiset(text) == _content_multiset(raw)
    # idempotent: a second pass is a byte-identical no-op with no warnings
    again = M.normalise(text, key)
    assert again.text == text and again.warnings == [] and again.pointers == []
    # and stamping the conforming text again changes nothing
    if key != "state":
        assert M.stamp(text, text, key, today="2027-01-01")[0] == text
    # no mem-comment anywhere but on the line under a bullet
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if M._is_comment_only(ln):
            assert state_blocks.is_bullet_line(lines[i - 1]), ln
        elif "<!--mem" in ln:
            pytest.fail(f"inline mem-comment left in text: {ln[:80]}")


def test_conforming_file_is_byte_identical():
    content = (
        M.FORMAT_HEADERS["decisions"] + "\n# DECISIONS\n\n## Pricing\n"
        "- Charge per seat, because usage is hard to explain.\n"
        "  <!--mem id:per-seat created:2026-05-01 touched:2026-05-01-->\n"
        "  Rejected: usage-based (unpredictable bills).\n"
        "- Free tier = 3 projects.\n"
        "  <!--mem id:free-tier-3 created:2026-07-01 touched:2026-07-01-->\n"
    )
    res = M.normalise(content, "decisions")
    assert res.text == content and res.warnings == [] and not res.changed
    assert M.stamp(content, content, "decisions", today=TODAY)[0] == content


def test_section_heading_with_top_level_bullets_is_not_an_entry():
    text, _ = _stamped("Interactive-desktop", "LESSONS.md", "lessons")
    assert "### 2026-10-08 — 原型 E v2 分层与验收\n- 互补裁切层" in text
    # ...while a heading whose first paragraph is prose became an entry
    assert "- 2026-10-03 — error-recovery\n  <!--mem id:" in text
    assert "### 2026-10-03" not in text


def test_marketing_decisions_heading_entries_keep_their_ids_and_split_at_bullets():
    text, _ = _stamped("orbital-marketing", "DECISIONS.md", "decisions")
    assert "- 2026-10-14：BP 首批用户、案例与融资口径\n  <!--mem id:bp created:2026-10-14 touched:2026-10-14-->\n  **Chose:**" in text
    # a bullet after a blank line is its own entry, not the heading's detail
    assert "\n- X 达人追踪链接 = 自家域名短链" in text
    assert "\n  **[2026-10-14 修订]**" in text        # ...its continuation stays with it
    assert "\n- 2026-10-13 Telemetry 修复延后" in text


def test_touched_is_not_bumped_by_a_format_only_rewrite():
    """Normalising is not an edit: hotness must not reset on the first pass."""
    text, _ = _stamped("orbital-marketing", "LESSONS.md", "lessons")
    assert "<!--mem id:attribution-before-narration created:2026-08-24 touched:2026-09-28-->" in text
    assert "<!--mem id:radar-user-visible-only created:2026-08-24 touched:2026-08-31-->" in text


# ---------------------------------------------------------------------------
# §1.3 regression: numbered playbook steps are detail, not entries
# ---------------------------------------------------------------------------

def test_playbook_steps_become_detail_and_the_entry_count_holds():
    old = "# LESSONS\n1. **Release playbook**\nSteps:\n1. bump\n2. tag\n3. publish\n2. **Other lesson**\nx\n"
    res = M.normalise(old, "lessons")
    stamped, _ = M.stamp(res.text, None, "lessons", today=TODAY)
    blocks = state_blocks.parse(stamped)[1]
    assert len(blocks) == 2
    assert blocks[0].content_lines[0] == "- Release playbook"
    assert [ln.strip() for ln in blocks[0].content_lines[1:]] == ["Steps:", "1. bump", "2. tag", "3. publish"]
    assert blocks[1].content_lines[0] == "- Other lesson"
    assert "1. bump" not in [b.text for b in blocks]


# ---------------------------------------------------------------------------
# §1.2 regression: ids
# ---------------------------------------------------------------------------

def test_two_chinese_titles_get_different_ids_never_bare_entry():
    s1, _ = M.stamp("# DECISIONS\n- 定价口径\n  body A\n", None, "decisions", today="2026-10-01")
    id1 = state_blocks.parse(s1)[1][0].id
    new = "# DECISIONS\n- 渠道选择\n  body B\n" + s1.split("# DECISIONS\n", 1)[1]
    s2, _ = M.stamp(new, s1, "decisions", today="2026-10-20")
    ids = [b.id for b in state_blocks.parse(s2)[1]]
    assert len(ids) == 2 and ids[0] != ids[1]
    assert "entry" not in ids
    assert ids[1] == id1                      # the existing one was not rewritten
    assert all(re.fullmatch(r"[a-z0-9-]+", i) for i in ids)


def test_new_entry_never_reuses_an_archived_id():
    archive = "## [archived 2026-10-01]\n\n- Keep API base url\n  <!--mem id:keep-api-base-url created:2026-06-15 touched:2026-06-15-->\n"
    stamped, _ = M.stamp("- Keep API base url\n", None, "decisions", today=TODAY,
                         archive_ids=M.archive_ids(archive))
    eid = state_blocks.parse(stamped)[1][0].id
    assert eid != "keep-api-base-url" and eid.startswith("keep-api-base-url")


def test_existing_bare_entry_id_is_kept():
    old = "- 老决定\n  <!--mem id:entry created:2026-10-01 touched:2026-10-01-->\n"
    stamped, _ = M.stamp(old, old, "decisions", today=TODAY)
    assert stamped == old


def test_duplicate_id_in_one_file_gets_a_fresh_second_id():
    text = (
        "- first\n  <!--mem id:same created:2026-10-01 touched:2026-10-01-->\n"
        "- second\n  <!--mem id:same created:2026-10-01 touched:2026-10-01-->\n"
    )
    stamped, warns = M.stamp(text, None, "lessons", today=TODAY)
    ids = [b.id for b in state_blocks.parse(stamped)[1]]
    assert ids[0] == "same" and ids[1] != "same" and warns


def test_comment_stripped_rewrite_keeps_ids_by_exact_text():
    old = (
        "- Charge per seat.\n  <!--mem id:per-seat created:2026-05-01 touched:2026-05-01-->\n"
        "- Free tier = 3 projects.\n  <!--mem id:free created:2026-07-01 touched:2026-07-01-->\n"
    )
    new = "- Free tier = 3 projects.\n- Charge per seat.\n  Rejected: usage-based.\n"
    stamped, _ = M.stamp(new, old, "decisions", today=TODAY)
    blocks = state_blocks.parse(stamped)[1]
    assert [b.id for b in blocks] == ["free", "per-seat"]
    assert blocks[0].fields["touched"] == "2026-07-01"     # unchanged text
    assert blocks[1].fields["touched"] == TODAY             # detail added
    assert blocks[1].fields["created"] == "2026-05-01"


# ---------------------------------------------------------------------------
# N4 / N5
# ---------------------------------------------------------------------------

def test_orphan_and_in_text_mem_comments_are_dropped():
    text = (
        "## Now\n  <!--mem id:orphan1 created:2026-10-13 touched:2026-10-13-->\n"
        "- real line\n  <!--mem id:keep01 created:2026-10-13 touched:2026-10-13-->\n"
        "  <!--mem id:stacked created:2026-10-12 touched:2026-10-12-->\n"
        "[archived 2026-10-11 id:radar1010] Release radar → PROJECT_STATE_ARCHIVE.md\n"
        "  <!--mem id:8b54bf created:2026-10-10 touched:2026-10-10-->\n"
        "- another <!--mem id:inline1 created:2026-10-13 touched:2026-10-13-->\n"
    )
    res = M.normalise(text, "state")
    assert "orphan1" not in res.text and "stacked" not in res.text and "8b54bf" not in res.text
    assert set(res.dropped_comment_ids) == {"orphan1", "stacked", "8b54bf"}
    assert "- another\n  <!--mem id:inline1" in res.text            # inline → next line
    assert "- real line\n  <!--mem id:keep01" in res.text
    assert len(res.pointers) == 1 and res.pointers[0].ids == ["radar1010"]


def test_legacy_pointers_fold_into_the_archive_archive_first(tmp_path):
    ws = WorkspaceFileManager(str(tmp_path))
    ws.ensure_dir()
    ws.write("state_archive", (
        "# PROJECT_STATE_ARCHIVE.md\n\n## [archived 2026-10-01]\n\n- old thing\n"
        "  <!--mem id:known1 created:2026-09-01 touched:2026-09-01-->\n"
    ))
    state = (
        "## Now\n- live line\n"
        "[archived 2026-10-01 id:known1] old thing → PROJECT_STATE_ARCHIVE.md\n"
        "[archived 2026-08-03] Hard rule #1 — read agent_output/x.md before scope work → PROJECT_STATE_ARCHIVE.md\n"
    )
    ws.write("state", state)
    live = ws.read("state")
    archive = ws.read("state_archive")
    assert "[archived" not in live and "- live line" in live
    # known id → just dropped; unknown → appended verbatim under a folded section
    assert archive.split(M.CONTENTS_CLOSE)[1].count("old thing") == 1
    assert "## [pointers folded " in archive
    assert "[archived 2026-08-03] Hard rule #1 — read agent_output/x.md before scope work → PROJECT_STATE_ARCHIVE.md" in archive
    assert archive.startswith("# PROJECT_STATE_ARCHIVE.md\n\n" + M.CONTENTS_OPEN)
    # the pure function reports the paths the pointers named
    res = M.normalise(state, "state")
    assert res.pointer_paths == ["agent_output/x.md"]


def test_pure_prose_is_stored_as_is_with_the_expected_shape_in_the_note():
    prose = "# LESSONS\nJust a paragraph of notes.\nAnother paragraph.\n"
    res = M.normalise(prose, "lessons")
    assert res.text == prose
    assert len(res.warnings) == 1 and "one `- ` bullet line per entry" in res.warnings[0]


def test_state_paragraphs_become_one_bullet_each_and_get_ids(tmp_path):
    ws = WorkspaceFileManager(str(tmp_path))
    ws.write("state", "## Active now\n**R19 ritual:** 930 stars.\nRadar 10-19: new target.\n- already a bullet\n")
    live = ws.read("state")
    blocks = state_blocks.parse(live)[1]
    assert [b.content_lines[0] for b in blocks] == [
        "- **R19 ritual:** 930 stars.", "- Radar 10-19: new target.", "- already a bullet",
    ]
    assert all(b.id and b.created == M._today() for b in blocks)
    assert "## Active now" in live


def test_scaffolding_pasted_back_is_removed():
    text = (
        "- a\n  <!--mem id:a1 created:2026-10-01 touched:2026-10-01-->\n"
        + M.FORMAT_HEADERS["state"] + "\n"
        "[... older content trimmed from this view — read the file on disk for the full text ...]- b\n"
    )
    res = M.normalise(M.FORMAT_HEADERS["state"] + "\n" + text, "state")
    assert res.text.count("<!--format") == 1
    assert "[... older" not in res.text and "\n- b" in res.text
