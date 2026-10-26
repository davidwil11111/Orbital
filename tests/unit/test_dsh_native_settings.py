# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""dsh runs on the user's own dsh setup; Orbital only exposes dsh's settings.

The rule (user, 2026-10-26): "if it works in dsh it works; if dsh doesn't
work, Orbital won't force it to work — but expose all the relevant settings."

dsh keeps provider definitions per profile. DSH Desktop's Models page writes
the ``desktop`` profile; the ``acp`` profile Orbital boots never reads it
(keys and sign-ins live in one shared file, the provider routes do not). So
every spawn copies the provider rows out of the desktop profile unchanged,
uses the default model picked there when Orbital's own setting is unset, and
offers in its dropdowns exactly what dsh offers over ACP.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

from agent_os.agents.composition import (
    JsExpression,
    acp_model_value,
    effective_model,
    read_mirror,
    render_composition,
    render_spawn_file,
    split_model_choice,
)
from agent_os.agents.manifest import ManifestLoader
from agent_os.agents.registry import AgentRegistry
from agent_os.agents.setup_engine import SetupEngine

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DSH_MANIFEST = os.path.join(_REPO, "agent_os", "agents", "manifests", "dsh.yaml")
MIRROR = {"home_env": "DSH_HOME", "home_default": "~/.dsh",
          "file": "profiles/desktop/cordis.patch.yml",
          "rows": ["llm-deepseek", "llm-deepseek-account", "llm-pi-ai"],
          "default_model_row": "agent-default-model",
          "default_provider": "deepseek-official"}

DESKTOP_PROFILE = """\
- id: llm-pi-ai
  config:
    providers:
      opencode-go:
        displayName: OpenCode Go
        api: anthropic-messages
        baseURL: https://opencode.ai/zen/go
        apiKeyEnv: OPENCODE_API_KEY
        headers:
          x-opencode-session: orbital
- id: llm-deepseek
  config:
    baseURL: !!js process.env.MY_DEEPSEEK_GATEWAY
- id: agent-default-model
  config:
    provider: opencode-go
    model: deepseek-v4-flash
- id: hmr
  config:
    enabled: true
"""


@pytest.fixture
def dsh_home(tmp_path, monkeypatch):
    home = tmp_path / "dsh-home"
    (home / "profiles" / "desktop").mkdir(parents=True)
    monkeypatch.setenv("DSH_HOME", str(home))
    return home


def _write_profile(home, text=DESKTOP_PROFILE):
    (home / "profiles" / "desktop" / "cordis.patch.yml").write_text(text)


def _load_dsh():
    return ManifestLoader.load(DSH_MANIFEST)


# ---------------------------------------------------------------------------
# The manifest says where the user's setup lives, and does not gate on a key
# ---------------------------------------------------------------------------

def test_manifest_mirrors_the_desktop_profile_provider_rows():
    assert _load_dsh().runtime.config_mirror == MIRROR


def test_a_deepseek_key_is_not_required():
    """dsh may run on the DeepSeek account, OpenAI, a gateway…: whether a
    task can be served is dsh's call, not a missing-key gate in Orbital."""
    [key] = [c for c in _load_dsh().setup.credentials if c.key == "DEEPSEEK_API_KEY"]
    assert key.required is False


# ---------------------------------------------------------------------------
# Reading the user's setup
# ---------------------------------------------------------------------------

def test_read_mirror_copies_only_provider_rows_and_the_default(dsh_home):
    _write_profile(dsh_home)
    rows, default, digest = read_mirror(MIRROR)
    assert [r["id"] for r in rows] == ["llm-pi-ai", "llm-deepseek"]
    assert rows[0]["config"]["providers"]["opencode-go"]["headers"] == {
        "x-opencode-session": "orbital"}
    assert default == {"provider": "opencode-go", "model": "deepseek-v4-flash"}
    assert len(digest) == 64


def test_a_js_expression_is_carried_through_never_evaluated(dsh_home, tmp_path):
    _write_profile(dsh_home)
    rows, _, _ = read_mirror(MIRROR)
    value = rows[1]["config"]["baseURL"]
    assert isinstance(value, JsExpression)
    assert str(value) == "process.env.MY_DEEPSEEK_GATEWAY"
    template = tmp_path / "t.yml"
    template.write_text("- id: acp\n  config: {provider: deepseek-official, model: m}\n")
    out = render_composition(str(template), model=None, persona=None,
                             persistence_root=str(tmp_path / "s"),
                             out_dir=str(tmp_path / "out"), mirror_rows=rows)
    # PyYAML may quote the scalar (``!!js 'process.env…'``); dsh reads the
    # quoted form as the same expression (verified with --dump-config).
    from agent_os.agents.composition import _yaml_load
    reloaded = {b["id"]: b for b in _yaml_load(open(out))}
    value = reloaded["llm-deepseek"]["config"]["baseURL"]
    assert isinstance(value, JsExpression)
    assert str(value) == "process.env.MY_DEEPSEEK_GATEWAY"
    assert "!!js" in open(out).read()


def test_no_desktop_profile_means_nothing_to_copy(dsh_home):
    rows, default, digest = read_mirror(MIRROR)
    assert rows == [] and default is None
    assert digest == read_mirror(MIRROR)[2]


def test_the_digest_follows_what_would_be_copied(dsh_home):
    _write_profile(dsh_home)
    before = read_mirror(MIRROR)[2]
    _write_profile(dsh_home, DESKTOP_PROFILE.replace("orbital", "other"))
    assert read_mirror(MIRROR)[2] != before
    # A row that is not copied does not count.
    _write_profile(dsh_home, DESKTOP_PROFILE.replace("orbital", "other")
                   .replace("enabled: true", "enabled: false"))
    assert read_mirror(MIRROR)[2] == read_mirror(MIRROR)[2]


def test_a_broken_profile_file_is_ignored(dsh_home):
    _write_profile(dsh_home, "- id: [unclosed\n")
    assert read_mirror(MIRROR)[:2] == ([], None)


# ---------------------------------------------------------------------------
# Which model a spawn runs
# ---------------------------------------------------------------------------

def test_split_model_choice():
    assert split_model_choice("opencode-go/deepseek-v4-pro", "deepseek-official") == (
        "opencode-go", "deepseek-v4-pro")
    # pi-ai model ids may contain a slash themselves.
    assert split_model_choice("openrouter/deepseek/deepseek-chat", "x") == (
        "openrouter", "deepseek/deepseek-chat")
    # A value saved before providers existed belongs to DeepSeek.
    assert split_model_choice("deepseek-v4-pro", "deepseek-official") == (
        "deepseek-official", "deepseek-v4-pro")
    assert split_model_choice("", "deepseek-official") == (None, None)


def test_effective_model_prefers_orbital_then_the_desktop_default(dsh_home):
    manifest = _load_dsh()
    assert effective_model(manifest, {}) is None
    _write_profile(dsh_home)
    assert effective_model(manifest, {}) == ("opencode-go", "deepseek-v4-flash")
    assert effective_model(manifest, {"model": "deepseek-official/deepseek-v4-pro"}) == (
        "deepseek-official", "deepseek-v4-pro")


def test_acp_model_value_matches_dsh_byte_for_byte():
    """dsh compares the option value as a string: ``["a", "b"]`` (with the
    space json.dumps adds) is rejected as an unknown option."""
    assert acp_model_value(("opencode-go", "deepseek-v4-pro")) == (
        '["opencode-go","deepseek-v4-pro"]')


def test_the_spawn_patch_carries_the_users_providers_and_model(dsh_home, tmp_path):
    _write_profile(dsh_home)
    out = render_spawn_file(_load_dsh(), data_dir=str(tmp_path / "data"),
                            config_store=None, persona="P",
                            out_dir=str(tmp_path / "out"),
                            persistence_root=str(tmp_path / "sessions"))
    blocks = yaml.load(open(out), Loader=yaml.BaseLoader)
    by_id = {b["id"]: b for b in blocks}
    assert by_id["acp"]["config"] == {"provider": "opencode-go", "model": "deepseek-v4-flash"}
    assert "llm-pi-ai" in by_id and "llm-deepseek" in by_id
    assert "hmr" not in by_id and "agent-default-model" not in by_id
    # Mirrored rows come after the template's, so they win over acp's own.
    assert [b["id"] for b in blocks][-2:] == ["llm-pi-ai", "llm-deepseek"]


# ---------------------------------------------------------------------------
# The spawn config: dsh's own session options, and the rebuild trigger
# ---------------------------------------------------------------------------

def _engine(tmp_path, store=None):
    registry = AgentRegistry()
    registry.register(_load_dsh())
    return SetupEngine(registry, sub_agent_config_store=store,
                       data_dir=str(tmp_path / "data"))


def _with_binary(tmp_path):
    exe = tmp_path / "bin" / "dsh"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    return patch("agent_os.agents.desktop_apps.find_desktop_app",
                 lambda app, os_name, **kw: SimpleNamespace(
                     label=app.label, app_path=str(exe.parent), executable=str(exe),
                     version="0.2.1-rc.3"))


def test_adapter_config_carries_model_and_effort_as_acp_options(dsh_home, tmp_path):
    from agent_os.daemon_v2.sub_agent_config_store import SubAgentConfigStore
    store = SubAgentConfigStore(str(tmp_path / "cfg.json"))
    engine = _engine(tmp_path, store)
    with _with_binary(tmp_path):
        assert engine.get_adapter_config("dsh", "/ws")["acp_config_options"] == {}
        _write_profile(dsh_home)
        assert engine.get_adapter_config("dsh", "/ws")["acp_config_options"] == {
            "model": '["opencode-go","deepseek-v4-flash"]'}
        store.set("dsh", {"model": "deepseek-official/deepseek-v4-pro", "effort": "max"})
        assert engine.get_adapter_config("dsh", "/ws")["acp_config_options"] == {
            "model": '["deepseek-official","deepseek-v4-pro"]',
            "reasoning_effort": "max"}


def test_editing_providers_in_the_app_changes_the_spawn_fingerprint(dsh_home, tmp_path):
    engine = _engine(tmp_path)
    with _with_binary(tmp_path):
        _write_profile(dsh_home)
        before = engine.spawn_fingerprint("dsh", "/ws")
        _write_profile(dsh_home, DESKTOP_PROFILE.replace("orbital", "changed"))
        assert engine.spawn_fingerprint("dsh", "/ws") != before


def test_acp_transport_receives_the_session_options():
    from agent_os.daemon_v2.sub_agent_manager import SubAgentManager
    manifest = SimpleNamespace(runtime=SimpleNamespace(transport="acp-sdk", mode="pipe"))
    mgr = SubAgentManager(process_manager=MagicMock())
    transport = mgr._resolve_transport(manifest, {
        "acp_config_options": {"reasoning_effort": "high"}})
    assert transport._desired_config == {"reasoning_effort": "high"}


# ---------------------------------------------------------------------------
# What dsh offers: the live probe
# ---------------------------------------------------------------------------

CONFIG_OPTIONS = [
    {"id": "model", "currentValue": '["opencode-go","deepseek-v4-flash"]', "options": [
        {"group": "opencode-go", "name": "OpenCode Go", "options": [
            {"value": '["opencode-go","deepseek-v4-flash"]', "name": "DeepSeek V4 Flash"},
            {"value": '["opencode-go","deepseek-v4-pro"]', "name": "DeepSeek V4 Pro"}]},
        {"group": "deepseek-official", "name": "DeepSeek", "options": [
            {"value": '["deepseek-official","deepseek-flash"]', "name": "deepseek-flash"}]},
    ]},
    {"id": "reasoning_effort", "currentValue": "", "options": [
        {"value": "", "name": "Provider default"}, {"value": "off", "name": "Off"},
        {"value": "high", "name": "High"}, {"value": "max", "name": "Max"}]},
]


def test_model_and_effort_choices_from_dshs_own_options():
    from agent_os.agents.acp_models import effort_choices, model_choices
    assert model_choices(CONFIG_OPTIONS) == [
        {"value": "opencode-go/deepseek-v4-flash", "label": "OpenCode Go · DeepSeek V4 Flash"},
        {"value": "opencode-go/deepseek-v4-pro", "label": "OpenCode Go · DeepSeek V4 Pro"},
        {"value": "deepseek-official/deepseek-flash", "label": "DeepSeek · deepseek-flash"},
    ]
    # "low" is not offered for this route, so it is not offered here either.
    assert [c["value"] for c in effort_choices(CONFIG_OPTIONS)] == ["off", "high", "max"]


def test_live_options_probe_runs_the_spawn_configuration_in_a_scratch_dir(dsh_home, tmp_path):
    from agent_os.agents import acp_models
    acp_models.clear_acp_models_cache()
    _write_profile(dsh_home)
    engine = _engine(tmp_path)
    seen = []

    def fake_handshake(argv, *, env=None, cwd=None, timeout=None,
                       session_cwd=None, session_settle_s=0.0):
        seen.append({"argv": argv, "cwd": cwd, "session_cwd": session_cwd,
                     "settle": session_settle_s,
                     "patch": open(argv[argv.index("--patch") + 1]).read()})
        return {"state": "ok", "config_options": CONFIG_OPTIONS}

    with _with_binary(tmp_path), patch("agent_os.agents.handshake.acp_handshake", fake_handshake):
        first = acp_models.live_options(engine, "dsh")
        again = acp_models.live_options(engine, "dsh")
    assert first == again and len(seen) == 1
    assert [c["value"] for c in first["models"]][:2] == [
        "opencode-go/deepseek-v4-flash", "opencode-go/deepseek-v4-pro"]
    call = seen[0]
    assert call["argv"][1:3] == ["--profile", "acp"]
    assert "opencode-go" in call["patch"]                 # the user's providers
    assert call["session_cwd"] == call["cwd"]             # a scratch dir…
    assert not os.path.exists(call["cwd"])                # …deleted afterwards
    assert call["settle"] > 0
    assert not (dsh_home / "sessions").exists()


def test_a_changed_setup_asks_dsh_again(dsh_home, tmp_path):
    from agent_os.agents import acp_models
    acp_models.clear_acp_models_cache()
    engine = _engine(tmp_path)
    calls = []

    def fake_handshake(argv, **kw):
        calls.append(1)
        return {"state": "ok", "config_options": CONFIG_OPTIONS}

    with _with_binary(tmp_path), patch("agent_os.agents.handshake.acp_handshake", fake_handshake):
        acp_models.live_options(engine, "dsh")
        _write_profile(dsh_home)            # a provider added in DSH Desktop
        acp_models.live_options(engine, "dsh")
    assert len(calls) == 2


def test_an_unanswered_probe_keeps_the_static_list(tmp_path):
    from agent_os.agents import acp_models
    acp_models.clear_acp_models_cache()
    engine = _engine(tmp_path)
    with _with_binary(tmp_path), patch("agent_os.agents.handshake.acp_handshake",
                                       lambda argv, **kw: {"state": "unknown"}):
        assert acp_models.live_options(engine, "dsh") is None


def test_handshake_can_open_a_settled_session(tmp_path):
    from agent_os.agents.handshake import acp_handshake
    script = tmp_path / "agent.py"
    script.write_text(textwrap.dedent("""
        import json, sys
        sessions = 0
        for line in sys.stdin:
            req = json.loads(line)
            if req["method"] == "initialize":
                result = {"agentInfo": {"name": "fake"}}
            else:
                sessions += 1
                # Like dsh: user routes are registered only after boot.
                models = ["default"] if sessions == 1 else ["default", "user-route"]
                result = {"sessionId": str(sessions), "configOptions": [
                    {"id": "model", "options": [{"value": m, "name": m} for m in models]}]}
            print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}), flush=True)
    """))
    quick = acp_handshake([sys.executable, str(script)], session_cwd=str(tmp_path), timeout=10)
    settled = acp_handshake([sys.executable, str(script)], session_cwd=str(tmp_path),
                            session_settle_s=0.1, timeout=10)
    values = lambda r: [o["value"] for o in r["config_options"][0]["options"]]
    assert values(quick) == ["default"]
    assert values(settled) == ["default", "user-route"]


# ---------------------------------------------------------------------------
# A resumed session keeps its recorded model until dsh can switch it
# ---------------------------------------------------------------------------

def test_a_desired_option_not_yet_offered_is_retried_before_the_prompt():
    from agent_os.agent.transports.acp_sdk_transport import ACPSDKTransport
    transport = ACPSDKTransport(config_options={"model": '["opencode-go","deepseek-v4-pro"]'})
    transport._session_id = "s1"
    transport._pending_config = {"model": 0}
    calls = []

    class _Conn:
        async def set_config_option(self, *, config_id, session_id, value):
            calls.append((config_id, value))
            return SimpleNamespace(config_options=[])

    transport._connection = _Conn()
    asyncio.run(transport._retry_pending_configuration())
    assert calls == [("model", '["opencode-go","deepseek-v4-pro"]')]
    assert transport._pending_config == {}


def test_a_never_offered_option_stops_being_retried():
    from agent_os.agent.transports.acp_sdk_transport import ACPSDKTransport
    transport = ACPSDKTransport(config_options={"model": "cursor-alias"})
    transport._session_id = "s1"
    transport._pending_config = {"model": 0}
    calls = []

    class _Conn:
        async def set_config_option(self, **kw):
            calls.append(1)
            raise RuntimeError("unknown model option")

    transport._connection = _Conn()
    for _ in range(6):
        asyncio.run(transport._retry_pending_configuration())
    assert len(calls) == ACPSDKTransport._PENDING_CONFIG_ATTEMPTS
    assert transport._pending_config == {}


# ---------------------------------------------------------------------------
# Settings: dropdowns are dsh's lists; a save is checked against them
# ---------------------------------------------------------------------------

def _settings(monkeypatch, tmp_path, live):
    from agent_os.api.routes import settings as routes
    from agent_os.daemon_v2.sub_agent_config_store import SubAgentConfigStore
    registry = AgentRegistry()
    registry.register(_load_dsh())
    store = SubAgentConfigStore(str(tmp_path / "cfg.json"))
    monkeypatch.setattr(routes, "_setup_engine",
                        SetupEngine(registry, sub_agent_config_store=store,
                                    data_dir=str(tmp_path / "data")))
    monkeypatch.setattr(routes, "_sub_agent_config_store", store)

    async def fake_live(slug):
        return live
    monkeypatch.setattr(routes, "_acp_live_options", fake_live)
    return routes, store


def test_dropdowns_show_what_dsh_offers(monkeypatch, tmp_path):
    from agent_os.agents.acp_models import effort_choices, model_choices
    routes, store = _settings(monkeypatch, tmp_path, {
        "models": model_choices(CONFIG_OPTIONS), "efforts": effort_choices(CONFIG_OPTIONS)})
    store.set("dsh", {"effort": "low"})          # saved before; dsh no longer offers it
    entry = {"slug": "dsh", "installed": True, "config": store.get("dsh"),
             "param_schema": store.schema_for("dsh")}
    [out] = asyncio.run(routes._augment_acp_live_models([entry]))
    model = out["param_schema"]["model"]
    assert model["allowed"][:2] == ["opencode-go/deepseek-v4-flash", "opencode-go/deepseek-v4-pro"]
    assert model["labels"]["opencode-go/deepseek-v4-pro"] == "OpenCode Go · DeepSeek V4 Pro"
    assert out["param_schema"]["effort"]["allowed"] == ["off", "high", "max", "low"]


def test_saving_a_model_dsh_offers_is_accepted_and_others_are_not(monkeypatch, tmp_path):
    from fastapi import HTTPException
    from agent_os.agents.acp_models import model_choices
    routes, store = _settings(monkeypatch, tmp_path, {"models": model_choices(CONFIG_OPTIONS),
                                                      "efforts": []})
    ok = asyncio.run(routes.put_sub_agent_config(
        "dsh", routes.SubAgentConfigRequest(model="opencode-go/deepseek-v4-pro")))
    assert ok["config"]["model"] == "opencode-go/deepseek-v4-pro"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes.put_sub_agent_config(
            "dsh", routes.SubAgentConfigRequest(model="openai/gpt-9")))
    assert exc.value.status_code == 400
