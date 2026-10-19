# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 117 D5 — the system row written after the user answers the
``unsandboxed_run`` card.

Live regression: the model distrusted the old row ("[Unsandboxed run] The
user allowed once; ...") as "a system notice, not from you in your own
words", refused to repeat the output and refused to re-run. The row must say
unambiguously that it records the USER'S OWN decision on the approval card in
the Orbital UI, that the output is the user's to see, and that the agent
should continue the task using it. Prefix and meta are unchanged.
"""

import pytest

from agent_os.daemon_v2.agent_manager import AgentManager


@pytest.mark.parametrize("decision", ["once", "always", "disable_safe_mode"])
def test_row_states_the_users_own_decision_and_says_to_continue(decision):
    content, meta = AgentManager._unsandboxed_run_row(
        "ls ~/.ssh", "Exit code: 0\nid_ed25519", decision, "unsandboxed_abc")
    assert content.startswith("[Unsandboxed run] ")
    lowered = content.lower()
    assert "the user's own decision" in lowered
    assert "approval card" in lowered
    assert "orbital" in lowered
    assert "the user's to see" in lowered
    assert "continue the task" in lowered
    # Command and output are still present verbatim.
    assert "$ ls ~/.ssh\nExit code: 0\nid_ed25519" in content
    assert meta == {"event": "unsandboxed_run", "decision": decision,
                    "command": "ls ~/.ssh", "pending_id": "unsandboxed_abc"}


def test_row_names_what_the_user_chose():
    once, _ = AgentManager._unsandboxed_run_row("x", "o", "once")
    always, _ = AgentManager._unsandboxed_run_row("x", "o", "always")
    off, _ = AgentManager._unsandboxed_run_row("x", "o", "disable_safe_mode")
    assert "allow once" in once.lower()
    assert "always allow" in always.lower()
    assert "turn safe mode off" in off.lower()


def test_parked_row_names_project_settings_not_the_card():
    content, meta = AgentManager._unsandboxed_run_row(
        "x", "o", "once", "ur_1", parked=True)
    assert content.startswith("[Unsandboxed run] ")
    assert "the user's own decision" in content.lower()
    assert "Project Settings" in content
    assert "approval card" not in content
    assert meta["pending_id"] == "ur_1"


def test_row_meta_without_pending_id_is_unchanged():
    _, meta = AgentManager._unsandboxed_run_row("x", "o", "once")
    assert meta == {"event": "unsandboxed_run", "decision": "once", "command": "x"}
