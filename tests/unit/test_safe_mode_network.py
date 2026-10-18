# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 117 §3.5 — the network side of an open project.

``request_network_access`` answers "the network is unrestricted" with no
pending when the project is open (and the interceptor lets the call through
so the tool can say so); ``_apply_network_rules`` skips ``configure_network``
for an open project; a flip to open tears the proxy down.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_os.agent.prompt_builder import Autonomy
from agent_os.agent.tools.request_network_access import (
    OPEN_NETWORK_SENTENCE,
    RequestNetworkAccessTool,
)
from agent_os.daemon_v2.agent_manager import AgentManager
from agent_os.daemon_v2.autonomy import AutonomyInterceptor


def test_tool_returns_the_open_sentence_with_no_pending():
    tool = RequestNetworkAccessTool(safe_mode_cb=lambda: False)
    result = tool.execute(domain="api.example.com", reason="fetch")
    assert result.content == OPEN_NETWORK_SENTENCE
    assert "pending" not in result.content
    assert "unrestricted" in OPEN_NETWORK_SENTENCE


def test_tool_in_safe_mode_is_unchanged():
    tool = RequestNetworkAccessTool(safe_mode_cb=lambda: True)
    body = json.loads(tool.execute(domain="api.example.com", reason="fetch").content)
    assert body["status"] == "pending"


def test_tool_without_callback_is_unchanged():
    body = json.loads(RequestNetworkAccessTool().execute(domain="x.com", reason="r").content)
    assert body["status"] == "pending"


@pytest.mark.parametrize("preset", list(Autonomy))
def test_interceptor_lets_the_network_ask_through_when_open(preset):
    interceptor = AutonomyInterceptor(preset, MagicMock(), "p1", safe_mode_cb=lambda: False)
    tc = {"id": "t1", "name": "request_network_access", "arguments": {"domain": "x.com"}}
    assert interceptor.should_intercept(tc) is False


@pytest.mark.parametrize("preset", list(Autonomy))
def test_interceptor_still_intercepts_the_network_ask_in_safe_mode(preset):
    interceptor = AutonomyInterceptor(preset, MagicMock(), "p1", safe_mode_cb=lambda: True)
    tc = {"id": "t1", "name": "request_network_access", "arguments": {"domain": "x.com"}}
    assert interceptor.should_intercept(tc) is True


def _manager(project):
    store = MagicMock()
    store.get_project.return_value = project
    return AgentManager(
        project_store=store, ws_manager=MagicMock(), sub_agent_manager=MagicMock(),
        activity_translator=MagicMock(), process_manager=MagicMock(),
        platform_provider=MagicMock(),
    )


def test_apply_network_rules_skips_an_open_project():
    mgr = _manager({"project_id": "p1", "safe_mode": False, "approved_domains": ["x.com"]})
    mgr._apply_network_rules("p1")
    mgr._platform_provider.configure_network.assert_not_called()


def test_apply_network_rules_still_pushes_for_a_safe_project():
    mgr = _manager({"project_id": "p1", "safe_mode": True, "approved_domains": ["x.com"]})
    mgr._apply_network_rules("p1")
    mgr._platform_provider.configure_network.assert_called_once()


def test_apply_network_rules_treats_a_missing_key_as_safe():
    mgr = _manager({"project_id": "p1", "approved_domains": []})
    mgr._apply_network_rules("p1")
    mgr._platform_provider.configure_network.assert_called_once()


@pytest.mark.asyncio
async def test_flip_to_open_tears_the_network_down():
    mgr = _manager({"project_id": "p1", "safe_mode": False})
    mgr._platform_provider.clear_network = AsyncMock()
    await mgr.on_safe_mode_changed("p1")
    mgr._platform_provider.clear_network.assert_awaited_once_with("p1")
    mgr._platform_provider.configure_network.assert_not_called()


@pytest.mark.asyncio
async def test_flip_to_safe_rebuilds_the_rules():
    mgr = _manager({"project_id": "p1", "safe_mode": True, "approved_domains": ["x.com"]})
    mgr._platform_provider.clear_network = AsyncMock()
    await mgr.on_safe_mode_changed("p1")
    mgr._platform_provider.clear_network.assert_not_awaited()
    mgr._platform_provider.configure_network.assert_called_once()


@pytest.mark.asyncio
async def test_flip_with_no_provider_is_a_noop():
    mgr = _manager({"project_id": "p1", "safe_mode": False})
    mgr._platform_provider = None
    await mgr.on_safe_mode_changed("p1")
