# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 117 D3 — the FOCUS rule follows the project's safe-mode switch.

Safe mode ON keeps the spec-077 focus rule byte-identical (don't read/list/
explore outside the workspace; use request_access). Safe mode OFF runs every
command as the user with the user's own permissions, so the old wording made
the model refuse ``whoami`` / ``ls ~/.ssh`` / a Desktop listing even when the
user explicitly asked. OFF keeps the "don't wander on your own initiative"
intent but says that a user request outside the workspace may be done
directly — no request_access / portal needed.
"""

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_os.agent.prompt_builder import Autonomy, PromptBuilder, PromptContext

OLD_FOCUS = (
    "do not read, list, or explore files outside the workspace and portals — "
    "not the user's home, Desktop, Documents, Downloads, or other projects — "
    "even to \"check\"."
)
OLD_ESCAPE = ("If you need access to files outside your workspace, use the request_access "
              "tool to ask the user. Never try to access them directly.")


def _ctx(**over):
    base = PromptContext(
        workspace="/w", model="m", autonomy=Autonomy.CHECK_IN, enabled_agents=[],
        tool_names=["read", "shell", "request_access"], os_type="windows",
        datetime_now="2026-01-01T00:00",
    )
    return replace(base, **over) if over else base


def _safety(ctx):
    return PromptBuilder(workspace=ctx.workspace)._safety(ctx)


def test_default_context_is_safe_mode_on():
    assert _ctx().safe_mode is True


def test_safe_mode_on_keeps_the_existing_focus_rule_verbatim():
    text = _safety(_ctx())
    assert OLD_FOCUS in text
    assert OLD_ESCAPE in text
    assert "use request_access so the user can grant it as a portal" in text
    # Explicit True is byte-identical to the default (existing callers).
    assert _safety(_ctx(safe_mode=True)) == text
    pb = PromptBuilder(workspace="/w")
    assert pb.build(_ctx(safe_mode=True)) == pb.build(_ctx())


def test_safe_mode_off_lets_the_agent_act_on_the_users_request_outside_the_workspace():
    text = _safety(_ctx(safe_mode=False))
    # The refusal-inducing sentences are gone.
    assert OLD_FOCUS not in text
    assert "Never try to access them directly" not in text
    assert "Never modify files outside your workspace unless given portal access" not in text
    lowered = text.lower()
    # Focus intent survives: no wandering on its own initiative.
    assert "on your own initiative" in lowered
    # The explicit permission when the user asks.
    assert "safe mode is off" in lowered
    assert "user's own permissions" in lowered
    assert "when the user asks" in lowered
    assert "no request_access or portal is needed" in lowered


def test_safe_mode_off_changes_only_the_safety_section():
    pb = PromptBuilder(workspace="/w")
    on, off = _safety(_ctx()), _safety(_ctx(safe_mode=False))
    assert on != off
    # Everything outside the safety section is unchanged.
    prefix_on = pb.build(_ctx())[0].replace(on, "<SAFETY>")
    prefix_off = pb.build(_ctx(safe_mode=False))[0].replace(off, "<SAFETY>")
    assert prefix_on == prefix_off


# ---------------------------------------------------------------------------
# Threading: AgentManager keeps live handles' prompt context in step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_safe_mode_changed_refreshes_live_prompt_contexts():
    from agent_os.daemon_v2.agent_manager import AgentManager

    project = {"project_id": "p1", "workspace": "/w", "safe_mode": True}
    other = {"project_id": "p2", "workspace": "/x"}
    store = MagicMock()
    store.get_project.side_effect = lambda pid: {"p1": project, "p2": other}.get(pid)
    mgr = AgentManager(
        project_store=store, ws_manager=MagicMock(), sub_agent_manager=MagicMock(),
        activity_translator=MagicMock(), process_manager=MagicMock(),
        platform_provider=MagicMock(),
    )
    mgr._platform_provider.clear_network = AsyncMock()

    def _handle(ctx):
        h = MagicMock()
        h.context_manager._base_ctx = ctx
        return h

    h1 = _handle(_ctx())
    h2 = _handle(_ctx())
    mgr._handles[("p1", "s1")] = h1
    mgr._handles[("p2", "s2")] = h2

    project["safe_mode"] = False
    await mgr.on_safe_mode_changed("p1")
    assert h1.context_manager._base_ctx.safe_mode is False
    assert h2.context_manager._base_ctx.safe_mode is True   # other project untouched

    project["safe_mode"] = True
    await mgr.on_safe_mode_changed("p1")
    assert h1.context_manager._base_ctx.safe_mode is True
