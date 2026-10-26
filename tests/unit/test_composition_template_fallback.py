# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""A launcher chosen on the locate card still gets its composition rendered.

Spec 127 V4 (Windows, 2026-10-22): with dsh pointed at a launcher outside
Orbital's data dir, nothing was staged there and the dispatch failed with
"Composition template not readable". The build ships the template beside
the manifest, so the renderer falls back to that copy — which, since dsh
moved to its desktop app, is the only copy there is.
"""

import os
from types import SimpleNamespace as NS

import pytest

from agent_os.agents.installer import bundled_asset_path
from agent_os.daemon_v2.sub_agent_manager import SubAgentManager


def test_bundled_asset_path_finds_the_dsh_template_and_nothing_else():
    path = bundled_asset_path("dsh", "acp.patch.yml")
    assert path is not None and os.path.isfile(path)
    assert bundled_asset_path("dsh", "nope.yml") is None
    assert bundled_asset_path("no-such-agent", "acp.patch.yml") is None


def test_render_falls_back_to_the_bundled_template(tmp_path):
    data_dir = tmp_path / "data"          # no agents/dsh/ staged here
    workspace = tmp_path / "ws"
    workspace.mkdir()
    fake_self = NS(_setup_engine=NS(data_dir=str(data_dir), sub_agent_config_store=None))
    manifest = NS(slug="dsh", runtime=NS(config_template="acp.patch.yml"))

    rendered = SubAgentManager._render_spawn_composition(
        fake_self, manifest, "dsh", str(workspace), None)

    assert os.path.isfile(rendered)
    assert rendered.startswith(str(workspace))
    with open(rendered, encoding="utf-8") as f:
        assert "deepseek-official" in f.read()


def test_render_prefers_the_installed_template_when_present(tmp_path):
    data_dir = tmp_path / "data"
    staged = data_dir / "agents" / "dsh"
    staged.mkdir(parents=True)
    src = bundled_asset_path("dsh", "acp.patch.yml")
    with open(src, encoding="utf-8") as f:
        text = f.read()
    (staged / "acp.patch.yml").write_text(
        text.replace("deepseek-official", "staged-marker-provider"), encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    fake_self = NS(_setup_engine=NS(data_dir=str(data_dir), sub_agent_config_store=None))
    manifest = NS(slug="dsh", runtime=NS(config_template="acp.patch.yml"))

    rendered = SubAgentManager._render_spawn_composition(
        fake_self, manifest, "dsh", str(workspace), None)
    with open(rendered, encoding="utf-8") as f:
        assert "staged-marker-provider" in f.read()
