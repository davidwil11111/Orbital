# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests: the memory pass must actually COMPRESS.

Incident (orbital-marketing, 2026-07-27): a pass that SUCCEEDED returned every
file bigger than it started and archived nothing — the prompt stated the
target but never the file's CURRENT size, so the model always concluded it was
fine. (The second defect of that incident — a 15-minute non-streaming
whole-file merge dropping its connection — went away with the whole-file
merge: the memory editor's replies are short id lists.)

Invariants:
  - consolidation_target(key) sits a real margin BELOW the soft budget, so a
    pass that lands on target does not re-trip the flag on the next append.
  - The editor prompt carries MEASURED sizes and an explicit amount to cut.
  - The floor never moves an entry (spec 124): when the editor declines the
    file stays whole and the injected view folds instead.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.testutils import streamable

from agent_os.agent import memory_editor
from agent_os.agent import memory_entries as _mem
from agent_os.agent import workspace_files as wsf_module
from agent_os.agent.workspace_files import (
    WorkspaceFileManager,
    run_session_end_routine,
)


def _mock_session(session_id="sess_compress"):
    session = MagicMock()
    session.session_id = session_id
    session.session_uuid = session_id
    session.get_messages.return_value = []
    return session


def _declining_editor():
    """An editor model that looks and chooses nothing."""
    provider = streamable(AsyncMock())
    resp = MagicMock()
    resp.text = "{}"
    resp.raw_message = {"role": "assistant", "content": "{}"}
    resp.tool_calls = []
    provider.complete.return_value = resp
    return provider


@pytest.fixture(autouse=True)
def _reset_completion_set():
    wsf_module._completed_session_ends.clear()
    memory_editor._RUNNING.clear()
    yield
    wsf_module._completed_session_ends.clear()


def _over_soft_workspace(tmp_path) -> WorkspaceFileManager:
    """decisions and lessons both over their soft budgets, like the incident."""
    ws = WorkspaceFileManager(str(tmp_path))
    ws.ensure_dir()
    ws.write("decisions", "".join(
        f"- 2026-07-{d:02d}: Decision {d}. {'x' * 1100}\n\n"
        for d in range(1, 27)
    ))
    ws.write("lessons", "".join(
        f"- Lesson {i}. {'y' * 1150}\n" for i in range(1, 19)
    ))
    return ws


def _prompt(ws) -> str:
    return memory_editor.build_prompt(
        {k: ws.read(k) or "" for k in memory_editor.LIVE_KEYS},
        today="2026-09-19", since=None, sessions=[],
    )


# ---------------------------------------------------------------------------
# 1. Consolidation target carries headroom below the soft budget
# ---------------------------------------------------------------------------

def test_target_sits_a_full_headroom_below_soft_for_durable_files():
    """Landing exactly on the soft budget means the very next appended entry
    re-trips the flag. The pass has to buy room."""
    for key in ("decisions", "lessons"):
        soft = _mem.FILE_BUDGETS[key]["soft"]
        target = _mem.consolidation_target(key)
        assert soft - target >= _mem.CONSOLIDATION_HEADROOM_TOKENS


def test_target_never_demands_an_absurd_cut_from_a_small_file():
    """A flat 1000-token headroom would gut PROJECT_STATE (soft 1800) and
    INDEX (soft 1500), so the target is floored proportionally."""
    for key in ("state", "index"):
        soft = _mem.FILE_BUDGETS[key]["soft"]
        target = _mem.consolidation_target(key)
        assert 0 < target < soft
        assert target >= soft * 0.5


def test_target_is_below_soft_for_every_layer1_file():
    for key in ("state", "decisions", "lessons", "index"):
        assert _mem.consolidation_target(key) < _mem.FILE_BUDGETS[key]["soft"]


# ---------------------------------------------------------------------------
# 2. The editor prompt states MEASURED sizes and an explicit amount to cut
# ---------------------------------------------------------------------------

def test_prompt_states_measured_size_and_quota_for_over_budget_files(tmp_path):
    ws = _over_soft_workspace(tmp_path)
    prompt = _prompt(ws)

    dec_now = _mem.est_tokens(_mem._budget_text(ws.read("decisions"), "decisions"))
    dec_target = _mem.consolidation_target("decisions")

    assert "exact counts" in prompt
    assert f"{int(dec_now)} tokens" in prompt
    assert f"target {dec_target}" in prompt
    assert f"cut ~{int(dec_now) - dec_target}" in prompt


def test_prompt_does_not_ask_the_model_to_estimate_its_own_size(tmp_path):
    """The '(~4 chars/token)' self-estimate is what made archiving never fire."""
    assert "4 chars/token" not in _prompt(_over_soft_workspace(tmp_path))


def test_prompt_marks_within_target_files_as_needing_no_action(tmp_path):
    ws = WorkspaceFileManager(str(tmp_path))
    ws.ensure_dir()
    ws.write("decisions", "- 2026-07-01: Tiny. Chose A.\n")
    assert "within target" in _prompt(ws).lower()


# ---------------------------------------------------------------------------
# 4. When the editor declines, the floor moves nothing — the view folds
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_declining_editor_leaves_the_file_whole_and_the_view_folds(tmp_path, caplog):
    """An editor that archives nothing leaves the file over budget on disk
    (archiving is its judgement, never the daemon's); the injected view
    folds the overflow and the floor logs it."""
    ws = _over_soft_workspace(tmp_path)
    before = ws.read("decisions")
    assert _mem.over_soft_budget(before, "decisions")  # guard

    with caplog.at_level(logging.INFO):
        await run_session_end_routine(
            _mock_session("s_floor"), _declining_editor(), ws, session_uuid="s_floor"
        )

    assert ws.read("decisions") == before
    assert not ws.exists("decisions_archive")
    # over soft, under hard: the floor has nothing to say, the view injects
    # every bullet line within the hard budget
    assert not any("over its hard cap" in r.getMessage() for r in caplog.records)
    view = _mem.inject_view(before, "decisions", _mem.FILE_BUDGETS["decisions"]["hard"])
    assert _mem.est_tokens(view) <= _mem.FILE_BUDGETS["decisions"]["hard"]
    assert view.count("- 2026-07-") == 26


def test_backstop_leaves_a_file_already_under_target_alone(tmp_path):
    ws = WorkspaceFileManager(str(tmp_path))
    ws.ensure_dir()
    ws.write("decisions", "- 2026-07-01: Small. Chose A.\n")
    before = ws.read("decisions")          # as persisted (write adds the format header)
    wsf_module._apply_hard_caps(ws)
    assert ws.read("decisions") == before
    assert not (ws.read("decisions_archive") or "").strip()
