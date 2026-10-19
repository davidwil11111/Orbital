# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Auth probe scope (1018 bench item 9) and the expired-sign-in record.

(c) The daemon logged an ``auth probe failed`` WARNING at every start for
agents that are not installed (cursor, kimi): the probe ran the bare command,
which of course failed. A not-installed agent is never probed; an installed
agent whose probe fails still logs its WARNING (spec 116 D7's instrument).

(b) The Claude Code card said "signed in" with an expired OAuth token:
``claude auth status`` reads the local token file and has no idea the
token no longer works, and the probe makes no network call (and must not).
A dispatch that fails with an authentication error is the evidence, so it
is recorded per agent and the status carries ``auth_expired`` until a
successful dispatch, a confirmed Orbital login, a logout, or a probe that
itself reports signed-out clears it. A plain re-probe that still says
``loggedIn: true`` does NOT clear it — that probe is exactly what cannot
see the expiry.
"""

from __future__ import annotations

import logging
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_os.agents.manifest import (
    AgentManifest,
    ManifestCredential,
    ManifestRuntime,
    ManifestSetup,
)
from agent_os.agents.registry import AgentRegistry
from agent_os.agents.setup_engine import SetupEngine, is_auth_failure_text

LOGGER = "agent_os.agents.setup_engine"


def _engine(slug: str = "claude-code", command: str = "orbital-test-no-such-cli-xyz"):
    manifest = AgentManifest(
        manifest_version="1", name="Claude Code", slug=slug, description="",
        author="t", version="1",
        runtime=ManifestRuntime(adapter="cli", command=command),
        setup=ManifestSetup(credentials=[ManifestCredential(
            key="claude_auth", label="Claude", type="oauth_cli", required=True,
            check_command=f"{command} auth status --json",
            check_field="loggedIn", check_value="true",
        )]),
    )
    registry = AgentRegistry()
    registry.register(manifest)
    return SetupEngine(registry=registry), manifest


def _result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args="", returncode=returncode,
                                       stdout=stdout, stderr=stderr)


def _warnings(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING]


# ---------------------------------------------------------------------------
# (c) only installed agents are probed
# ---------------------------------------------------------------------------

class TestAuthProbeOnlyForInstalledAgents:

    def test_not_installed_agent_is_never_probed_and_logs_nothing(self, caplog):
        engine, _ = _engine()
        calls: list = []

        def _run(cmd, *a, **kw):
            calls.append(cmd)
            return _result(returncode=1, stderr="not found")
        with patch("agent_os.agents.setup_engine.shutil.which", return_value=None), \
                patch("agent_os.agents.setup_engine.subprocess.run", side_effect=_run), \
                caplog.at_level(logging.WARNING, logger=LOGGER):
            status = engine.check_agent("claude-code")
        assert status.installed is False
        assert not any("auth status" in str(c) for c in calls), calls
        assert not [r for r in _warnings(caplog) if "auth probe" in r.getMessage()]
        # Still reported as not signed in — it is not, there is no CLI.
        assert status.credentials_configured is False
        assert status.missing_credentials == ["claude_auth"]
        assert engine.last_auth_probe("claude-code") is None

    def test_installed_agent_with_failing_probe_still_warns(self, caplog):
        engine, _ = _engine()
        engine._resolved_paths["claude-code"] = "/opt/claude"

        def _run(cmd, *a, **kw):
            if "auth status" in str(cmd):
                return _result(returncode=1, stderr="error: unknown command 'auth'")
            return _result(stdout="2.1.302 (Claude Code)")
        with patch("agent_os.agents.setup_engine.subprocess.run", side_effect=_run), \
                caplog.at_level(logging.WARNING, logger=LOGGER):
            status = engine.check_agent("claude-code")
        assert status.installed is True
        assert status.credentials_configured is False
        probe_warnings = [r for r in _warnings(caplog) if "auth probe" in r.getMessage()]
        assert len(probe_warnings) == 1
        assert "exit code 1" in probe_warnings[0].getMessage()

    def test_check_credentials_without_a_binary_still_probes(self):
        """The explicit credential re-check (post-login) keeps probing the
        bare command; only check_agent knows the agent is not installed."""
        engine, manifest = _engine()
        with patch("agent_os.agents.setup_engine.subprocess.run",
                   return_value=_result(stdout='{"loggedIn": true}')) as run:
            ok, missing = engine.check_credentials(manifest)
        assert ok is True and missing == []
        assert run.called


# ---------------------------------------------------------------------------
# (b) the expired sign-in record
# ---------------------------------------------------------------------------

AUTH_ERRORS = [
    "Failed to authenticate. API Error: 401 {\"type\":\"error\",\"error\":"
    "{\"type\":\"authentication_error\",\"message\":\"OAuth token has expired.\"}}",
    "Error: OAuth session expired, please log in again",
    "Your access token could not be refreshed because your refresh token is invalid.",
    "Invalid API key · Please run /login",
]

NOT_AUTH_ERRORS = [
    "Error: context window exceeded",
    "API Error: 529 overloaded",
    "Error: task was cancelled",
    "",
]


class TestAuthFailureText:

    @pytest.mark.parametrize("text", AUTH_ERRORS)
    def test_auth_errors_match(self, text):
        assert is_auth_failure_text(text) is True

    @pytest.mark.parametrize("text", NOT_AUTH_ERRORS)
    def test_other_errors_do_not(self, text):
        assert is_auth_failure_text(text) is False


def _installed_signed_in_engine():
    engine, _ = _engine()
    engine._resolved_paths["claude-code"] = "/opt/claude"
    state = {"logged_in": True}

    def _run(cmd, *a, **kw):
        if "auth status" in str(cmd):
            return _result(stdout='{"loggedIn": %s}' % ("true" if state["logged_in"] else "false"))
        return _result(stdout="2.1.302 (Claude Code)")
    return engine, state, _run


class TestAuthExpiredRecord:

    def test_recorded_failure_marks_the_status_expired(self):
        engine, _state, run = _installed_signed_in_engine()
        assert engine.note_auth_failure("claude-code", AUTH_ERRORS[0]) is True
        with patch("agent_os.agents.setup_engine.subprocess.run", side_effect=run):
            status = engine.check_agent("claude-code")
        assert status.auth_expired is True
        assert engine.auth_expired("claude-code") is True

    def test_unrelated_error_is_not_recorded(self):
        engine, _state, run = _installed_signed_in_engine()
        assert engine.note_auth_failure("claude-code", "Error: context window exceeded") is False
        with patch("agent_os.agents.setup_engine.subprocess.run", side_effect=run):
            assert engine.check_agent("claude-code").auth_expired is False

    def test_a_signed_in_reprobe_does_not_clear_it(self):
        """The probe reads the local token file; it says loggedIn=true with
        an expired token, so it cannot be what clears the record."""
        engine, _state, run = _installed_signed_in_engine()
        engine.note_auth_failure("claude-code", AUTH_ERRORS[1])
        with patch("agent_os.agents.setup_engine.subprocess.run", side_effect=run):
            engine.check_agent("claude-code")
            engine.invalidate_cache()
            assert engine.check_agent("claude-code").auth_expired is True

    def test_a_signed_out_probe_supersedes_it(self):
        engine, state, run = _installed_signed_in_engine()
        engine.note_auth_failure("claude-code", AUTH_ERRORS[1])
        state["logged_in"] = False
        with patch("agent_os.agents.setup_engine.subprocess.run", side_effect=run):
            status = engine.check_agent("claude-code")
            assert status.auth_expired is False
            assert status.credentials_configured is False
            # Signing in again afterwards reads as signed in, not expired.
            state["logged_in"] = True
            assert engine.check_agent("claude-code").auth_expired is False

    def test_clear_drops_it(self):
        engine, _state, run = _installed_signed_in_engine()
        engine.note_auth_failure("claude-code", AUTH_ERRORS[2])
        engine.clear_auth_failure("claude-code")
        with patch("agent_os.agents.setup_engine.subprocess.run", side_effect=run):
            assert engine.check_agent("claude-code").auth_expired is False

    def test_recording_drops_the_cached_sweep(self):
        """The card must turn on its next read, not after the 60 s TTL."""
        engine, _state, run = _installed_signed_in_engine()
        with patch("agent_os.agents.setup_engine.subprocess.run", side_effect=run):
            assert engine.check_all()[0].auth_expired is False
            engine.note_auth_failure("claude-code", AUTH_ERRORS[0])
            assert engine.check_all()[0].auth_expired is True

    def test_record_is_per_agent(self):
        engine, _state, run = _installed_signed_in_engine()
        engine.note_auth_failure("codex", AUTH_ERRORS[0])
        assert engine.auth_expired("codex") is True
        assert engine.auth_expired("claude-code") is False


# ---------------------------------------------------------------------------
# The settings routes surface and clear it
# ---------------------------------------------------------------------------

class TestRoutesSurfaceAuthExpired:

    def test_status_entry_carries_auth_expired(self, monkeypatch):
        from agent_os.api.routes import settings as settings_routes
        engine, _state, run = _installed_signed_in_engine()
        monkeypatch.setattr(settings_routes, "_setup_engine", engine)
        monkeypatch.setattr(settings_routes, "_sub_agent_config_store", None)
        engine.note_auth_failure("claude-code", AUTH_ERRORS[0])
        with patch("agent_os.agents.setup_engine.subprocess.run", side_effect=run):
            entry = settings_routes._build_sub_agent_status_entry(
                engine.check_agent("claude-code"))
        assert entry["auth_expired"] is True
        # Still dispatchable: the record is a hint to sign in again, not a
        # gate (a token refreshed elsewhere would otherwise lock the agent).
        assert entry["credentials_configured"] is True

    def test_confirmed_login_clears_it(self, monkeypatch, tmp_path):
        import asyncio
        import sys
        from agent_os.api.routes import settings as settings_routes
        engine, _state, run = _installed_signed_in_engine()
        engine.note_auth_failure("claude-code", AUTH_ERRORS[0])
        monkeypatch.setattr(settings_routes, "_setup_engine", engine)
        monkeypatch.setattr(settings_routes, "_ws_manager", None)
        monkeypatch.setattr(settings_routes, "_forget_quota", lambda slug: None)
        monkeypatch.setattr(settings_routes, "telemetry",
                            SimpleNamespace(emit=lambda *a, **k: None))
        script = tmp_path / "login.py"
        script.write_text('print("Login successful")\n', encoding="utf-8")
        command = f'"{sys.executable}" "{script}"'
        settings_routes._login_jobs["lj"] = {"slug": "claude-code", "status": "running"}
        try:
            with patch("agent_os.agents.setup_engine.subprocess.run", side_effect=run):
                asyncio.run(settings_routes._run_login_job("claude-code", "lj", command))
            assert settings_routes._login_jobs["lj"]["status"] == "complete"
        finally:
            settings_routes._login_jobs.pop("lj", None)
        assert engine.auth_expired("claude-code") is False
