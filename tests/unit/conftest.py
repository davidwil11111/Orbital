# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit-suite isolation for session import (2026-10-21).

``list_sessions`` lists a project folder's native Claude Code / Codex sessions
(``agent_os.onboarding.session_import``). Point the importer at empty homes for
every unit test so none reads the developer's real ``~/.claude`` / ``~/.codex``;
tests that exercise the importer set these to their own fixture dirs.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_native_session_homes(tmp_path_factory, monkeypatch):
    empty = tmp_path_factory.mktemp("native-homes")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(empty / "claude"))
    monkeypatch.setenv("ORBITAL_CODEX_HOME", str(empty / "codex"))
    yield
