# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The in-app Update must never leave the user silently without the CLI.

1018 Windows bench, item 5: ``claude update`` (npm method) gave up after
39 s on a slow network with the package half-removed. ``claude`` was gone,
and the card still said "installed v2.1.100" until the user clicked Refresh.

Covered here:

- every update terminal (success or failure) re-probes the CLI; a CLI that is
  no longer there ends the job as ``cli_missing_after_update`` (not
  ``nonzero_exit`` / ``unverified``) and the status cache is dropped so the
  next list read says "not installed";
- an npm install is updated with npm itself
  (``npm install -g @anthropic-ai/claude-code@latest``), resolved next to the
  CLI first and then on PATH, under a 15-minute budget; a native (or unknown)
  install keeps ``claude update``.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent_os.agents.setup_engine import SetupEngine
from agent_os.api.routes import settings as settings_routes


class _Ws:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def broadcast_global(self, payload: dict) -> None:
        self.events.append(dict(payload))

    def terminal(self) -> dict:
        finals = [e for e in self.events
                  if e["type"] in ("login.complete", "login.failed")]
        assert len(finals) == 1, f"expected one terminal event, got {finals}"
        return finals[0]


class _Telemetry:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict | None]] = []

    def emit(self, event: str, fields: dict | None = None, **_kw) -> None:
        self.events.append((event, fields))

    def field(self, event: str) -> dict:
        rows = [f for n, f in self.events if n == event]
        assert len(rows) == 1, f"expected one {event}, got {self.events}"
        return rows[0]


class _Engine:
    """``after`` is what the re-probe finds once the updater exits: a
    ``--version`` line, or None when the CLI is gone."""

    def __init__(self, after: str | None, *, installed: bool | None = None):
        self._registry = {"claude-code": object()}
        self._after = after
        self._installed = (after is not None) if installed is None else installed
        self.invalidations = 0
        self.path_invalidations: list[str] = []
        self.check_agent_calls = 0

    def invalidate_cache(self) -> None:
        self.invalidations += 1

    def invalidate_resolved_path(self, slug: str) -> None:
        self.path_invalidations.append(slug)

    def cached_status(self, slug):
        return None

    def check_agent(self, slug):
        self.check_agent_calls += 1
        detected = SetupEngine._extract_version(self._after) if self._after else None
        return SimpleNamespace(
            slug=slug, installed=self._installed, version=self._after,
            update_needed=bool(detected) and not SetupEngine._version_gte(detected, "2.1.114"),
            min_version="2.1.114",
            binary_path="/fake/bin/claude" if self._installed else None,
        )


def _stub(tmp_path, body: str) -> str:
    script = tmp_path / "fake_updater.py"
    script.write_text(body, encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


@pytest.fixture
def env(monkeypatch):
    ws = _Ws()
    tele = _Telemetry()
    monkeypatch.setattr(settings_routes, "_ws_manager", ws)
    monkeypatch.setattr(settings_routes, "telemetry", tele)
    settings_routes._login_jobs.clear()
    yield ws, tele
    settings_routes._login_jobs.clear()


def _run_update(command: str, job_id: str = "upd1") -> dict:
    settings_routes._login_jobs[job_id] = {
        "slug": "claude-code", "status": "running", "command": command, "kind": "update"}
    asyncio.run(settings_routes._run_login_job("claude-code", job_id, command, kind="update"))
    return settings_routes._login_jobs[job_id]


# ---------------------------------------------------------------------------
# The re-probe after every update terminal
# ---------------------------------------------------------------------------

class TestCliMissingAfterUpdate:

    def test_failed_update_that_removed_the_cli_says_so(self, env, tmp_path, monkeypatch):
        """The bench case: npm failed mid-way and took the CLI with it."""
        ws, tele = env
        engine = _Engine(after=None)
        monkeypatch.setattr(settings_routes, "_setup_engine", engine)
        job = _run_update(_stub(tmp_path, 'import sys\nprint("Failed to install update")\nsys.exit(1)\n'))

        final = ws.terminal()
        assert final["type"] == "login.failed"
        assert final["kind"] == "update"
        assert final["reason"] == "cli_missing_after_update"
        assert final["verified"] is False
        assert job["reason"] == "cli_missing_after_update"
        assert job["status"] == "failed"
        assert job["return_code"] == 1
        assert engine.check_agent_calls == 1
        assert tele.field("update_failed")["reason"] == "cli_missing_after_update"

    def test_exit_zero_with_no_cli_left_is_cli_missing(self, env, tmp_path, monkeypatch):
        ws, _ = env
        monkeypatch.setattr(settings_routes, "_setup_engine", _Engine(after=None))
        job = _run_update(_stub(tmp_path, 'print("Successfully updated")\n'))
        final = ws.terminal()
        assert final["type"] == "login.failed"
        assert final["reason"] == "cli_missing_after_update"
        assert job["reason"] == "cli_missing_after_update"

    def test_binary_present_but_version_unreadable_is_cli_missing(self, env, tmp_path,
                                                                  monkeypatch):
        """A shim left behind whose ``--version`` no longer runs is no CLI."""
        ws, _ = env
        monkeypatch.setattr(settings_routes, "_setup_engine",
                            _Engine(after=None, installed=True))
        _run_update(_stub(tmp_path, 'print("done")\n'))
        assert ws.terminal()["reason"] == "cli_missing_after_update"

    def test_failure_with_the_cli_intact_keeps_its_own_reason(self, env, tmp_path,
                                                              monkeypatch):
        ws, tele = env
        engine = _Engine(after="2.1.100 (Claude Code)")
        monkeypatch.setattr(settings_routes, "_setup_engine", engine)
        job = _run_update(_stub(tmp_path, 'import sys\nsys.exit(1)\n'))
        final = ws.terminal()
        assert final["type"] == "login.failed"
        assert final["reason"] == "nonzero_exit"
        assert job["reason"] == "nonzero_exit"
        # Re-probed even though the updater failed.
        assert engine.check_agent_calls == 1
        assert tele.field("update_failed")["reason"] == "nonzero_exit"

    def test_failure_drops_the_cached_detection(self, env, tmp_path, monkeypatch):
        """The card must say "not installed" on its next read, without the
        user clicking Refresh: the status cache and the resolved path are both
        dropped on a failed update too, not only on exit 0."""
        _ws, _ = env
        engine = _Engine(after=None)
        monkeypatch.setattr(settings_routes, "_setup_engine", engine)
        _run_update(_stub(tmp_path, 'import sys\nsys.exit(1)\n'))
        assert engine.invalidations >= 1
        assert engine.path_invalidations == ["claude-code"]

    def test_successful_update_is_still_verified(self, env, tmp_path, monkeypatch):
        ws, _ = env
        monkeypatch.setattr(settings_routes, "_setup_engine",
                            _Engine(after="2.1.302 (Claude Code)"))
        job = _run_update(_stub(tmp_path, 'print("ok")\n'))
        final = ws.terminal()
        assert final["type"] == "login.complete" and final["verified"] is True
        assert job["status"] == "complete" and job["reason"] is None

    def test_timeout_that_removed_the_cli_says_so(self, env, tmp_path, monkeypatch):
        ws, _ = env
        monkeypatch.setattr(settings_routes, "UPDATE_TIMEOUT_SECONDS", 0.5)
        monkeypatch.setattr(settings_routes, "_setup_engine", _Engine(after=None))
        job = _run_update(_stub(tmp_path, 'import time\ntime.sleep(30)\n'))
        final = ws.terminal()
        assert final["type"] == "login.failed"
        assert final["timed_out"] is True
        assert final["reason"] == "cli_missing_after_update"
        assert job["timed_out"] is True

    def test_login_jobs_never_reprobe_the_version(self, env, tmp_path, monkeypatch):
        """Only the update kind re-probes ``--version``; a login is judged by
        its credential re-check as before."""
        ws, _ = env
        engine = _Engine(after=None)
        engine.check_credentials = lambda manifest, binary=None: (True, [])
        engine.resolve_binary = lambda manifest: "/fake/bin/claude"
        monkeypatch.setattr(settings_routes, "_setup_engine", engine)
        monkeypatch.setattr(settings_routes, "_forget_quota", lambda slug: None)
        settings_routes._login_jobs["l1"] = {"slug": "claude-code", "status": "running"}
        asyncio.run(settings_routes._run_login_job(
            "claude-code", "l1", _stub(tmp_path, 'print("hi")\n')))
        assert ws.terminal()["type"] == "login.complete"
        assert engine.check_agent_calls == 0


class TestUpdateBudget:

    def test_update_uses_its_own_long_budget_not_the_login_idle_timeout(
            self, env, tmp_path, monkeypatch):
        """npm is silent while it downloads; the login idle timeout must not
        kill it (and leave the package half-removed)."""
        ws, _ = env
        monkeypatch.setattr(settings_routes, "LOGIN_IDLE_TIMEOUT_SECONDS", 0.3)
        monkeypatch.setattr(settings_routes, "_setup_engine",
                            _Engine(after="2.1.302 (Claude Code)"))
        _run_update(_stub(tmp_path, 'import time\ntime.sleep(1.2)\nprint("added 1 package")\n'))
        final = ws.terminal()
        assert final["type"] == "login.complete", final

    def test_budget_is_fifteen_minutes(self):
        assert settings_routes.UPDATE_TIMEOUT_SECONDS == 15 * 60

    def test_budget_is_wall_clock_not_idle(self, env, tmp_path, monkeypatch):
        """A chatty updater still stops at the budget."""
        ws, _ = env
        monkeypatch.setattr(settings_routes, "UPDATE_TIMEOUT_SECONDS", 1.0)
        monkeypatch.setattr(settings_routes, "_setup_engine",
                            _Engine(after="2.1.302 (Claude Code)"))
        body = ('import time\n'
                'for i in range(100):\n'
                '    print("progress %d" % i, flush=True)\n'
                '    time.sleep(0.1)\n')
        _run_update(_stub(tmp_path, body))
        assert ws.terminal()["timed_out"] is True


# ---------------------------------------------------------------------------
# npm installs are updated with npm
# ---------------------------------------------------------------------------

def _engine_with_binary(binary: str):
    from agent_os.agents.manifest import AgentManifest, ManifestRuntime, ManifestSetup
    from agent_os.agents.registry import AgentRegistry

    manifest = AgentManifest(
        manifest_version="1", name="Claude Code", slug="claude-code",
        description="", author="t", version="1.0.0",
        runtime=ManifestRuntime(adapter="cli", command="claude"),
        setup=ManifestSetup(update_command="claude update", min_version="2.1.114",
                            npm_package="@anthropic-ai/claude-code"),
    )
    registry = AgentRegistry()
    registry.register(manifest)
    engine = SetupEngine(registry)
    engine._resolved_paths["claude-code"] = binary
    return engine


def _split(command: str) -> list[str]:
    if os.name == "nt":
        # list2cmdline's inverse is close enough for these simple strings.
        import shlex
        return [a.strip('"') for a in shlex.split(command, posix=False)]
    import shlex
    return shlex.split(command)


class TestNpmUpdateCommand:

    def test_claude_code_manifest_declares_its_npm_package(self):
        from agent_os.agents.manifest import ManifestLoader
        path = os.path.join(os.path.dirname(settings_routes.__file__), "..", "..",
                            "agents", "manifests", "claude_code.yaml")
        manifest = ManifestLoader.load(os.path.normpath(path))
        assert manifest.setup.npm_package == "@anthropic-ai/claude-code"
        # Native installs keep the CLI's own updater.
        assert manifest.setup.update_command == "claude update"

    def test_npm_install_runs_npm_directly(self, monkeypatch, tmp_path):
        npm = tmp_path / "nodejs" / ("npm.cmd" if os.name == "nt" else "npm")
        npm.parent.mkdir()
        npm.write_text("")
        monkeypatch.setattr(settings_routes.shutil, "which",
                            lambda name: str(npm) if name == "npm" else None)
        engine = _engine_with_binary(str(tmp_path / "AppData" / "Roaming" / "npm" / "claude.cmd"))
        monkeypatch.setattr(settings_routes, "_setup_engine", engine)

        command = settings_routes._resolve_setup_command("claude-code", "update")
        argv = _split(command)
        assert argv == [str(npm), "install", "-g", "@anthropic-ai/claude-code@latest"]

    def test_npm_next_to_the_cli_wins_over_path(self, monkeypatch, tmp_path):
        """nvm-style layout: ``…/bin/claude`` and ``…/bin/npm`` side by side.
        PATH's npm may belong to another node and update the wrong tree."""
        bindir = tmp_path / "node_modules" / ".bin"
        bindir.mkdir(parents=True)
        cli = bindir / ("claude.cmd" if os.name == "nt" else "claude")
        sibling = bindir / ("npm.cmd" if os.name == "nt" else "npm")
        cli.write_text("")
        sibling.write_text("")
        monkeypatch.setattr(settings_routes.shutil, "which",
                            lambda name: "/elsewhere/npm" if name == "npm" else None)
        monkeypatch.setattr(settings_routes, "_setup_engine", _engine_with_binary(str(cli)))
        argv = _split(settings_routes._resolve_setup_command("claude-code", "update"))
        assert argv[0] == str(sibling)

    def test_path_with_spaces_is_quoted(self, monkeypatch, tmp_path):
        npm_dir = tmp_path / "Program Files" / "nodejs"
        npm_dir.mkdir(parents=True)
        npm = npm_dir / ("npm.cmd" if os.name == "nt" else "npm")
        npm.write_text("")
        monkeypatch.setattr(settings_routes.shutil, "which",
                            lambda name: str(npm) if name == "npm" else None)
        monkeypatch.setattr(settings_routes, "_setup_engine",
                            _engine_with_binary("/home/u/.npm-global/bin/claude"))
        command = settings_routes._resolve_setup_command("claude-code", "update")
        assert command.startswith(('"', "'")), command
        assert _split(command)[0] == str(npm)

    def test_native_install_keeps_claude_update(self, monkeypatch):
        monkeypatch.setattr(settings_routes.shutil, "which", lambda name: "/usr/bin/npm")
        monkeypatch.setattr(settings_routes, "_setup_engine",
                            _engine_with_binary("/home/u/.local/bin/claude"))
        assert settings_routes._resolve_setup_command("claude-code", "update") \
            == "/home/u/.local/bin/claude update"

    def test_unknown_install_kind_keeps_claude_update(self, monkeypatch):
        monkeypatch.setattr(settings_routes.shutil, "which", lambda name: "/usr/bin/npm")
        monkeypatch.setattr(settings_routes, "_setup_engine",
                            _engine_with_binary("/opt/homebrew/bin/claude"))
        assert settings_routes._resolve_setup_command("claude-code", "update") \
            == "/opt/homebrew/bin/claude update"

    def test_npm_install_without_npm_falls_back_to_claude_update(self, monkeypatch):
        monkeypatch.setattr(settings_routes.shutil, "which", lambda name: None)
        monkeypatch.setattr(settings_routes, "_setup_engine",
                            _engine_with_binary("/home/u/.npm-global/bin/claude"))
        assert settings_routes._resolve_setup_command("claude-code", "update") \
            == "/home/u/.npm-global/bin/claude update"

    def test_npm_update_runs_without_a_console_window(self, env, tmp_path, monkeypatch):
        """The runner spawns every job with ``win_no_window_flags`` (npm.cmd
        would otherwise flash a console on Windows)."""
        seen: dict = {}
        real = asyncio.create_subprocess_shell

        async def _spy(cmd, **kw):
            seen.update(kw)
            return await real(cmd, **kw)
        monkeypatch.setattr(settings_routes.asyncio, "create_subprocess_shell", _spy)
        monkeypatch.setattr(settings_routes, "_setup_engine",
                            _Engine(after="2.1.302 (Claude Code)"))
        _run_update(_stub(tmp_path, 'print("ok")\n'))
        assert seen.get("creationflags") == settings_routes.win_no_window_flags()
