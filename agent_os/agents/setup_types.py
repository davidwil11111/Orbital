# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Setup status types for agent installation and configuration checks."""

from dataclasses import dataclass, field


@dataclass
class SetupAction:
    """A single action needed to get an agent ready."""
    action: str         # "install_dependency" | "install_agent" | "configure_credential" | "update_agent" | "none"
    label: str          # human-readable: "Install Node.js 18+"
    command: str | None  # suggested command, or None for credential prompts
    blocking: bool      # can't proceed without this


@dataclass
class AgentSetupStatus:
    """Full setup status for a single agent."""
    slug: str
    name: str
    installed: bool
    binary_path: str | None        # resolved absolute path to binary
    version: str | None            # detected version string
    dependencies_met: bool
    missing_dependencies: list[str]
    credentials_configured: bool
    missing_credentials: list[str]
    setup_actions: list[SetupAction] = field(default_factory=list)
    # "configured" | "missing" | "unknown", set only by a ``model_provider``
    # credential (Pi). "unknown" is dispatchable but not verified. None for
    # every other agent, whose state is ``credentials_configured`` alone.
    credential_state: str | None = None
    # Spec 116 D4: the installed CLI is older than the manifest's
    # ``setup.min_version`` floor. Its own state, distinct from "needs
    # login": ``ready`` is false while it is set, and the UI says "update".
    # ``min_version`` is echoed so the message can name the floor. False/None
    # when the version could not be read or the manifest declares no floor.
    update_needed: bool = False
    min_version: str | None = None
    # A dispatch of this agent failed with an authentication error ("Failed
    # to authenticate", "OAuth token has expired", "could not be refreshed")
    # while its status probe still says signed in: the probe reads the local
    # token file and cannot see the expiry (1018 bench item 9). The card asks
    # the user to sign in again. Not a dispatch gate.
    auth_expired: bool = False
    # The locate card: the launcher the user chose for this agent (None when
    # detection found it on its own). ``override_missing`` is that file
    # having disappeared since — the agent reads not-detected, the card says
    # which file is gone, and detection never quietly falls back to PATH.
    override_path: str | None = None
    override_missing: bool = False
    # Where the binary came from (SetupEngine.resolved_source): the card
    # names a desktop app ("DeepSeek Harness 0.2.1-rc.3") or the user's pick.
    source: dict | None = None
    # ACP agents: the ``initialize`` handshake verdict (agents/handshake.py)
    # — {"state": "ok"|"failed"|"unknown", "detail"}. Only "failed" makes
    # the agent not ready; None for agents that are not ACP.
    handshake: dict | None = None


def handshake_failed(status) -> bool:
    """The agent was found but did not answer the ACP handshake."""
    handshake = getattr(status, "handshake", None)
    return isinstance(handshake, dict) and handshake.get("state") == "failed"


def status_ready(status) -> bool:
    """Dispatchable: installed, dependencies met, credentials configured,
    not below its version floor, and not failing its handshake. The one
    definition every readiness surface shares."""
    return bool(
        getattr(status, "installed", False)
        and getattr(status, "dependencies_met", True)
        and getattr(status, "credentials_configured", True)
        and not getattr(status, "update_needed", False)
        and not handshake_failed(status)
    )
