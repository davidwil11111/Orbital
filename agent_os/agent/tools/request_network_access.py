# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""RequestNetworkAccessTool — TOFU domain grant request (always pending)."""

import json
from typing import Callable

from agent_os.daemon_v2.network_rules_builder import normalize_domain

from .base import Tool, ToolResult

# Spec 117 D3 — the answer on a project with safe mode OFF: there is no proxy
# and nothing to approve, so no pending is created.
OPEN_NETWORK_SENTENCE = (
    "The network is unrestricted in this project, no request needed. "
    "Run the command directly."
)


class RequestNetworkAccessTool(Tool):
    """Ask the user to approve a network domain for this project.

    The AutonomyInterceptor intercepts this call in EVERY preset — approval
    persists the domain to the project allowlist permanently. In hands-off
    the ask auto-denies for the current run after a timeout; the request
    stays pending in Project Settings.

    Spec 117: on an open project (safe mode OFF) the interceptor lets the
    call through and ``execute`` answers with ``OPEN_NETWORK_SENTENCE``.
    """

    def __init__(self, safe_mode_cb: Callable[[], bool] | None = None):
        self._safe_mode_cb = safe_mode_cb
        self.name = "request_network_access"
        self.description = (
            "Request permanent approval for shell/network access to a domain "
            "not on this project's allowlist. Use when a command needs an "
            "unapproved API or host (a proxy 403 means policy, not a broken "
            "network). For merely READING web content, use the browser tool "
            "instead — it needs no approval."
        )
        self.parameters = {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Bare domain to approve, e.g. api.stripe.com",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this task needs it (shown to the user)",
                },
                "intent": {
                    "type": "string",
                    "description": "What you'll do: e.g. 'fetch docs from' or "
                                   "'upload build artifacts to' (display only)",
                },
            },
            "required": ["domain", "reason"],
        }

    def execute(self, **arguments) -> ToolResult:
        if self._safe_mode_cb is not None:
            try:
                if not self._safe_mode_cb():
                    return ToolResult(content=OPEN_NETWORK_SENTENCE)
            except Exception:
                pass  # unreadable switch → treat as safe mode (pending as before)
        raw = arguments.get("domain", "")
        domain = normalize_domain(raw)
        if domain is None:
            return ToolResult(content=(
                f"Error: '{raw}' is not a grantable domain (bare registrable "
                f"domains only — no IPs, wildcards, or paths)."
            ))
        return ToolResult(content=json.dumps({
            "domain": domain,
            "reason": arguments.get("reason", ""),
            "intent": arguments.get("intent", ""),
            "status": "pending",
        }))
