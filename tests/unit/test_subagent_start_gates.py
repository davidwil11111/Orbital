# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pre-dispatch gates in ``SubAgentManager.start()``.

Spec 116 D5: a Claude Code older than the manifest floor (2.1.114 — the
first npm layout with a native exe) is refused with "Claude Code v2.1.100
is older than 2.1.114; open Settings → Sub-agents and click Update" instead
of the SDK's batch-script paragraph. Spec 113 A6: an agent that is installed
but not signed in / missing its key is refused before the CLI is spawned,
instead of failing at runtime with no signal. Both record a reason the
inject route copies onto the chat row (``last_start_failure_meta``) and the
telemetry row carries (``subagent_failed{reason}``) with structured
diagnostics — the Windows report must be readable from those alone.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from agent_os import telemetry
from agent_os.agents.manifest import ManifestLoader
from agent_os.agents.registry import AgentRegistry
from agent_os.agents.setup_engine import SetupEngine
from agent_os.agents.setup_types import AgentSetupStatus
from agent_os.daemon_v2.models import make_session_key
from agent_os.daemon_v2.sub_agent_manager import SubAgentManager

_MANIFEST = os.path.join(os.path.dirname(__file__), "..", "..", "agent_os",
                         "agents", "manifests", "claude_code.yaml")


class _ProcessManager:
    def set_turn_closed_callback(self, callback):
        pass

    def set_permission_request_callback(self, callback):
        pass

    def set_active_dispatch(self, *args, **kwargs):
        pass

    def clear_dispatch(self, *args, **kwargs):
        pass


def _status(**overrides) -> AgentSetupStatus:
    base = dict(
        slug="claude-code", name="Claude Code", installed=True,
        binary_path=r"C:\Users\x\AppData\Roaming\npm\claude.CMD",
        version="2.1.302 (Claude Code)", dependencies_met=True,
        missing_dependencies=[], credentials_configured=True,
        missing_credentials=[], setup_actions=[], credential_state=None,
        update_needed=False, min_version="2.1.114",
    )
    base.update(overrides)
    return AgentSetupStatus(**base)


def _manager(status: AgentSetupStatus, *, cached: bool = True, probe: dict | None = None):
    registry = AgentRegistry()
    registry.register(ManifestLoader.load(os.path.abspath(_MANIFEST)))
    engine = SetupEngine(registry)
    engine.check_agent = lambda slug: status                      # type: ignore[method-assign]
    engine.cached_status = (lambda slug: status) if cached else (lambda slug: None)  # type: ignore[method-assign]
    engine.last_auth_probe = lambda slug: probe                   # type: ignore[method-assign]
    engine.get_adapter_config = lambda slug, project_workspace, credential_overrides=None: {  # type: ignore[method-assign]
        "command": status.binary_path, "args": [], "workspace": project_workspace,
        "approval_patterns": [], "env": {}, "network_domains": [], "interactive": False,
    }
    return SubAgentManager(_ProcessManager(), registry=registry, setup_engine=engine)


@pytest.fixture
def spool_rows(tmp_path):
    telemetry.reset_for_tests()
    telemetry.configure(tmp_path, is_enabled=lambda: True)

    def read():
        path = tmp_path / "telemetry" / "events.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    yield read
    telemetry.reset_for_tests()


# ---------------------------------------------------------------------------
# Spec 116 D5 — version floor
# ---------------------------------------------------------------------------

class TestVersionGate:

    @pytest.mark.asyncio
    async def test_old_cli_is_refused_with_the_update_sentence(self):
        manager = _manager(_status(version="2.1.100 (Claude Code)", update_needed=True))

        result = await manager.start("p1", "claude-code", session_id="s1")

        assert result == ("Error: Claude Code v2.1.100 is older than 2.1.114; "
                          "open Settings → Sub-agents and click Update")
        assert manager._adapters.get(make_session_key("p1", "s1"), {}) == {}

    @pytest.mark.asyncio
    async def test_meta_names_the_reason_floor_and_install_kind(self):
        """What the inject route copies onto the chat row's ``_meta`` and
        what the telemetry row carries: the Windows case in one dict."""
        manager = _manager(_status(version="2.1.100 (Claude Code)", update_needed=True))

        await manager.start("p1", "claude-code", session_id="s1")
        meta = manager.last_start_failure_meta("p1", "claude-code")

        assert meta["reason"] == "update_needed"
        assert meta["min_version"] == "2.1.114"
        assert meta["version"] == "2.1.100"
        assert meta["cli_version"] == "2.1.100"
        assert meta["install_kind"] == "npm"
        assert isinstance(meta["duration_ms"], int)
        assert manager.last_start_failure_meta("p1", "codex") is None

    @pytest.mark.asyncio
    async def test_send_emits_subagent_failed_with_reason_and_diagnostics(self, spool_rows):
        manager = _manager(_status(version="2.1.100 (Claude Code)", update_needed=True))

        result = await manager.send("p1", "claude-code", "hi", session_id="s1")

        assert result.startswith("Error: Claude Code v2.1.100 is older than")
        rows = [r for r in spool_rows() if r["event"] == "subagent_failed"]
        assert len(rows) == 1
        row = rows[0]
        assert row["agent"] == "claude-code"
        assert row["reason"] == "update_needed"
        assert row["cli_version"] == "2.1.100"
        assert row["install_kind"] == "npm"
        assert row["exit_code"] is None
        assert set(row) == {"event", "ts", "agent", "reason", "exit_code", "cli_version",
                            "install_kind", "duration_ms", "error_class",
                            "stderr_category"}
        assert not [r for r in spool_rows() if r["event"] == "subagent_dispatched"]

    @pytest.mark.asyncio
    async def test_gate_runs_check_agent_when_no_sweep_is_cached(self):
        manager = _manager(_status(version="2.1.100 (Claude Code)", update_needed=True),
                           cached=False)
        result = await manager.start("p1", "claude-code", session_id="s1")
        assert "click Update" in result

    @pytest.mark.asyncio
    async def test_version_at_the_floor_passes_the_gate(self):
        """A CLI at/above the floor reaches the transport (which this test
        leaves unwired, so the spawn fails later and differently)."""
        manager = _manager(_status(version="2.1.114 (Claude Code)"))
        result = await manager.start("p1", "claude-code", session_id="s1")
        assert "older than" not in result
        meta = manager.last_start_failure_meta("p1", "claude-code")
        assert meta is None or meta["reason"] == "spawn"


# ---------------------------------------------------------------------------
# Spec 113 A6 — login gate
# ---------------------------------------------------------------------------

class TestAuthGate:

    @pytest.mark.asyncio
    async def test_signed_out_cli_is_refused_before_spawn(self):
        manager = _manager(_status(credentials_configured=False,
                                   missing_credentials=["claude_auth"]),
                           probe={"exit_code": 1, "error_class": None,
                                  "stderr_category": "not_logged_in", "duration_ms": 300})

        result = await manager.start("p1", "claude-code", session_id="s1")

        assert result == ("Error: 'claude-code' is installed but not signed in — "
                          "open Settings → Sub-agents")
        meta = manager.last_start_failure_meta("p1", "claude-code")
        assert meta["reason"] == "not_authenticated"
        assert meta["min_version"] is None
        assert meta["cli_version"] == "2.1.302"
        # The auth probe's own outcome rides along (spec 113).
        assert meta["exit_code"] == 1
        assert meta["stderr_category"] == "not_logged_in"

    @pytest.mark.asyncio
    async def test_missing_secret_names_the_key(self):
        manager = _manager(_status(credentials_configured=False,
                                   missing_credentials=["DEEPSEEK_API_KEY"]))
        result = await manager.start("p1", "claude-code", session_id="s1")
        assert result == ("Error: 'claude-code' is installed but missing DEEPSEEK_API_KEY — "
                          "open Settings → Sub-agents")

    @pytest.mark.asyncio
    async def test_send_counts_not_authenticated_not_dispatched(self, spool_rows):
        manager = _manager(_status(credentials_configured=False,
                                   missing_credentials=["claude_auth"]))
        result = await manager.send("p1", "claude-code", "hi", session_id="s1")
        assert result.startswith("Error: 'claude-code' is installed but not signed in")
        events = [(r["event"], r.get("reason")) for r in spool_rows()
                  if r["event"].startswith("subagent_")]
        assert events == [("subagent_failed", "not_authenticated")]

    @pytest.mark.asyncio
    async def test_version_gate_wins_over_the_auth_gate(self):
        """An old CLI reads as signed-out too (no ``auth status`` subcommand
        before 2.1.41) — the user must hear "update", not "login"."""
        manager = _manager(_status(version="2.1.100 (Claude Code)", update_needed=True,
                                   credentials_configured=False,
                                   missing_credentials=["claude_auth"]))
        result = await manager.start("p1", "claude-code", session_id="s1")
        assert "click Update" in result
        assert manager.last_start_failure_meta("p1", "claude-code")["reason"] == "update_needed"


# ---------------------------------------------------------------------------
# Spec 116 D8 — is a worker of this agent alive anywhere?
# ---------------------------------------------------------------------------

class TestHasLiveAdapter:

    def test_registered_adapter_counts_even_when_idle(self):
        manager = SubAgentManager(_ProcessManager())
        manager._adapters[make_session_key("p1", "s1")] = {
            "claude-code": SimpleNamespace(is_alive=lambda: True)}
        assert manager.has_live_adapter("claude-code") is True
        assert manager.has_live_adapter("codex") is False

    def test_dead_transport_does_not_count(self):
        manager = SubAgentManager(_ProcessManager())
        manager._adapters[make_session_key("p1", "s1")] = {
            "claude-code": SimpleNamespace(is_alive=lambda: False)}
        assert manager.has_live_adapter("claude-code") is False

    def test_adapter_without_is_alive_counts(self):
        manager = SubAgentManager(_ProcessManager())
        manager._adapters[make_session_key("p2", "s9")] = {"claude-code": object()}
        assert manager.has_live_adapter("claude-code") is True
