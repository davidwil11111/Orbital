# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 117 D7 — granted folders are readable by read/grep/glob in safe mode.

``_compute_scope_roots(..., include_portals=True)`` appends the provider's
portal paths for the project's own scope as secondary read roots when the
project is in safe mode only. The scratch-session portal sync keeps using the
plain root list so a granted portal is never re-granted (read-only) onto
itself. The provider exposes its portals through a read-only accessor.
"""

import os
from unittest.mock import MagicMock

import pytest

from agent_os.daemon_v2.agent_manager import AgentManager
from agent_os.platform.base import PlatformProvider


def _store(projects):
    store = MagicMock()
    by_id = {p["project_id"]: p for p in projects}
    store.get_project.side_effect = lambda pid: by_id.get(pid)
    store.list_projects.side_effect = lambda: list(projects)
    return store


def _manager(projects, provider):
    return AgentManager(
        project_store=_store(projects),
        ws_manager=MagicMock(),
        sub_agent_manager=MagicMock(),
        activity_translator=MagicMock(),
        process_manager=MagicMock(),
        platform_provider=provider,
    )


class _Provider:
    """Just the accessor the manager reads; keyed by scope realpath."""

    def __init__(self, portals: dict[str, dict[str, str]]):
        self._portals = portals
        self.granted: list[tuple[str, str, str]] = []

    def portal_paths_for_scope(self, scope: str) -> dict[str, str]:
        return dict(self._portals.get(os.path.realpath(scope), {}))

    def grant_folder_access(self, path, mode, *, scope):
        self.granted.append((path, mode, scope))
        return MagicMock(success=True)

    def revoke_folder_access(self, path, *, scope):
        return MagicMock(success=True)


def _project(tmp_path, pid, *, safe_mode, is_scratch=False):
    ws = tmp_path / pid
    ws.mkdir(exist_ok=True)
    return {"project_id": pid, "name": pid, "workspace": str(ws),
            "is_scratch": is_scratch, "safe_mode": safe_mode}


def test_portals_become_secondary_roots_in_safe_mode(tmp_path):
    p = _project(tmp_path, "p1", safe_mode=True)
    granted = tmp_path / "granted"
    granted.mkdir()
    provider = _Provider({os.path.realpath(p["workspace"]): {str(granted): "read_only"}})
    mgr = _manager([p], provider)
    roots, labels = mgr._compute_scope_roots("p1", "s1", include_portals=True)
    assert roots == [p["workspace"], str(granted)]
    assert labels[os.path.realpath(str(granted))] == str(granted)


def test_portals_are_not_added_for_an_open_project(tmp_path):
    p = _project(tmp_path, "p1", safe_mode=False)
    granted = tmp_path / "granted"
    granted.mkdir()
    provider = _Provider({os.path.realpath(p["workspace"]): {str(granted): "read_only"}})
    mgr = _manager([p], provider)
    roots, _ = mgr._compute_scope_roots("p1", "s1", include_portals=True)
    assert roots == [p["workspace"]]


def test_default_call_is_unchanged(tmp_path):
    p = _project(tmp_path, "p1", safe_mode=True)
    granted = tmp_path / "granted"
    granted.mkdir()
    provider = _Provider({os.path.realpath(p["workspace"]): {str(granted): "read_only"}})
    mgr = _manager([p], provider)
    roots, labels = mgr._compute_scope_roots("p1", "s1")
    assert roots == [p["workspace"]] and labels == {}


def test_the_workspace_itself_is_never_doubled(tmp_path):
    """The macOS provider lists the working dir as its own read_write portal."""
    p = _project(tmp_path, "p1", safe_mode=True)
    provider = _Provider({os.path.realpath(p["workspace"]): {p["workspace"]: "read_write"}})
    mgr = _manager([p], provider)
    roots, _ = mgr._compute_scope_roots("p1", "s1", include_portals=True)
    assert roots == [p["workspace"]]


def test_scratch_portal_sync_does_not_regrant_portals_onto_themselves(tmp_path):
    scratch = _project(tmp_path, "scratch", safe_mode=True, is_scratch=True)
    other = _project(tmp_path, "other", safe_mode=True)
    granted = tmp_path / "granted"
    granted.mkdir()
    provider = _Provider({os.path.realpath(scratch["workspace"]): {str(granted): "read_only"}})
    mgr = _manager([scratch, other], provider)
    mgr._sync_scratch_read_portals("scratch", "s1", scratch["workspace"])
    granted_paths = [g[0] for g in provider.granted]
    assert os.path.realpath(other["workspace"]) in granted_paths
    assert str(granted) not in granted_paths


def test_no_provider_is_a_noop(tmp_path):
    p = _project(tmp_path, "p1", safe_mode=True)
    mgr = _manager([p], None)
    roots, _ = mgr._compute_scope_roots("p1", "s1", include_portals=True)
    assert roots == [p["workspace"]]


def test_base_provider_accessor_defaults_to_empty():
    assert PlatformProvider.portal_paths_for_scope(MagicMock(), "/x") == {}


def test_macos_provider_accessor_returns_a_copy_of_its_scope(tmp_path):
    from agent_os.platform.macos.provider import MacOSPlatformProvider
    provider = MacOSPlatformProvider()
    ws = str(tmp_path / "ws")
    os.makedirs(ws)
    provider.grant_folder_access("/tmp/granted", "read", scope=ws)
    got = provider.portal_paths_for_scope(ws)
    assert got == {"/tmp/granted": "read"}
    got["/tmp/other"] = "read_write"
    assert provider.portal_paths_for_scope(ws) == {"/tmp/granted": "read"}
    assert provider.portal_paths_for_scope(str(tmp_path / "elsewhere")) == {}


@pytest.mark.asyncio
async def test_macos_clear_network_drops_pending_rules_and_stops_the_proxy():
    from agent_os.platform.macos.provider import MacOSPlatformProvider
    from agent_os.platform.types import NetworkRules

    provider = MacOSPlatformProvider()
    provider.configure_network("p1", NetworkRules(mode="allowlist", domains=["x.com"]))
    proxy = MagicMock()

    async def _stop():
        proxy.stopped = True
    proxy.stop = _stop
    provider._proxies["p1"] = proxy
    await provider.clear_network("p1")
    assert "p1" not in provider._pending_rules
    assert "p1" not in provider._proxies
    assert proxy.stopped is True
    await provider.clear_network("p1")  # idempotent
