# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the agent setup engine.

Tests cover: resolve_binary, check_dependencies, check_credentials,
check_agent, check_all, get_adapter_config.
"""

import os
import subprocess
from dataclasses import field
from unittest.mock import MagicMock, patch

import pytest

from agent_os.agents.manifest import (
    AgentManifest,
    ManifestCapabilities,
    ManifestCredential,
    ManifestDependency,
    ManifestPermissions,
    ManifestRuntime,
    ManifestSetup,
)
from agent_os.agents.registry import AgentRegistry
from agent_os.agents.setup_engine import SetupEngine
from agent_os.agents.setup_types import AgentSetupStatus, SetupAction


# ---------------------------------------------------------------------------
# Helpers — reusable manifest fixtures
# ---------------------------------------------------------------------------

def _make_cli_manifest(
    slug="test-agent",
    name="Test Agent",
    command="testagent",
    auto_detect=None,
    dependencies=None,
    credentials=None,
    check_command="testagent --version",
    args=None,
    approval_patterns=None,
    network_domains=None,
    interactive=False,
) -> AgentManifest:
    """Build a CLI-type AgentManifest for testing."""
    return AgentManifest(
        manifest_version="1",
        name=name,
        slug=slug,
        description="A test agent",
        author="tester",
        version="1.0.0",
        runtime=ManifestRuntime(
            adapter="cli",
            command=command,
            args=args or ["--output-format", "stream-json"],
            interactive=interactive,
            output_format="stream-json",
            approval_patterns=approval_patterns or [
                {"regex": "Proceed\\?", "type": "confirm"},
            ],
        ),
        setup=ManifestSetup(
            dependencies=dependencies or [],
            install_command="npm install -g test-agent",
            check_command=check_command,
            auto_detect=auto_detect or {},
            credentials=credentials or [],
        ),
        permissions=ManifestPermissions(
            network_domains=network_domains or ["api.example.com"],
        ),
    )


def _make_builtin_manifest() -> AgentManifest:
    """Build a built-in adapter manifest for testing."""
    return AgentManifest(
        manifest_version="1",
        name="Orbital Assistant",
        slug="built-in",
        description="Built-in management agent",
        author="agent-os",
        version="1.0.0",
        runtime=ManifestRuntime(adapter="built_in"),
    )


def _make_registry(*manifests: AgentManifest) -> AgentRegistry:
    """Build a registry pre-loaded with manifests."""
    reg = AgentRegistry()
    for m in manifests:
        reg.register(m)
    return reg


# ---------------------------------------------------------------------------
# resolve_binary tests
# ---------------------------------------------------------------------------


class TestResolveBinary:

    @patch("agent_os.agents.setup_engine.shutil.which")
    def test_resolve_binary_via_which(self, mock_which):
        """shutil.which finds the binary on PATH."""
        mock_which.return_value = "/usr/bin/testagent"
        manifest = _make_cli_manifest()
        registry = _make_registry(manifest)
        engine = SetupEngine(registry)

        result = engine.resolve_binary(manifest)

        assert result == "/usr/bin/testagent"
        mock_which.assert_called_with("testagent")
        # Cached
        assert engine.get_resolved_path("test-agent") == "/usr/bin/testagent"

    @patch("agent_os.agents.setup_engine.shutil.which", return_value=None)
    @patch("agent_os.agents.setup_engine.os.path.isfile")
    @patch("agent_os.agents.setup_engine.detect_os", return_value="linux")
    def test_resolve_binary_via_auto_detect(self, mock_os, mock_isfile, mock_which):
        """which returns None but an auto_detect path exists on disk."""
        mock_isfile.return_value = True

        manifest = _make_cli_manifest(
            auto_detect={"linux": ["/usr/local/bin/testagent"]},
        )
        registry = _make_registry(manifest)
        engine = SetupEngine(registry)

        result = engine.resolve_binary(manifest)

        assert result == "/usr/local/bin/testagent"
        assert engine.get_resolved_path("test-agent") == "/usr/local/bin/testagent"

    @patch("agent_os.agents.setup_engine.shutil.which", return_value=None)
    @patch("agent_os.agents.setup_engine.os.path.isfile", return_value=False)
    @patch("agent_os.agents.setup_engine.detect_os", return_value="linux")
    @patch("agent_os.agents.setup_engine.subprocess.run")
    def test_resolve_binary_not_found(self, mock_run, mock_os, mock_isfile, mock_which):
        """Nothing found at all — returns None."""
        mock_run.side_effect = FileNotFoundError

        manifest = _make_cli_manifest(
            auto_detect={"linux": ["/nonexistent/testagent"]},
        )
        registry = _make_registry(manifest)
        engine = SetupEngine(registry)

        result = engine.resolve_binary(manifest)

        assert result is None
        assert engine.get_resolved_path("test-agent") is None


# ---------------------------------------------------------------------------
# check_dependencies tests
# ---------------------------------------------------------------------------


class TestCheckDependencies:

    @patch("agent_os.agents.setup_engine.subprocess.run")
    def test_check_dependencies_all_met(self, mock_run):
        """All dependency check_commands succeed with valid versions."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="v20.0.0\n",
        )

        deps = [
            ManifestDependency(
                name="Node.js",
                check_command="node --version",
                min_version="18.0.0",
            ),
        ]
        manifest = _make_cli_manifest(dependencies=deps)
        registry = _make_registry(manifest)
        engine = SetupEngine(registry)

        met, missing = engine.check_dependencies(manifest)

        assert met is True
        assert missing == []

    @patch("agent_os.agents.setup_engine.subprocess.run")
    def test_check_dependencies_missing(self, mock_run):
        """Dependency check_command raises FileNotFoundError — treated as missing."""
        mock_run.side_effect = FileNotFoundError

        deps = [
            ManifestDependency(
                name="Node.js",
                check_command="node --version",
                min_version="18.0.0",
            ),
        ]
        manifest = _make_cli_manifest(dependencies=deps)
        registry = _make_registry(manifest)
        engine = SetupEngine(registry)

        met, missing = engine.check_dependencies(manifest)

        assert met is False
        assert missing == ["Node.js"]


# ---------------------------------------------------------------------------
# check_credentials tests
# ---------------------------------------------------------------------------


class TestCheckCredentials:

    def test_check_credentials_from_env(self, monkeypatch):
        """Credential found in environment variable."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key-123")

        creds = [
            ManifestCredential(
                key="ANTHROPIC_API_KEY",
                label="Anthropic API Key",
                env_var="ANTHROPIC_API_KEY",
                required=True,
            ),
        ]
        manifest = _make_cli_manifest(credentials=creds)
        registry = _make_registry(manifest)
        engine = SetupEngine(registry)

        ok, missing = engine.check_credentials(manifest)

        assert ok is True
        assert missing == []

    def test_check_credentials_missing(self, monkeypatch):
        """No env var, no credential store — credential is missing."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        creds = [
            ManifestCredential(
                key="ANTHROPIC_API_KEY",
                label="Anthropic API Key",
                env_var="ANTHROPIC_API_KEY",
                required=True,
            ),
        ]
        manifest = _make_cli_manifest(credentials=creds)
        registry = _make_registry(manifest)
        engine = SetupEngine(registry)

        ok, missing = engine.check_credentials(manifest)

        assert ok is False
        assert missing == ["ANTHROPIC_API_KEY"]


# ---------------------------------------------------------------------------
# check_agent tests
# ---------------------------------------------------------------------------


class TestCheckAgent:

    @patch("agent_os.agents.setup_engine.subprocess.run")
    @patch("agent_os.agents.setup_engine.shutil.which")
    def test_check_agent_full_status(self, mock_which, mock_run, monkeypatch):
        """Full integration: resolve binary + deps + creds -> complete status."""
        mock_which.return_value = "/usr/bin/testagent"
        mock_run.return_value = MagicMock(returncode=0, stdout="v20.0.0\n")
        monkeypatch.setenv("TEST_API_KEY", "key-123")

        deps = [
            ManifestDependency(
                name="Node.js",
                check_command="node --version",
                min_version="18.0.0",
            ),
        ]
        creds = [
            ManifestCredential(
                key="TEST_API_KEY",
                label="Test API Key",
                env_var="TEST_API_KEY",
                required=True,
            ),
        ]
        manifest = _make_cli_manifest(dependencies=deps, credentials=creds)
        registry = _make_registry(manifest)
        engine = SetupEngine(registry)

        status = engine.check_agent("test-agent")

        assert isinstance(status, AgentSetupStatus)
        assert status.slug == "test-agent"
        assert status.name == "Test Agent"
        assert status.installed is True
        assert status.binary_path == "/usr/bin/testagent"
        assert status.dependencies_met is True
        assert status.missing_dependencies == []
        assert status.credentials_configured is True
        assert status.missing_credentials == []
        assert status.setup_actions == []

    def test_check_agent_built_in(self):
        """Built-in agent always shows installed=True, no binary_path needed."""
        manifest = _make_builtin_manifest()
        registry = _make_registry(manifest)
        engine = SetupEngine(registry)

        status = engine.check_agent("built-in")

        assert status.installed is True
        assert status.binary_path is None
        assert status.version == "1.0.0"
        assert status.dependencies_met is True
        assert status.credentials_configured is True
        assert status.setup_actions == []


# ---------------------------------------------------------------------------
# check_all tests
# ---------------------------------------------------------------------------


class TestCheckAll:

    @patch("agent_os.agents.setup_engine.subprocess.run")
    @patch("agent_os.agents.setup_engine.shutil.which", return_value=None)
    @patch("agent_os.agents.setup_engine.os.path.isfile", return_value=False)
    @patch("agent_os.agents.setup_engine.detect_os", return_value="linux")
    def test_check_all_returns_all_agents(
        self, mock_detect, mock_isfile, mock_which, mock_run
    ):
        """check_all() returns status for every registered agent."""
        mock_run.side_effect = FileNotFoundError

        cli_manifest = _make_cli_manifest()
        builtin_manifest = _make_builtin_manifest()
        registry = _make_registry(cli_manifest, builtin_manifest)
        engine = SetupEngine(registry)

        results = engine.check_all()

        assert len(results) == 2
        slugs = {s.slug for s in results}
        assert slugs == {"test-agent", "built-in"}

        # Built-in is always installed
        builtin_status = next(s for s in results if s.slug == "built-in")
        assert builtin_status.installed is True


# ---------------------------------------------------------------------------
# get_adapter_config tests
# ---------------------------------------------------------------------------


class TestGetAdapterConfig:

    @patch("agent_os.agents.setup_engine.shutil.which")
    def test_get_adapter_config_builds_correctly(self, mock_which, monkeypatch):
        """Verify command is absolute path, args from manifest, env has credentials."""
        mock_which.return_value = "/usr/bin/testagent"
        monkeypatch.setenv("TEST_API_KEY", "sk-secret")

        creds = [
            ManifestCredential(
                key="TEST_API_KEY",
                label="Test Key",
                env_var="TEST_API_KEY",
                required=True,
            ),
        ]
        manifest = _make_cli_manifest(
            credentials=creds,
            args=["--output-format", "stream-json"],
            approval_patterns=[
                {"regex": "Proceed\\?", "type": "confirm"},
            ],
            network_domains=["api.example.com"],
            interactive=True,
        )
        registry = _make_registry(manifest)
        engine = SetupEngine(registry)

        # Resolve the binary first
        engine.resolve_binary(manifest)

        config = engine.get_adapter_config(
            slug="test-agent",
            project_workspace="/home/user/project",
        )

        assert config["command"] == "/usr/bin/testagent"
        assert config["args"] == ["--output-format", "stream-json"]
        assert config["workspace"] == "/home/user/project"
        assert "Proceed\\?" in config["approval_patterns"]
        assert config["env"]["TEST_API_KEY"] == "sk-secret"
        assert "api.example.com" in config["network_domains"]
        assert config["interactive"] is True

    @patch("agent_os.agents.setup_engine.shutil.which", return_value=None)
    @patch("agent_os.agents.setup_engine.os.path.isfile", return_value=False)
    @patch("agent_os.agents.setup_engine.detect_os", return_value="linux")
    @patch("agent_os.agents.setup_engine.subprocess.run")
    def test_get_adapter_config_not_installed(
        self, mock_run, mock_detect, mock_isfile, mock_which
    ):
        """Agent not installed raises ValueError."""
        mock_run.side_effect = FileNotFoundError

        manifest = _make_cli_manifest()
        registry = _make_registry(manifest)
        engine = SetupEngine(registry)

        with pytest.raises(ValueError, match="not installed"):
            engine.get_adapter_config(
                slug="test-agent",
                project_workspace="/tmp/workspace",
            )


# ---------------------------------------------------------------------------
# Spec 116 D1/D4 — version floor (``setup.min_version``)
# ---------------------------------------------------------------------------

class TestVersionFloor:
    """An installed CLI older than the manifest's floor is its own state:
    ``update_needed`` set, ``ready`` false, an ``update_agent`` action. A
    missing version or a manifest without a floor never trips it."""

    @staticmethod
    def _engine(version_output, min_version="2.1.114", update_command="testagent update"):
        manifest = _make_cli_manifest()
        manifest.setup.min_version = min_version
        manifest.setup.update_command = update_command
        manifest.setup.update_label = "Update Test Agent"
        registry = AgentRegistry()
        registry.register(manifest)
        engine = SetupEngine(registry)
        engine._resolved_paths["test-agent"] = "/usr/local/bin/testagent"
        engine._run_check_command = MagicMock(return_value=version_output)
        engine.check_dependencies = MagicMock(return_value=(True, []))
        engine._evaluate_credentials = MagicMock(return_value=([], None))
        return engine

    def test_version_below_the_floor_needs_update(self):
        status = self._engine("2.1.100 (Claude Code)").check_agent("test-agent")
        assert status.update_needed is True
        assert status.min_version == "2.1.114"
        assert status.installed is True
        actions = [a for a in status.setup_actions if a.action == "update_agent"]
        assert len(actions) == 1
        assert actions[0].command == "testagent update"
        assert actions[0].label == "Update Test Agent"
        assert actions[0].blocking is True

    @pytest.mark.parametrize("version", ["2.1.114 (Claude Code)", "2.1.302 (Claude Code)",
                                         "v3.0.0"])
    def test_version_at_or_above_the_floor_is_fine(self, version):
        status = self._engine(version).check_agent("test-agent")
        assert status.update_needed is False
        assert status.min_version == "2.1.114"
        assert not [a for a in status.setup_actions if a.action == "update_agent"]

    def test_unreadable_version_does_not_apply_the_floor(self):
        status = self._engine(None).check_agent("test-agent")
        assert status.version is None
        assert status.update_needed is False
        assert not [a for a in status.setup_actions if a.action == "update_agent"]

    def test_manifest_without_a_floor_never_needs_update(self):
        status = self._engine("0.0.1", min_version=None).check_agent("test-agent")
        assert status.update_needed is False
        assert status.min_version is None

    def test_claude_code_manifest_floor_parses(self):
        from agent_os.agents.manifest import ManifestLoader

        path = os.path.join(os.path.dirname(__file__), "..", "..", "agent_os",
                            "agents", "manifests", "claude_code.yaml")
        manifest = ManifestLoader.load(os.path.abspath(path))
        assert manifest.setup.min_version == "2.1.114"
        assert manifest.setup.update_command == "claude update"
        assert manifest.setup.update_label
        assert SetupEngine._version_gte("2.1.114", manifest.setup.min_version)
        assert not SetupEngine._version_gte("2.1.100", manifest.setup.min_version)

    def test_cached_status_reads_the_last_sweep_without_probing(self):
        engine = self._engine("2.1.100 (Claude Code)")
        assert engine.cached_status("test-agent") is None
        engine.check_all()
        engine._run_check_command.reset_mock()
        cached = engine.cached_status("test-agent")
        assert cached is not None and cached.update_needed is True
        engine._run_check_command.assert_not_called()
        assert engine.cached_status("nope") is None
