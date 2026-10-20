# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 124 §6 item 5: the injected view — every bullet line, detail only
for hot / pinned entries, folding by section, order and byte stability —
plus the bullet-lines-only soft budget (§3.4 step 5)."""

from __future__ import annotations

from agent_os.agent import memory_entries as M


def _entry(i: int, *, touched: str, detail_lines: int = 3, section=None, tag: str = "") -> str:
    tagtxt = f" tag:{tag}" if tag else ""
    body = f"- Decision {i:03d}: chose option {i} because reason {i}.\n"
    body += f"  <!--mem id:d{i:03d} created:2026-01-01 touched:{touched}{tagtxt}-->\n"
    for k in range(detail_lines):
        body += f"  detail {i:03d}/{k} " + "x" * 40 + "\n"
    return body


def _file(n: int, *, sections: int = 0, detail_lines: int = 3, pinned: set | None = None) -> str:
    out = ["# DECISIONS", ""]
    per = n // sections if sections else n
    for i in range(1, n + 1):
        if sections and (i - 1) % per == 0:
            out.append(f"## Topic {(i - 1) // per + 1}")
        touched = f"2026-{1 + (i % 9):02d}-{1 + (i % 27):02d}"
        out.append(_entry(i, touched=touched, detail_lines=detail_lines,
                          tag="pinned" if pinned and i in pinned else "").rstrip("\n"))
    return "\n".join(out) + "\n"


def _bullets(view: str) -> list[str]:
    return [ln for ln in view.split("\n") if ln.startswith("- ")]


def test_everything_fits_returns_the_same_object_when_nothing_is_stripped():
    content = "# D\n- a\n  detail\n- b\n"
    assert M.inject_view(content, "decisions", 1000) is content


def test_everything_fits_strips_only_the_comments():
    content = _file(3)
    view = M.inject_view(content, "decisions", 9000)
    assert "<!--mem" not in view
    assert view == M.inject_view(content, "decisions", 9000)
    assert _bullets(view) == _bullets(content)
    assert "detail 001/0" in view and "detail 003/2" in view


def test_over_budget_keeps_every_bullet_and_detail_only_for_the_hottest():
    content = _file(60, detail_lines=4)
    full = len(content)
    budget = full // 4 // 2                      # half the file, in tokens
    view = M.inject_view(content, "decisions", budget)
    assert len(view) <= budget * 4
    assert _bullets(view) == _bullets(content)   # all 60 bullet lines, file order
    assert "<!--mem" not in view
    with_detail = {ln.split("/")[0].split()[-1] for ln in view.split("\n") if ln.startswith("  detail")}
    assert 0 < len(with_detail) < 60
    # the entries that kept their detail are the most recently touched ones
    touched = {f"{i:03d}": f"2026-{1 + (i % 9):02d}-{1 + (i % 27):02d}" for i in range(1, 61)}
    hottest = min(touched[i] for i in with_detail)
    coldest_without = max(t for i, t in touched.items() if i not in with_detail)
    assert hottest >= coldest_without
    # byte-stable
    assert M.inject_view(content, "decisions", budget) == view


def test_pinned_entries_always_keep_their_detail():
    content = _file(60, detail_lines=4, pinned={7})
    content = content.replace("touched:2026-08-08 tag:pinned", "touched:2020-01-01 tag:pinned")
    view = M.inject_view(content, "decisions", len(content) // 4 // 3)
    assert "detail 007/3" in view


def test_bullet_overflow_folds_by_section_with_counts_and_newest():
    content = _file(400, sections=8, detail_lines=0)
    budget = 400 * 20 // 4 // 4                  # a quarter of the bullet lines
    view = M.inject_view(content, "decisions", budget)
    assert len(view) <= budget * 4 + len(M._TRIM_NOTE)
    assert "<!--mem" not in view
    for s in range(1, 9):
        assert f"## Topic {s}" in view
    counts = [ln for ln in view.split("\n") if ln.startswith("[") and "more entries in this section not shown" in ln]
    assert len(counts) == 8
    assert all("read orbital/DECISIONS.md" in ln for ln in counts)
    shown = _bullets(view)
    assert 8 <= len(shown) < 400
    # file order is kept
    nums = [int(ln.split(":")[0].split()[-1]) for ln in shown]
    assert nums == sorted(nums)
    # every section shows at least its newest entry
    lines = view.split("\n")
    for s in range(1, 9):
        at = lines.index(f"## Topic {s}")
        assert lines[at + 1].startswith("- ")
    assert M.inject_view(content, "decisions", budget) == view


def test_bullet_overflow_without_sections_keeps_newest_plus_a_count_line():
    content = _file(120, detail_lines=0)
    budget = 120 * 20 // 4 // 3
    view = M.inject_view(content, "decisions", budget)
    shown = _bullets(view)
    assert 0 < len(shown) < 120
    assert view.count("more entries above not shown") == 1
    assert "# DECISIONS" in view
    nums = [int(ln.split(":")[0].split()[-1]) for ln in shown]
    assert nums == sorted(nums)


def test_state_file_uses_the_same_algorithm():
    content = "## Now\n" + "".join(
        f"- state line {i} " + "s" * 60 + f"\n  <!--mem id:s{i:03d} created:2026-10-{1 + i % 20:02d} touched:2026-10-{1 + i % 20:02d}-->\n  detail {i}\n"
        for i in range(40)
    )
    bullets_only = len(M._budget_text(content, "state"))
    view = M.inject_view(content, "state", (bullets_only + 120) // 4)
    assert "<!--mem" not in view
    assert view.count("- state line") == 40
    assert 0 < view.count("  detail") < 40


def test_index_is_still_head_trimmed():
    content = "\n".join(f"- f{i}.md — thing {i}" for i in range(200)) + "\n"
    view = M.inject_view(content, "index", 50)
    assert view.endswith(M._TRIM_NOTE) and "- f0.md" in view


# ---------------------------------------------------------------------------
# soft budget = bullet lines only
# ---------------------------------------------------------------------------

def test_soft_budget_counts_bullet_lines_not_detail_or_comments(monkeypatch):
    monkeypatch.setitem(M.FILE_BUDGETS, "lessons", {"soft": 20, "hard": 300})
    content = "- a short rule.\n  <!--mem id:a created:2026-01-01 touched:2026-01-01-->\n" + "  detail " + "d" * 500 + "\n"
    assert M.est_tokens(M._budget_text(content, "lessons")) < 20
    assert not M.over_soft_budget(content, "lessons")
    assert M.soft_flag(content, "lessons") is None
    big = "- " + "r" * 200 + "\n"
    assert M.over_soft_budget(big, "lessons")
    assert "(1 entries)" in M.soft_flag(big, "lessons")


def test_hard_budget_counts_the_whole_text():
    content = "- a short rule.\n" + "  detail " + "d" * 500 + "\n"
    assert M.over_hard_budget(content, "state") is False
    assert M.over_hard_budget("- x\n" + "  d" * 5000, "state") is True
