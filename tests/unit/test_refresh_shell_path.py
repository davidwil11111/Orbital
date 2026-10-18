# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 115 D2 / §7 — ``POST /settings/sub-agents/refresh`` re-captures the
shell PATH before probing and forgets every cached binary path, so "I just
installed it, click Refresh" works without relaunching the app.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _no_live_model_probes(monkeypatch):
    import agent_os.agent.transports.codex_models as _cm
    import agent_os.agent.transports.claude_models as _clm

    async def _unavailable(binary="x", **_kw):
        return None

    monkeypatch.setattr(_cm, "fetch_codex_models", _unavailable)
    monkeypatch.setattr(_clm, "fetch_claude_models", _unavailable)
    _cm.clear_codex_models_cache()
    _clm.clear_claude_models_cache()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    os.makedirs(str(tmp_path / "home"), exist_ok=True)
    from agent_os.api.app import create_app
    app = create_app(data_dir=str(tmp_path / "data"))
    with TestClient(app) as c:
        yield c


def test_refresh_recaptures_the_shell_path_before_probing(client, monkeypatch):
    from agent_os.api.routes import settings as routes
    from agent_os.platform import shell_path

    order: list[str] = []

    def fake_merge(*a, **kw):
        order.append("merge")
        return shell_path.ShellPathCapture(login=[], interactive=[])

    monkeypatch.setattr(shell_path, "merge_shell_path_into_environ", fake_merge)
    engine = routes._setup_engine
    assert engine is not None
    monkeypatch.setattr(engine, "check_all", lambda: order.append("check_all") or [])

    resp = client.post("/api/v2/settings/sub-agents/refresh")
    assert resp.status_code == 200
    assert order == ["merge", "check_all"]


def test_refresh_forgets_every_cached_binary_path(client, monkeypatch):
    """Memory ``claude-code "signed out" = stale binary path``: resolve_binary
    caches hits for the daemon lifetime, so a moved binary kept its dead path
    across Refresh. Refresh now drops every slug's cached hit."""
    from agent_os.api.routes import settings as routes
    from agent_os.platform import shell_path

    monkeypatch.setattr(shell_path, "merge_shell_path_into_environ",
                        lambda *a, **kw: shell_path.ShellPathCapture(login=[], interactive=[]))
    engine = routes._setup_engine
    monkeypatch.setattr(engine, "check_all", lambda: [])
    engine._resolved_paths["codex"] = "/dead/codex"
    engine._resolved_paths["claude-code"] = "/dead/claude"

    assert client.post("/api/v2/settings/sub-agents/refresh").status_code == 200
    assert engine.get_resolved_path("codex") is None
    assert engine.get_resolved_path("claude-code") is None
