# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Where an agent comes from, and whether it actually answers.

The four things a dispatch needs (a program, a protocol that matches, a
working credential, a process spawned with today's settings) each fail on
their own. This file covers the first three on the setup-engine side:

* sources — a vendor desktop app before or after the standalone CLI, and a
  pinned binary that heals when its file disappears;
* the ACP handshake as the readiness verdict (only "failed" blocks);
* a key the agent keeps in its own store (DSH Desktop), which wins over
  anything Orbital injects.
"""

from __future__ import annotations

import asyncio
import logging
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent_os.agents.desktop_apps import DesktopApp, DesktopAppHit, DesktopAppPlatform
from agent_os.agents.manifest import (
    AgentManifest,
    ManifestCredential,
    ManifestRuntime,
    ManifestSetup,
)
from agent_os.agents.registry import AgentRegistry
from agent_os.agents.setup_engine import SetupEngine, agent_store_has
from agent_os.agents.setup_types import AgentSetupStatus, status_ready


def _manifest(slug="acpy", *, transport="acp-sdk", desktop_order=None,
              credentials=None, check_command=None):
    apps = []
    if desktop_order:
        apps = [DesktopApp(label="Vendor App", order=desktop_order, platforms={
            "macos": DesktopAppPlatform(executable="Contents/bin/acpy"),
            "windows": DesktopAppPlatform(executable="bin\\acpy.cmd"),
            "linux": DesktopAppPlatform(executable="bin/acpy"),
        })]
    return AgentManifest(
        manifest_version="1", name="Acpy", slug=slug, description="", author="",
        version="1.0.0",
        runtime=ManifestRuntime(adapter="cli", command="acpy", transport=transport,
                                mode="pipe", args=["acp"]),
        setup=ManifestSetup(desktop_apps=apps, credentials=credentials or [],
                            check_command=check_command),
    )


def _engine(manifest, tmp_path, **kw):
    registry = AgentRegistry()
    registry.register(manifest)
    return SetupEngine(registry, data_dir=str(tmp_path / "data"), **kw)


def _exe(tmp_path, name):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return str(path)


def _desktop(executable):
    def _find(app, os_name, **_kw):
        if executable is None:
            return None
        return DesktopAppHit(label=app.label, app_path=os.path.dirname(executable),
                             executable=executable, version="3.1")
    return patch("agent_os.agents.desktop_apps.find_desktop_app", _find)


def _which(path):
    return patch("agent_os.agents.setup_engine.shutil.which", return_value=path)


# ---------------------------------------------------------------------------
# Sources and their order
# ---------------------------------------------------------------------------

def test_after_cli_desktop_app_loses_to_a_standalone_cli(tmp_path):
    cli = _exe(tmp_path, "cli/acpy")
    app = _exe(tmp_path, "Vendor.app/Contents/bin/acpy")
    engine = _engine(_manifest(desktop_order="after_cli"), tmp_path)
    with _desktop(app), _which(cli):
        assert engine.resolve_binary(engine._registry.get("acpy")) == cli
    assert engine.resolved_source("acpy") == {"kind": "path"}


def test_after_cli_desktop_app_is_used_when_no_cli_exists(tmp_path):
    app = _exe(tmp_path, "Vendor.app/Contents/bin/acpy")
    engine = _engine(_manifest(desktop_order="after_cli"), tmp_path)
    with _desktop(app), _which(None):
        assert engine.resolve_binary(engine._registry.get("acpy")) == app
    source = engine.resolved_source("acpy")
    assert source["kind"] == "desktop_app" and source["app_version"] == "3.1"


def test_before_cli_desktop_app_wins(tmp_path):
    cli = _exe(tmp_path, "cli/acpy")
    app = _exe(tmp_path, "Vendor.app/Contents/bin/acpy")
    engine = _engine(_manifest(desktop_order="before_cli"), tmp_path)
    with _desktop(app), _which(cli):
        assert engine.resolve_binary(engine._registry.get("acpy")) == app


def test_a_desktop_app_never_lands_on_the_daemon_path(tmp_path, monkeypatch):
    app = _exe(tmp_path, "Vendor.app/Contents/bin/acpy")
    monkeypatch.setenv("PATH", "/usr/bin")
    engine = _engine(_manifest(desktop_order="before_cli"), tmp_path)
    with _desktop(app):
        engine.resolve_binary(engine._registry.get("acpy"))
    assert os.environ["PATH"] == "/usr/bin"


def test_a_pinned_binary_that_disappears_is_resolved_again(tmp_path):
    """An app update that moves its CLI, or a self-updating CLI that
    relocates (the claude-code "signed out" bug), must not read
    "installed" with a dead path until the daemon restarts."""
    first = _exe(tmp_path, "old/acpy")
    second = _exe(tmp_path, "new/acpy")
    manifest = _manifest()
    engine = _engine(manifest, tmp_path)
    with _which(first):
        assert engine.resolve_binary(manifest) == first
    os.unlink(first)
    with _which(second):
        assert engine.resolve_binary(manifest) == second
        assert engine.get_adapter_config("acpy", "/ws")["command"] == second


def test_a_pinned_binary_that_still_exists_is_not_re_resolved(tmp_path):
    first = _exe(tmp_path, "old/acpy")
    manifest = _manifest()
    engine = _engine(manifest, tmp_path)
    with _which(first):
        engine.resolve_binary(manifest)
    with _which("/somewhere/else"):
        assert engine.resolve_binary(manifest) == first


# ---------------------------------------------------------------------------
# Handshake verdict
# ---------------------------------------------------------------------------

def _handshake(state, detail=None):
    calls = []

    def _fake(argv, **kw):
        calls.append((argv, kw))
        return {"state": state, "detail": detail, "agent_name": None,
                "agent_version": None}
    return patch("agent_os.agents.handshake.acp_handshake", _fake), calls


def test_acp_agent_status_carries_the_handshake(tmp_path):
    binary = _exe(tmp_path, "cli/acpy")
    engine = _engine(_manifest(), tmp_path)
    fake, calls = _handshake("ok")
    with _which(binary), fake:
        status = engine.check_agent("acpy")
    assert status.handshake["state"] == "ok"
    # The manifest's own args: the process a dispatch would start.
    assert calls[0][0] == [binary, "acp"]
    assert status_ready(status)


def test_failed_handshake_makes_the_agent_not_ready(tmp_path):
    binary = _exe(tmp_path, "cli/acpy")
    engine = _engine(_manifest(), tmp_path)
    fake, _ = _handshake("failed", "error: --profile <name> is required")
    with _which(binary), fake:
        status = engine.check_agent("acpy")
    assert status.installed is True
    assert status.handshake == {"state": "failed",
                                "detail": "error: --profile <name> is required",
                                "agent_name": None, "agent_version": None}
    assert status_ready(status) is False


def test_unknown_handshake_does_not_block(tmp_path):
    binary = _exe(tmp_path, "cli/acpy")
    engine = _engine(_manifest(), tmp_path)
    fake, _ = _handshake("unknown", "No answer to ACP initialize within 20 s.")
    with _which(binary), fake:
        status = engine.check_agent("acpy")
    assert status_ready(status) is True


def test_a_verdict_is_cached_until_the_binary_changes(tmp_path):
    binary = _exe(tmp_path, "cli/acpy")
    engine = _engine(_manifest(), tmp_path)
    fake, calls = _handshake("ok")
    with _which(binary), fake:
        engine.check_agent("acpy")
        engine.check_agent("acpy")
        assert len(calls) == 1
        # An update rewrites the file: a new identity, a new handshake.
        with open(binary, "a") as f:
            f.write("# updated\n")
        engine.check_agent("acpy")
    assert len(calls) == 2


def test_a_timeout_is_asked_again_next_sweep(tmp_path):
    binary = _exe(tmp_path, "cli/acpy")
    engine = _engine(_manifest(), tmp_path)
    fake, calls = _handshake("unknown")
    with _which(binary), fake:
        engine.check_agent("acpy")
        engine.check_agent("acpy")
    assert len(calls) == 2


def test_non_acp_agents_are_not_handshaken(tmp_path):
    binary = _exe(tmp_path, "cli/acpy")
    engine = _engine(_manifest(transport="sdk"), tmp_path)
    fake, calls = _handshake("failed")
    with _which(binary), fake:
        status = engine.check_agent("acpy")
    assert status.handshake is None and calls == []


def test_an_uninstalled_agent_is_not_handshaken(tmp_path):
    engine = _engine(_manifest(), tmp_path)
    fake, calls = _handshake("ok")
    with _which(None), fake:
        status = engine.check_agent("acpy")
    assert status.installed is False and status.handshake is None and calls == []


def test_dispatch_gate_refuses_a_failed_handshake_with_its_words():
    from agent_os.daemon_v2.sub_agent_manager import SubAgentManager

    mgr = SubAgentManager(process_manager=MagicMock())
    status = AgentSetupStatus(
        slug="dsh", name="DeepSeek Harness", installed=True, binary_path="/x/dsh",
        version="0.1.0", dependencies_met=True, missing_dependencies=[],
        credentials_configured=True, missing_credentials=[],
        handshake={"state": "failed", "detail": "error: --profile <name> is required"})

    async def _gate_status(handle):
        return status

    mgr._gate_status = _gate_status
    manifest = SimpleNamespace(name="DeepSeek Harness", setup=SimpleNamespace(credentials=[]))
    refusal = asyncio.run(mgr._pre_dispatch_gate("p", "dsh", manifest, started=0.0))
    assert refusal.startswith("Error: DeepSeek Harness was found but did not answer")
    assert "--profile <name> is required" in refusal


# ---------------------------------------------------------------------------
# The agent's own credential store
# ---------------------------------------------------------------------------

_STORE = {"file": "~/.dsh/.credentials.yaml", "home_env": "DSH_HOME",
          "key": "refs.DEEPSEEK_API_KEY", "label": "DeepSeek Harness"}


def _write_store(home, text):
    home.mkdir(parents=True, exist_ok=True)
    (home / ".credentials.yaml").write_text(text)


def test_agent_store_holds_the_key(tmp_path):
    home = tmp_path / "dsh-home"
    _write_store(home, "version: 1\nrefs:\n  DEEPSEEK_API_KEY: sk-desktop\n")
    assert agent_store_has(_STORE, {"DSH_HOME": str(home)}) is True


def test_agent_store_misses(tmp_path, caplog):
    home = tmp_path / "dsh-home"
    env = {"DSH_HOME": str(home)}
    assert agent_store_has(_STORE, env) is False                 # no file
    _write_store(home, "version: 1\nrefs:\n  OPENAI_API_KEY: sk-x\n")
    assert agent_store_has(_STORE, env) is False                 # other key
    _write_store(home, "version: 1\nrefs:\n  DEEPSEEK_API_KEY: '  '\n")
    assert agent_store_has(_STORE, env) is False                 # blank
    _write_store(home, "refs: [sk-secret-in-a-broken-file\n")
    with caplog.at_level(logging.WARNING):
        assert agent_store_has(_STORE, env) is False             # malformed
    assert "sk-secret" not in caplog.text


def test_agent_store_defaults_to_the_home_relative_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))          # what ~ is on Windows
    _write_store(tmp_path / ".dsh", "refs:\n  DEEPSEEK_API_KEY: sk-desktop\n")
    assert agent_store_has(_STORE, {}) is True


def test_a_key_in_the_agent_store_configures_the_credential(tmp_path, monkeypatch):
    home = tmp_path / "dsh-home"
    _write_store(home, "refs:\n  DEEPSEEK_API_KEY: sk-desktop\n")
    monkeypatch.setenv("DSH_HOME", str(home))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    cred = ManifestCredential(key="DEEPSEEK_API_KEY", label="DeepSeek API Key",
                              env_var="DEEPSEEK_API_KEY", agent_store=dict(_STORE))
    binary = _exe(tmp_path, "cli/acpy")
    engine = _engine(_manifest(transport="sdk", credentials=[cred]), tmp_path)
    with _which(binary):
        status = engine.check_agent("acpy")
        # Not copied into the spawn env: the agent reads its own file.
        env = engine.get_adapter_config("acpy", "/ws")["env"]
    assert status.credentials_configured is True
    assert "DEEPSEEK_API_KEY" not in env


def test_without_the_agent_store_the_orbital_key_still_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "empty-home"))
    cred = ManifestCredential(key="DEEPSEEK_API_KEY", label="DeepSeek API Key",
                              env_var="DEEPSEEK_API_KEY", agent_store=dict(_STORE))
    store = MagicMock()
    store.get.side_effect = lambda k: "sk-orbital" if k == "DEEPSEEK_API_KEY" else None
    binary = _exe(tmp_path, "cli/acpy")
    engine = _engine(_manifest(transport="sdk", credentials=[cred]), tmp_path,
                     credential_store=store)
    with _which(binary):
        assert engine.check_agent("acpy").credentials_configured is True
        assert engine.get_adapter_config("acpy", "/ws")["env"]["DEEPSEEK_API_KEY"] == "sk-orbital"


# ---------------------------------------------------------------------------
# A launcher inside an app bundle has spaces in its path
# ---------------------------------------------------------------------------

def test_version_check_quotes_a_path_with_spaces():
    resolved = "/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh"
    assert SetupEngine._substitute_binary("dsh -V", "dsh", resolved) == f'"{resolved}" -V'
    assert SetupEngine._substitute_binary("dsh -V", "dsh", "/usr/local/bin/dsh") == (
        "/usr/local/bin/dsh -V")


# ---------------------------------------------------------------------------
# The settings payload
# ---------------------------------------------------------------------------

def test_settings_entry_names_the_source_handshake_and_key_owner(tmp_path, monkeypatch):
    from agent_os.agents.manifest import ManifestLoader
    from agent_os.api.routes import settings as settings_routes

    home = tmp_path / "dsh-home"
    _write_store(home, "refs:\n  DEEPSEEK_API_KEY: sk-desktop\n")
    monkeypatch.setenv("DSH_HOME", str(home))
    manifest = ManifestLoader.load(os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "agent_os", "agents", "manifests", "dsh.yaml"))
    registry = AgentRegistry()
    registry.register(manifest)
    monkeypatch.setattr(settings_routes, "_setup_engine",
                        SetupEngine(registry, data_dir=str(tmp_path)))
    monkeypatch.setattr(settings_routes, "_sub_agent_config_store", None)
    monkeypatch.setattr("agent_os.daemon_v2.models.detect_os", lambda: "macos")
    monkeypatch.setattr("platform.machine", lambda: "arm64")
    source = {"kind": "desktop_app", "label": "DeepSeek Harness",
              "app_path": "/Applications/DeepSeek Harness.app", "app_version": "0.2.1-rc.3"}
    status = AgentSetupStatus(
        slug="dsh", name="DeepSeek Harness", installed=True,
        binary_path="/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh",
        version="0.2.1-rc.3", dependencies_met=True, missing_dependencies=[],
        credentials_configured=True, missing_credentials=[],
        source=source, handshake={"state": "ok", "detail": None})

    entry = settings_routes._build_sub_agent_status_entry(status)

    assert entry["source"] == source
    assert entry["handshake"]["state"] == "ok"
    assert entry["ready"] is True
    assert entry["desktop_app"] == {
        "label": "DeepSeek Harness",
        "download_url": "https://download.deepseek.com/desktop/dsh-latest-macos-arm64.dmg"}
    by_key = {c["key"]: c for c in entry["credentials"]}
    assert by_key["DEEPSEEK_API_KEY"]["stored_by"] == "DeepSeek Harness"
    assert by_key["DEEPSEEK_BASE_URL"]["stored_by"] is None

    failed = AgentSetupStatus(**{**status.__dict__,
                                 "handshake": {"state": "failed", "detail": "x"}})
    assert settings_routes._build_sub_agent_status_entry(failed)["ready"] is False


# ---------------------------------------------------------------------------
# The locate card's Test asks the handshake too
# ---------------------------------------------------------------------------

def test_locate_test_rejects_a_binary_that_fails_the_handshake(tmp_path):
    binary = _exe(tmp_path, "picked/acpy")
    engine = _engine(_manifest(), tmp_path)
    fake, calls = _handshake("failed", "error: --profile <name> is required")
    with fake:
        result = engine.test_override("acpy", binary)
    assert result["ok"] is False
    assert "--profile <name> is required" in result["error"]
    assert calls[0][0] == [binary, "acp"]


def test_locate_test_accepts_a_binary_that_answers(tmp_path):
    binary = _exe(tmp_path, "picked/acpy")
    engine = _engine(_manifest(), tmp_path)
    fake, _ = _handshake("ok")
    with fake:
        assert engine.test_override("acpy", binary)["ok"] is True


def test_locate_test_skips_the_handshake_for_non_acp_agents(tmp_path):
    binary = _exe(tmp_path, "picked/acpy")
    engine = _engine(_manifest(transport="sdk"), tmp_path)
    fake, calls = _handshake("failed")
    with fake:
        assert engine.test_override("acpy", binary)["ok"] is True
    assert calls == []


def test_a_picked_app_is_saved_as_the_launcher_inside_it(tmp_path, monkeypatch):
    from agent_os.api.routes import settings as settings_routes

    app_dir = tmp_path / "DeepSeek Harness.app"
    launcher = _exe(app_dir, "Contents/bin/acpy")
    manifest = _manifest(desktop_order="before_cli")
    engine = _engine(manifest, tmp_path)
    monkeypatch.setattr(settings_routes, "_setup_engine", engine)
    monkeypatch.setattr("agent_os.daemon_v2.models.detect_os", lambda: "macos")
    fake, calls = _handshake("ok")
    with fake:
        result = settings_routes._locate_test("acpy", str(app_dir))
    assert result["ok"] is True
    # The macOS branch joins with "/"; on a Windows test host the tmp path
    # itself has "\", so compare normalized.
    assert os.path.normpath(result["binary"]) == os.path.normpath(launcher)
    assert os.path.normpath(calls[0][0][0]) == os.path.normpath(launcher)


# ---------------------------------------------------------------------------
# The routine sweep never waits on a handshake (spec 136: the sweep is the
# slow part of Settings already)
# ---------------------------------------------------------------------------

def _slow_handshake(state, gate):
    def _fake(argv, **kw):
        gate.wait(10)
        return {"state": state, "detail": "slow verdict", "agent_name": None,
                "agent_version": None}
    return patch("agent_os.agents.handshake.acp_handshake", _fake)


def test_sweep_reports_a_running_handshake_as_unknown(tmp_path):
    import threading
    binary = _exe(tmp_path, "cli/acpy")
    engine = _engine(_manifest(), tmp_path)
    gate = threading.Event()
    with _which(binary), _slow_handshake("failed", gate):
        [status] = [s for s in engine.check_all() if s.slug == "acpy"]
        assert status.handshake["state"] == "unknown"
        assert status_ready(status) is True
        gate.set()
        # The verdict lands and expires the cached sweep, so the next caller
        # sees it instead of the stale "unknown" for a full TTL.
        for _ in range(100):
            if engine._check_all_cache[1] == 0.0:
                break
            import time
            time.sleep(0.05)
        [status] = [s for s in engine.check_all() if s.slug == "acpy"]
    assert status.handshake["state"] == "failed"
    assert status_ready(status) is False


def test_an_explicit_wait_returns_the_verdict(tmp_path):
    import threading
    binary = _exe(tmp_path, "cli/acpy")
    engine = _engine(_manifest(), tmp_path)
    gate = threading.Event()
    gate.set()
    with _which(binary), _slow_handshake("ok", gate):
        [status] = [s for s in engine.check_all(handshake_wait=10) if s.slug == "acpy"]
    assert status.handshake["state"] == "ok"


def test_handshakes_of_all_acp_agents_run_in_parallel(tmp_path):
    import threading
    import time
    registry = AgentRegistry()
    for slug in ("acp-a", "acp-b", "acp-c"):
        registry.register(_manifest(slug))
    engine = SetupEngine(registry, data_dir=str(tmp_path / "data"))
    binary = _exe(tmp_path, "cli/acpy")
    in_flight, peak, lock = [0], [0], threading.Lock()

    def _fake(argv, **kw):
        with lock:
            in_flight[0] += 1
            peak[0] = max(peak[0], in_flight[0])
        time.sleep(0.3)
        with lock:
            in_flight[0] -= 1
        return {"state": "ok", "detail": None, "agent_name": None, "agent_version": None}

    with _which(binary), patch("agent_os.agents.handshake.acp_handshake", _fake):
        started = time.monotonic()
        engine.check_all(handshake_wait=10)
        elapsed = time.monotonic() - started
    assert peak[0] == 3
    assert elapsed < 0.85
