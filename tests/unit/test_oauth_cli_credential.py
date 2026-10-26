# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for oauth_cli credential type.

Tests cover: ManifestCredential new fields, SetupEngine.check_credentials()
with oauth_cli type, _check_cli_auth() success/failure/timeout, and
_build_actions() producing run_cli_auth action.
"""

import json
import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from agent_os.agents.manifest import (
    ManifestCredential,
    ManifestLoader,
    ManifestRuntime,
    ManifestSetup,
    AgentManifest,
)
from agent_os.agents.registry import AgentRegistry
from agent_os.agents.setup_engine import SetupEngine


class TestManifestOAuthCLIFields:
    """Test ManifestCredential has oauth_cli fields."""

    def test_default_credential_fields(self):
        cred = ManifestCredential(key="test", label="Test")
        assert cred.type == "secret"
        assert cred.check_command == ""
        assert cred.check_field == ""
        assert cred.check_value == ""
        assert cred.setup_command == ""
        assert cred.setup_label == ""

    def test_oauth_cli_credential_fields(self):
        cred = ManifestCredential(
            key="claude_auth",
            label="Claude Account",
            type="oauth_cli",
            check_command="claude auth status --json",
            check_field="loggedIn",
            check_value="true",
            setup_command="claude login",
            setup_label="Log in to Claude",
        )
        assert cred.type == "oauth_cli"
        assert cred.check_command == "claude auth status --json"
        assert cred.check_field == "loggedIn"
        assert cred.check_value == "true"
        assert cred.setup_command == "claude login"
        assert cred.setup_label == "Log in to Claude"

    def test_manifest_loads_oauth_cli_credential_fields(self):
        path = os.path.join(
            os.path.dirname(__file__), os.pardir, os.pardir,
            "agent_os", "agents", "manifests", "claude_code.yaml",
        )
        path = os.path.normpath(path)
        m = ManifestLoader.load(path)
        assert len(m.setup.credentials) >= 1
        cred = m.setup.credentials[0]
        assert cred.key == "claude_auth"
        assert cred.type == "oauth_cli"
        assert cred.check_command == "claude auth status --json"
        assert cred.check_field == "loggedIn"
        assert cred.check_value == "true"
        # `claude auth login`, NOT `claude login`. Claude Code retired the
        # top-level `login` subcommand, so `claude login` parsed as a positional
        # PROMPT: the CLI answered conversationally and exited 0, which the login
        # job read as success. This assertion used to pin that dead string, and
        # so certified the bug it should have caught. Do not "restore" it.
        assert cred.setup_command == "claude auth login"
        assert cred.setup_label == "Log in to Claude"


def _make_manifest_with_oauth_cli():
    """Helper to create a manifest with an oauth_cli credential."""
    return AgentManifest(
        manifest_version="1",
        name="Test Agent",
        slug="test-agent",
        description="Test",
        author="test",
        version="1.0.0",
        runtime=ManifestRuntime(adapter="cli", command="test-cmd"),
        setup=ManifestSetup(
            credentials=[
                ManifestCredential(
                    key="test_auth",
                    label="Test Auth",
                    type="oauth_cli",
                    required=True,
                    check_command="test-cmd auth status --json",
                    check_field="loggedIn",
                    check_value="true",
                    setup_command="test-cmd login",
                    setup_label="Log in to Test",
                ),
            ],
        ),
    )


class TestCheckCredentialsOAuthCLI:
    """Test SetupEngine.check_credentials() with oauth_cli type."""

    def test_check_credentials_oauth_cli_success(self):
        registry = AgentRegistry()
        manifest = _make_manifest_with_oauth_cli()
        registry.register(manifest)
        engine = SetupEngine(registry=registry)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"loggedIn": True})

        with patch("agent_os.agents.setup_engine.subprocess.run", return_value=mock_result):
            ok, missing = engine.check_credentials(manifest)

        assert ok is True
        assert missing == []

    def test_check_credentials_oauth_cli_not_logged_in(self):
        registry = AgentRegistry()
        manifest = _make_manifest_with_oauth_cli()
        registry.register(manifest)
        engine = SetupEngine(registry=registry)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"loggedIn": False})

        with patch("agent_os.agents.setup_engine.subprocess.run", return_value=mock_result):
            ok, missing = engine.check_credentials(manifest)

        assert ok is False
        assert "test_auth" in missing

    def test_check_credentials_oauth_cli_command_fails(self):
        registry = AgentRegistry()
        manifest = _make_manifest_with_oauth_cli()
        registry.register(manifest)
        engine = SetupEngine(registry=registry)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("agent_os.agents.setup_engine.subprocess.run", return_value=mock_result):
            ok, missing = engine.check_credentials(manifest)

        assert ok is False
        assert "test_auth" in missing

    def test_check_credentials_oauth_cli_timeout(self):
        registry = AgentRegistry()
        manifest = _make_manifest_with_oauth_cli()
        registry.register(manifest)
        engine = SetupEngine(registry=registry)

        with patch("agent_os.agents.setup_engine.subprocess.run",
                    side_effect=subprocess.TimeoutExpired("test-cmd", 10)):
            ok, missing = engine.check_credentials(manifest)

        # Spec 136 decision 2: a probe that never answered is not a "signed
        # out" verdict. With no earlier answer to keep, the sign-in is
        # unknown, which does not count as missing (no Login button).
        assert ok is True
        assert missing == []

    def test_check_credentials_oauth_cli_invalid_json(self):
        registry = AgentRegistry()
        manifest = _make_manifest_with_oauth_cli()
        registry.register(manifest)
        engine = SetupEngine(registry=registry)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not json"

        with patch("agent_os.agents.setup_engine.subprocess.run", return_value=mock_result):
            ok, missing = engine.check_credentials(manifest)

        assert ok is False
        assert "test_auth" in missing

    def test_check_credentials_oauth_cli_missing_field(self):
        registry = AgentRegistry()
        manifest = _make_manifest_with_oauth_cli()
        registry.register(manifest)
        engine = SetupEngine(registry=registry)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"someOtherField": True})

        with patch("agent_os.agents.setup_engine.subprocess.run", return_value=mock_result):
            ok, missing = engine.check_credentials(manifest)

        assert ok is False
        assert "test_auth" in missing

    def test_check_credentials_oauth_cli_not_required_skipped(self):
        """Non-required oauth_cli credentials should be skipped."""
        manifest = AgentManifest(
            manifest_version="1", name="T", slug="t", description="",
            author="", version="1.0.0",
            runtime=ManifestRuntime(adapter="cli", command="t"),
            setup=ManifestSetup(
                credentials=[
                    ManifestCredential(
                        key="opt_auth", label="Optional",
                        type="oauth_cli", required=False,
                        check_command="t auth --json",
                        check_field="loggedIn", check_value="true",
                    ),
                ],
            ),
        )
        registry = AgentRegistry()
        registry.register(manifest)
        engine = SetupEngine(registry=registry)

        # No subprocess mock needed — should skip entirely
        ok, missing = engine.check_credentials(manifest)
        assert ok is True
        assert missing == []


class TestBuildActionsOAuthCLI:
    """Test _build_actions() produces run_cli_auth for oauth_cli credentials."""

    def test_build_actions_oauth_cli_produces_run_cli_auth_action(self):
        registry = AgentRegistry()
        manifest = _make_manifest_with_oauth_cli()
        registry.register(manifest)
        engine = SetupEngine(registry=registry)

        actions = engine._build_actions(
            manifest, installed=True, missing_deps=[], missing_creds=["test_auth"],
        )

        assert len(actions) == 1
        action = actions[0]
        assert action.action == "run_cli_auth"
        assert action.label == "Log in to Test"
        assert action.command == "test-cmd login"
        assert action.blocking is True

    def test_build_actions_regular_credential_still_works(self):
        manifest = AgentManifest(
            manifest_version="1", name="T", slug="t", description="",
            author="", version="1.0.0",
            runtime=ManifestRuntime(adapter="cli", command="t"),
            setup=ManifestSetup(
                credentials=[
                    ManifestCredential(
                        key="API_KEY", label="API Key",
                        type="secret", required=True,
                    ),
                ],
            ),
        )
        registry = AgentRegistry()
        registry.register(manifest)
        engine = SetupEngine(registry=registry)

        actions = engine._build_actions(
            manifest, installed=True, missing_deps=[], missing_creds=["API_KEY"],
        )

        assert len(actions) == 1
        assert actions[0].action == "configure_credential"
        assert actions[0].label == "Set API Key"


class TestDottedCheckField:
    """``check_field`` may be a dotted path into the probe's JSON (Kimi Code's
    ``kimi provider list --json`` nests the login under providers.<id>)."""

    def _engine_and_manifest(self, check_field="providers.managed:kimi-code.type",
                             check_value="kimi"):
        registry = AgentRegistry()
        manifest = _make_manifest_with_oauth_cli()
        cred = manifest.setup.credentials[0]
        cred.check_field = check_field
        cred.check_value = check_value
        registry.register(manifest)
        return SetupEngine(registry=registry), manifest

    def _run(self, engine, manifest, payload):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(payload)
        with patch("agent_os.agents.setup_engine.subprocess.run", return_value=mock_result):
            return engine.check_credentials(manifest)

    def test_nested_value_matches(self):
        engine, manifest = self._engine_and_manifest()
        ok, missing = self._run(engine, manifest, {
            "providers": {"managed:kimi-code": {"type": "kimi", "oauth": {"storage": "file"}}},
            "models": {},
        })
        assert (ok, missing) == (True, [])

    def test_logged_out_shape_is_missing(self):
        engine, manifest = self._engine_and_manifest()
        ok, missing = self._run(engine, manifest, {"providers": {}, "models": {}})
        assert ok is False
        assert missing == ["test_auth"]

    def test_non_dict_intermediate_is_missing_not_a_crash(self):
        engine, manifest = self._engine_and_manifest()
        ok, missing = self._run(engine, manifest, {"providers": ["managed:kimi-code"]})
        assert ok is False
        assert missing == ["test_auth"]

    def test_top_level_field_still_works(self):
        engine, manifest = self._engine_and_manifest(check_field="loggedIn", check_value="true")
        ok, _ = self._run(engine, manifest, {"loggedIn": True})
        assert ok is True


class TestOAuthCLIWithoutProbe:
    """An oauth_cli credential with no check_command (MiniMax Code: the CLI has
    no status subcommand) is reported as the ``unknown`` credential state —
    dispatchable, never "missing" — instead of a login Orbital cannot see."""

    def _manifest(self):
        manifest = _make_manifest_with_oauth_cli()
        cred = manifest.setup.credentials[0]
        cred.check_command = ""
        cred.check_field = ""
        cred.check_value = ""
        cred.setup_command = ""
        return manifest

    def test_not_missing_and_no_cli_spawned(self):
        registry = AgentRegistry()
        manifest = self._manifest()
        registry.register(manifest)
        engine = SetupEngine(registry=registry)
        with patch("agent_os.agents.setup_engine.subprocess.run") as run:
            ok, missing = engine.check_credentials(manifest)
        assert (ok, missing) == (True, [])
        run.assert_not_called()

    def test_status_reports_unknown_credential_state(self):
        registry = AgentRegistry()
        manifest = self._manifest()
        registry.register(manifest)
        engine = SetupEngine(registry=registry)
        with patch.object(engine, "resolve_binary", return_value="/usr/local/bin/test-cmd"), \
             patch.object(engine, "_run_check_command", return_value="1.0.0"), \
             patch.object(engine, "check_dependencies", return_value=(True, [])):
            status = engine.check_agent(manifest.slug)
        assert status.credentials_configured is True
        assert status.missing_credentials == []
        assert status.credential_state == "unknown"

    def test_real_manifest_minimax_code_is_unknown(self):
        from agent_os.agents.manifest import ManifestLoader
        path = os.path.normpath(os.path.join(
            os.path.dirname(__file__), os.pardir, os.pardir,
            "agent_os", "agents", "manifests", "minimax-code.yaml"))
        manifest = ManifestLoader.load(path)
        registry = AgentRegistry()
        registry.register(manifest)
        engine = SetupEngine(registry=registry)
        missing, state = engine._evaluate_credentials(manifest, "/opt/homebrew/bin/mcode")
        assert (missing, state) == ([], "unknown")
