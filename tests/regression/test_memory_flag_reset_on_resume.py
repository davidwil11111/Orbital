# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression: a resume after a chat-mode write to PROJECT_STATE.md must
cause the next run's prepare() to re-read the file. The mechanism is the
per-run Layer-1 snapshot (spec 112): ``begin_run()`` at the next run's
entry refreshes it. (The cold-resume row and its ``_cold_resume_injected``
flag were removed 2026-10-20.)
"""

from __future__ import annotations

import os
import tempfile

import pytest

from agent_os.agent.context import ContextManager
from agent_os.agent.project_paths import ProjectPaths
from agent_os.agent.prompt_builder import Autonomy, PromptContext
from agent_os.agent.session import Session
from agent_os.agent.workspace_files import WorkspaceFileManager


class _StubPromptBuilder:
    def build(self, context):
        return ("CACHED", "SEMI_STABLE", "DYNAMIC")


@pytest.mark.asyncio
async def test_cold_resume_flag_reset_causes_memory_reread(tmp_path):
    workspace = str(tmp_path)
    paths = ProjectPaths(workspace)
    os.makedirs(paths.orbital_dir, exist_ok=True)

    # Initial PROJECT_STATE.md content
    with open(paths.project_state, "w", encoding="utf-8") as f:
        f.write("# Initial state")

    sess = Session.new("sess_mem_test", workspace)
    wf = WorkspaceFileManager(workspace)

    ctx = PromptContext(
        workspace=workspace,
        model="test",
        autonomy=Autonomy.HANDS_OFF,
        enabled_agents=[],
        tool_names=[],
        os_type="linux",
        datetime_now="2026-01-01T00:00:00Z",
        project_name="test",
        agent_name="test",
    )
    cm = ContextManager(
        sess, _StubPromptBuilder(), ctx,
        model_context_limit=200_000,
        workspace_files=wf,
        sub_agent_provider=lambda: [],
    )

    # First run reads memory
    cm.begin_run()
    cm.prepare()

    # Modify the file. Within the run the snapshot wins; the next run re-reads.
    with open(paths.project_state, "w", encoding="utf-8") as f:
        f.write("# Mutated state — after chat")

    cm.begin_run()
    second = cm.prepare()

    # The newly built result must contain the post-chat content somewhere
    # in the system messages it produced.
    all_text = " ".join(
        m.get("content", "") if isinstance(m.get("content"), str) else ""
        for m in second
        if isinstance(m, dict)
    )
    assert "Mutated state" in all_text
