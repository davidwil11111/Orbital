# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression: cold resume finds memory files at new-layout paths.

Bug (F4/F5): After TASK-02, writers produce files at the new flat layout
{workspace}/orbital/... but readers still looked at old slug-namespaced
paths like {workspace}/orbital/{slug}/... Cold resume would inject nothing
(empty context), and the session would start as if no memory existed.

Fix: All readers now route through ProjectPaths, which resolves the same
flat paths that the writers use. Cold resume finds memory files and injects
them so the agent resumes with context.
"""

import json
import os
import re

import pytest

from agent_os.agent.project_paths import ProjectPaths
from agent_os.agent.session import Session
from agent_os.agent.workspace_files import WorkspaceFileManager


@pytest.fixture
def workspace(tmp_path):
    """Seed a workspace with the new flat layout."""
    ws = str(tmp_path)
    pp = ProjectPaths(ws)
    os.makedirs(pp.instructions_dir, exist_ok=True)
    os.makedirs(pp.sessions_dir, exist_ok=True)

    # Write the four Layer-1 memory files (SESSION_LOG retired; CONTEXT → INDEX)
    with open(pp.project_state, "w", encoding="utf-8") as f:
        f.write("## Current Status\nWorking on feature-X implementation.")
    with open(pp.decisions, "w", encoding="utf-8") as f:
        f.write("## 2026-04-01: Use async IO\nChosen for performance.")
    with open(pp.lessons, "w", encoding="utf-8") as f:
        f.write("1. Always validate input before processing.\n2. Write tests first.")
    with open(pp.index, "w", encoding="utf-8") as f:
        f.write("- **Client:** ACME Corp.\n- **Deadline:** 2026-05-01.")

    # Write project goals
    with open(pp.project_goals, "w", encoding="utf-8") as f:
        f.write("# Mission\nBuild a REST API with authentication.\n\n# Rules\n- Keep it simple.")

    # Write a prior session JSONL
    session_path = pp.session_file("sess_prior_1")
    with open(session_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"role": "user", "content": "Start the auth work", "timestamp": "2026-04-01T00:00:00+00:00"}) + "\n")
        f.write(json.dumps({"role": "assistant", "content": "OK, starting auth module.", "timestamp": "2026-04-01T00:00:01+00:00"}) + "\n")

    return ws


class TestColdResumeNewLayout:
    """Cold resume reads from the new flat layout, not old slug paths."""

    def test_workspace_file_manager_reads_memory_files(self, workspace):
        """WorkspaceFileManager.read() returns content from new-layout paths."""
        wfm = WorkspaceFileManager(workspace)
        assert wfm.read("state") is not None, "PROJECT_STATE.md not found at new-layout path"
        assert "feature-X" in wfm.read("state")
        assert wfm.read("decisions") is not None
        assert wfm.read("lessons") is not None
        # CONTEXT.md was renamed to INDEX.md ("context" key → "index").
        assert wfm.read("index") is not None

    # test_cold_resume_context_contains_memory_content / _does_not_show_onboarding:
    # DELETED 2026-10-20 with build_cold_resume_context (the Layer-1 rows injected
    # every turn are the resume context).

    def test_project_goals_readable_at_new_path(self, workspace):
        """project_goals.md is readable at ProjectPaths.project_goals."""
        pp = ProjectPaths(workspace)
        assert os.path.isfile(pp.project_goals), "project_goals.md must exist at new-layout path"
        with open(pp.project_goals, "r") as f:
            content = f.read()
        assert "REST API" in content

    def test_sessions_dir_at_new_path(self, workspace):
        """Session files live at ProjectPaths.sessions_dir."""
        pp = ProjectPaths(workspace)
        assert os.path.isdir(pp.sessions_dir), "sessions dir must exist at new-layout path"
        files = os.listdir(pp.sessions_dir)
        assert any(f.endswith(".jsonl") for f in files), \
            "At least one .jsonl session file must exist in sessions_dir"

    def test_no_slug_dir_needed(self, workspace):
        """The new layout has NO slug subdirectory under orbital/."""
        pp = ProjectPaths(workspace)
        orbital_entries = os.listdir(pp.orbital_dir)
        # No entry should look like a slug (e.g. 'my-project-a1b2')
        sluglike = [e for e in orbital_entries if re.match(r'^[a-z0-9-]+-[a-f0-9]{4}$', e)]
        assert not sluglike, f"Found slug-style directories in orbital/: {sluglike}"
