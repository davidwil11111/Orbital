# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for the in-app CLI login flow (bug #64).

The bug: ``claude login`` is not a subcommand. It parses as the positional
prompt, so the CLI answers conversationally, burns an LLM turn, and **exits
0** without authenticating anything. ``_run_login_job`` keyed success off
``rc == 0`` alone, so Orbital reported a login that had not happened and the
row flipped straight back to signed-out.

Two fixes are covered, and the second is the one that generalises:

1. The manifest command is ``claude auth login``.
2. A zero exit code is necessary but not sufficient — the manifest's
   credential ``check_command`` is re-run before a login is reported as
   *confirmed*. That closes the false-success class for every agent with a
   login flow, not just claude-code.

Also covered: OSC-8/ANSI sanitising of ``login.progress`` (the CLI wraps its
URL in a terminal hyperlink, so the raw line carries the URL twice plus
control bytes) and the idle timeout that stops an abandoned flow leaking a
process forever.

No real login command runs here — the CLI is a stub script. A real
``codex login`` clears the developer's own credentials on start.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from unittest.mock import patch

import psutil
import pytest

from agent_os.agents.setup_engine import SetupEngine
from agent_os.api.routes import settings as settings_routes


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------

class _RecordingWs:
    """Captures broadcast_global payloads instead of sending them."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def broadcast_global(self, payload: dict) -> None:
        self.events.append(dict(payload))

    @property
    def types(self) -> list[str]:
        return [e["type"] for e in self.events]

    def of_type(self, type_: str) -> list[dict]:
        return [e for e in self.events if e["type"] == type_]

    def terminal(self) -> dict:
        """The single login.complete / login.failed the job ends on."""
        finals = [e for e in self.events
                  if e["type"] in ("login.complete", "login.failed")]
        assert len(finals) == 1, f"expected one terminal event, got {finals}"
        return finals[0]


class _StubEngine:
    """The SetupEngine seam ``_run_login_job`` reads for its re-check.

    ``configured`` is what the manifest's ``check_command`` would report
    *after* the login process exits.
    """

    def __init__(self, slug: str = "claude-code", configured: bool = False):
        self._manifest = object()  # opaque — the stub never inspects it
        self._registry = {slug: self._manifest}
        self._configured = configured
        self.credential_checks = 0
        self.invalidations = 0

    def invalidate_cache(self) -> None:
        self.invalidations += 1

    def resolve_binary(self, manifest) -> str:
        return "/fake/bin/claude"

    def check_credentials(self, manifest, resolved_binary=None):
        self.credential_checks += 1
        if self._configured:
            return (True, [])
        return (False, ["claude_auth"])


class _RecordingTelemetry:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict | None]] = []
        self.latched: list[str] = []

    def emit(self, event: str, fields: dict | None = None, **_kw) -> None:
        self.events.append((event, fields))

    def latch(self, milestone: str) -> None:
        self.latched.append(milestone)

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.events]


def _stub_cli(tmp_path, body: str, name: str = "fake_cli.py") -> str:
    """A shell command string running ``body`` under this interpreter.

    Double quotes so the same string works under both ``sh`` and ``cmd.exe``
    — ``_run_login_job`` spawns through the shell.
    """
    script = tmp_path / name
    script.write_text(body, encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


def _run_job(command: str, slug: str = "claude-code", job_id: str = "job1") -> None:
    """Seed the job table the way the route does, then run the job."""
    settings_routes._login_jobs[job_id] = {
        "slug": slug, "status": "running", "command": command,
    }
    asyncio.run(settings_routes._run_login_job(slug, job_id, command))


@pytest.fixture
def login_env(monkeypatch):
    """Wire the module globals ``_run_login_job`` reads. Yields (ws, engine)."""
    ws = _RecordingWs()
    engine = _StubEngine()
    monkeypatch.setattr(settings_routes, "_ws_manager", ws)
    monkeypatch.setattr(settings_routes, "_setup_engine", engine)
    settings_routes._login_jobs.clear()
    yield ws, engine
    settings_routes._login_jobs.clear()


# ---------------------------------------------------------------------------
# 1. D1 — the manifest command must exist
# ---------------------------------------------------------------------------

class TestClaudeLoginCommand:
    """``claude login`` is not a subcommand; ``claude auth login`` is."""

    @staticmethod
    def _load_claude_manifest():
        from agent_os.agents.manifest import ManifestLoader

        path = os.path.join(
            os.path.dirname(__file__),
            "..", "..", "agent_os", "agents", "manifests", "claude_code.yaml",
        )
        return ManifestLoader.load(os.path.abspath(path))

    def test_manifest_declares_auth_login(self):
        manifest = self._load_claude_manifest()
        setup_commands = [c.setup_command for c in manifest.setup.credentials
                          if c.setup_command]
        assert setup_commands, "claude-code manifest lost its login command"
        assert all(cmd.endswith("auth login") for cmd in setup_commands), (
            f"expected 'claude auth login', got {setup_commands} — a bare "
            f"'claude login' parses as a prompt and exits 0 without "
            f"authenticating"
        )

    @patch("agent_os.agents.setup_engine.shutil.which",
           return_value="/opt/fake/claude")
    def test_resolve_setup_command_ends_with_auth_login(self, _which,
                                                        monkeypatch):
        """End-to-end through the resolver the Login button calls."""
        from agent_os.agents.registry import AgentRegistry

        registry = AgentRegistry()
        registry.register(self._load_claude_manifest())
        monkeypatch.setattr(settings_routes, "_setup_engine",
                            SetupEngine(registry))

        cmd = settings_routes._resolve_setup_command("claude-code", "login")

        assert cmd, "_resolve_setup_command('claude-code', 'login') was empty"
        assert cmd.endswith("auth login"), cmd
        # F1's other half: the resolved absolute path still gets substituted
        # in even though the command is now three words.
        assert cmd == "/opt/fake/claude auth login"

    def test_substitute_binary_rewrites_multiword_command(self):
        """_substitute_binary only matches at the start of the string — the
        multi-word form must still pick up the resolved path."""
        assert SetupEngine._substitute_binary(
            "claude auth login", "claude", "/Users/x/.local/bin/claude",
        ) == "/Users/x/.local/bin/claude auth login"


# ---------------------------------------------------------------------------
# 2. The false-success class — rc == 0 is not proof of a login
# ---------------------------------------------------------------------------

class TestLoginSuccessIsVerified:
    """The regression test for the whole bug: a CLI that exits 0 without
    authenticating must not be reported as a *confirmed* success."""

    def test_exit_zero_without_auth_is_not_confirmed(self, login_env, tmp_path):
        ws, engine = login_env  # engine reports still-not-configured
        cmd = _stub_cli(tmp_path, 'print("login is not something I can run")\n')

        _run_job(cmd)

        final = ws.terminal()
        assert final.get("verified") is False, (
            "an exit-0 that left the CLI unauthenticated was reported as a "
            "confirmed login — this is bug #64"
        )
        assert settings_routes._login_jobs["job1"]["status"] != "complete"
        assert engine.credential_checks >= 1, (
            "the manifest check_command was never re-run after the login exit"
        )

    def test_unconfirmed_exit_zero_is_not_a_hard_failure(self, login_env,
                                                         tmp_path):
        """Locked decision 2: a slow keychain write must not turn a real login
        into a reported error. The event stays login.complete."""
        ws, _engine = login_env
        cmd = _stub_cli(tmp_path, 'print("Login successful.")\n')

        _run_job(cmd)

        assert ws.terminal()["type"] == "login.complete"

    def test_verified_exit_zero_is_a_confirmed_success(self, login_env,
                                                       tmp_path, monkeypatch):
        ws, _engine = login_env
        monkeypatch.setattr(settings_routes, "_setup_engine",
                            _StubEngine(configured=True))
        cmd = _stub_cli(tmp_path, 'print("Login successful.")\n')

        _run_job(cmd)

        final = ws.terminal()
        assert final["type"] == "login.complete"
        assert final["verified"] is True
        assert settings_routes._login_jobs["job1"]["status"] == "complete"

    def test_nonzero_exit_still_fails(self, login_env, tmp_path):
        ws, engine = login_env
        cmd = _stub_cli(tmp_path, 'import sys\nsys.exit(3)\n')

        _run_job(cmd)

        final = ws.terminal()
        assert final["type"] == "login.failed"
        assert final["return_code"] == 3
        assert settings_routes._login_jobs["job1"]["status"] == "failed"
        assert engine.credential_checks == 0, (
            "no point re-checking credentials when the CLI itself failed"
        )


# ---------------------------------------------------------------------------
# 3. Progress lines are sanitised before they reach the UI
# ---------------------------------------------------------------------------

# What the claude CLI actually prints: an OSC-8 terminal hyperlink, so the
# URL appears twice (target + label) wrapped in control bytes.
_URL = "https://claude.com/cai/oauth/authorize?state=abc123"
_OSC8_LINE = f"\x1b]8;;{_URL}\x1b\\{_URL}\x1b]8;;\x1b\\"


class TestProgressSanitising:

    def test_strips_osc8_hyperlink_to_a_single_url(self):
        cleaned = settings_routes._strip_terminal_escapes(_OSC8_LINE)
        assert cleaned == _URL
        assert cleaned.count("https://") == 1
        assert "\x1b" not in cleaned

    def test_strips_sgr_colour_codes(self):
        assert settings_routes._strip_terminal_escapes(
            "\x1b[1;32mOpening browser…\x1b[0m") == "Opening browser…"

    def test_keeps_a_plain_line_untouched(self):
        assert settings_routes._strip_terminal_escapes(
            "Opening browser to sign in…") == "Opening browser to sign in…"

    def test_spinner_redraws_collapse_to_the_last_frame(self):
        assert settings_routes._strip_terminal_escapes(
            "- waiting\r\\ waiting\r| done") == "| done"

    # A CRLF line terminator is NOT a spinner redraw. Getting this wrong made
    # every progress line reduce to "" on Windows (`"waiting\r\n"` → split on
    # \r → "\n" → ""), which the broadcaster skips as falsy — so the whole
    # login progress stream, sign-in URL included, vanished on the platform
    # carrying most users. Asserted on the pure function so the guard holds on
    # every platform; the original tests drove a real child process and so only
    # ever saw the host's own line ending.
    def test_crlf_terminator_is_not_treated_as_a_redraw(self):
        assert settings_routes._strip_terminal_escapes(
            "still waiting 0\r\n") == "still waiting 0"

    def test_crlf_terminated_osc8_url_survives(self):
        assert settings_routes._strip_terminal_escapes(_OSC8_LINE + "\r\n") == _URL

    def test_redraws_still_collapse_when_crlf_terminated(self):
        assert settings_routes._strip_terminal_escapes(
            "- waiting\r| done\r\n") == "| done"

    def test_bare_cr_terminator_is_not_treated_as_a_redraw(self):
        assert settings_routes._strip_terminal_escapes("waiting\r") == "waiting"

    def test_broadcast_progress_line_is_clean(self, login_env, tmp_path):
        ws, _engine = login_env
        body = (
            'url = "https://claude.com/cai/oauth/authorize?state=abc123"\n'
            'print("\\x1b]8;;" + url + "\\x1b\\\\" + url + '
            '"\\x1b]8;;\\x1b\\\\", flush=True)\n'
        )
        _run_job(_stub_cli(tmp_path, body))

        progress = ws.of_type("login.progress")
        assert progress, "no login.progress broadcast"
        line = progress[0]["line"]
        assert line == _URL
        assert "\x1b" not in line
        assert line.count("https://") == 1


# ---------------------------------------------------------------------------
# 4. Idle timeout (locked decision 4)
# ---------------------------------------------------------------------------

class TestLoginIdleTimeout:

    def test_idle_process_is_killed_and_reported_failed(self, login_env,
                                                        tmp_path, monkeypatch):
        ws, _engine = login_env
        monkeypatch.setattr(settings_routes, "LOGIN_IDLE_TIMEOUT_SECONDS", 0.5)
        pidfile = tmp_path / "cli.pid"
        body = (
            'import os, time\n'
            f'open(r"{pidfile}", "w").write(str(os.getpid()))\n'
            'print("Paste code here if prompted >", flush=True)\n'
            'time.sleep(60)\n'
        )

        started = time.monotonic()
        _run_job(_stub_cli(tmp_path, body))
        elapsed = time.monotonic() - started

        assert elapsed < 20, (
            f"the job waited {elapsed:.1f}s — the idle timeout did not fire"
        )
        final = ws.terminal()
        assert final["type"] == "login.failed"
        assert final.get("timed_out") is True
        assert settings_routes._login_jobs["job1"]["status"] == "failed"
        # The job spawns through the shell, so the CLI is the shell's CHILD.
        # Killing only the shell (cmd.exe on Windows) left `claude auth login`
        # running after every abandoned login.
        cli_pid = int(pidfile.read_text())
        try:
            alive = psutil.Process(cli_pid).status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            alive = False
        if alive:
            psutil.Process(cli_pid).kill()
        assert not alive, "the timed-out login left the CLI process running"

    def test_output_resets_the_idle_clock(self, login_env, tmp_path,
                                          monkeypatch):
        """The timeout is *since last activity*, not a wall-clock cap: a CLI
        that keeps printing must be allowed to run past it."""
        ws, _engine = login_env
        monkeypatch.setattr(settings_routes, "LOGIN_IDLE_TIMEOUT_SECONDS", 0.6)
        body = (
            'import time\n'
            'for i in range(6):\n'
            '    print("still waiting %d" % i, flush=True)\n'
            '    time.sleep(0.2)\n'
        )

        _run_job(_stub_cli(tmp_path, body))

        final = ws.terminal()
        assert final["type"] == "login.complete", (
            "a CLI that kept printing was killed by the idle timeout"
        )
        assert len(ws.of_type("login.progress")) == 6


# ---------------------------------------------------------------------------
# 5. Telemetry emits on the login path
# ---------------------------------------------------------------------------

class TestLoginTelemetry:

    @pytest.fixture
    def recorder(self, monkeypatch):
        rec = _RecordingTelemetry()
        monkeypatch.setattr(settings_routes, "telemetry", rec)
        return rec

    def test_attempt_is_emitted_when_the_job_starts(self, login_env, tmp_path,
                                                    recorder):
        _run_job(_stub_cli(tmp_path, 'print("hi")\n'))
        assert ("login_attempted", {"agent": "claude-code"}) in recorder.events

    def test_unverified_completion_is_its_own_event_not_a_failure(
            self, login_env, tmp_path, recorder):
        """Spec 113 A1 (decision P2): the UI never called an unconfirmed
        exit-0 a failure, and a status probe that breaks on one platform
        must not read as users failing to sign in. It is ``login_unverified``."""
        _run_job(_stub_cli(tmp_path, 'print("Login successful.")\n'))
        assert "login_failed" not in recorder.names
        assert "login_unverified" in recorder.names

    def test_verified_completion_does_not_count_as_a_failure(
            self, login_env, tmp_path, recorder, monkeypatch):
        monkeypatch.setattr(settings_routes, "_setup_engine",
                            _StubEngine(configured=True))
        _run_job(_stub_cli(tmp_path, 'print("Login successful.")\n'))
        assert "login_failed" not in recorder.names

    def test_nonzero_exit_counts_as_a_failure(self, login_env, tmp_path,
                                              recorder):
        _run_job(_stub_cli(tmp_path, 'import sys\nsys.exit(1)\n'))
        assert "login_failed" in recorder.names

    def test_timeout_counts_as_a_failure(self, login_env, tmp_path, recorder,
                                         monkeypatch):
        monkeypatch.setattr(settings_routes, "LOGIN_IDLE_TIMEOUT_SECONDS", 0.4)
        body = 'import time\ntime.sleep(60)\n'
        _run_job(_stub_cli(tmp_path, body))
        assert "login_failed" in recorder.names


# ---------------------------------------------------------------------------
# 6. Spec 113 A1/A3 — every terminal carries a reason, structured
#    diagnostics, and one redacted daemon.log line
# ---------------------------------------------------------------------------

_SETTINGS_LOGGER = "agent_os.api.routes.settings"


def _field(recorder, event):
    rows = [fields for name, fields in recorder.events if name == event]
    assert len(rows) == 1, f"expected one {event}, got {recorder.events}"
    return rows[0]


class TestLoginFailureReasons:

    @pytest.fixture
    def recorder(self, monkeypatch):
        rec = _RecordingTelemetry()
        monkeypatch.setattr(settings_routes, "telemetry", rec)
        return rec

    def test_spawn_error_reason(self, login_env, recorder, caplog, monkeypatch):
        async def _boom(*_a, **_k):
            raise OSError(2, "No such file or directory", "claude")
        monkeypatch.setattr(settings_routes.asyncio, "create_subprocess_shell", _boom)
        with caplog.at_level(logging.WARNING, logger=_SETTINGS_LOGGER):
            _run_job("claude auth login")
        fields = _field(recorder, "login_failed")
        assert fields["reason"] == "spawn_error"
        assert fields["agent"] == "claude-code"
        assert fields["exit_code"] is None
        assert fields["error_class"] == "FileNotFoundError"
        assert fields["stderr_category"] == "unknown_command"
        assert settings_routes._login_jobs["job1"]["reason"] == "spawn_error"
        assert any("reason=spawn_error" in r.getMessage() for r in caplog.records)

    def test_timeout_reason(self, login_env, tmp_path, recorder, monkeypatch, caplog):
        monkeypatch.setattr(settings_routes, "LOGIN_IDLE_TIMEOUT_SECONDS", 0.4)
        with caplog.at_level(logging.WARNING, logger=_SETTINGS_LOGGER):
            _run_job(_stub_cli(tmp_path, 'import time\ntime.sleep(60)\n'))
        fields = _field(recorder, "login_failed")
        assert fields["reason"] == "timeout"
        assert fields["error_class"] == "TimeoutError"
        assert fields["stderr_category"] == "timeout"
        assert isinstance(fields["duration_ms"], int)
        assert any("reason=timeout" in r.getMessage() and "timed_out=True" in r.getMessage()
                   for r in caplog.records)

    def test_nonzero_exit_reason_carries_the_exit_code(self, login_env, tmp_path,
                                                       recorder, caplog):
        body = 'import sys\nprint("unknown command: auth")\nsys.exit(3)\n'
        with caplog.at_level(logging.WARNING, logger=_SETTINGS_LOGGER):
            _run_job(_stub_cli(tmp_path, body))
        fields = _field(recorder, "login_failed")
        assert fields["reason"] == "nonzero_exit"
        assert fields["exit_code"] == 3
        assert fields["error_class"] is None
        assert fields["stderr_category"] == "unknown_command"
        assert any("return_code=3" in r.getMessage() for r in caplog.records)

    def test_unverified_reason_and_diagnostics(self, login_env, tmp_path, recorder):
        _run_job(_stub_cli(tmp_path, 'print("Login successful.")\n'))
        fields = _field(recorder, "login_unverified")
        assert fields["reason"] == "unverified"
        assert fields["exit_code"] == 0
        assert set(fields) >= {"agent", "reason", "exit_code", "cli_version",
                               "install_kind", "duration_ms", "error_class",
                               "stderr_category"}
        # Nothing free-text rides along.
        assert not any(isinstance(v, str) and "http" in v for v in fields.values())

    def test_cli_version_and_install_kind_come_from_the_status_cache(
            self, login_env, tmp_path, recorder, monkeypatch):
        from types import SimpleNamespace
        engine = settings_routes._setup_engine
        engine.cached_status = lambda slug: SimpleNamespace(
            version="2.1.100 (Claude Code)",
            binary_path=r"C:\\Users\\x\\AppData\\Roaming\\npm\\claude.CMD")
        # The job invalidates the cache BEFORE it reports; the diagnostics
        # must have been read at job start, not at the terminal.
        original = engine.invalidate_cache

        def _invalidate():
            engine.cached_status = lambda slug: None
            original()
        engine.invalidate_cache = _invalidate
        _run_job(_stub_cli(tmp_path, 'import sys\nsys.exit(1)\n'))
        fields = _field(recorder, "login_failed")
        assert fields["cli_version"] == "2.1.100"
        assert fields["install_kind"] == "npm"

    def test_log_line_redacts_oauth_query_strings(self, login_env, tmp_path, caplog):
        body = (
            'import sys\n'
            'print("Open https://claude.com/cai/oauth/authorize?code=SECRET123&state=abc")\n'
            'sys.exit(1)\n'
        )
        with caplog.at_level(logging.WARNING, logger=_SETTINGS_LOGGER):
            _run_job(_stub_cli(tmp_path, body))
        lines = [r.getMessage() for r in caplog.records if "job job1" in r.getMessage()]
        assert len(lines) == 1, lines
        assert "SECRET123" not in lines[0]
        assert "?code=" not in lines[0]
        assert "https://claude.com/cai/oauth/authorize?…" in lines[0]
        # The job entry (read back over the login-jobs route) is redacted too.
        tail = settings_routes._login_jobs["job1"]["tail"]
        assert tail and "SECRET123" not in "".join(tail)

    def test_log_line_keeps_only_the_last_five_lines(self, login_env, tmp_path, caplog):
        body = 'import sys\n' + ''.join(f'print("line {i}")\n' for i in range(9)) + 'sys.exit(1)\n'
        with caplog.at_level(logging.WARNING, logger=_SETTINGS_LOGGER):
            _run_job(_stub_cli(tmp_path, body))
        assert settings_routes._login_jobs["job1"]["tail"] == [
            "line 4", "line 5", "line 6", "line 7", "line 8"]

    def test_verified_login_writes_a_log_line_but_no_failure_event(
            self, login_env, tmp_path, recorder, monkeypatch, caplog):
        monkeypatch.setattr(settings_routes, "_setup_engine", _StubEngine(configured=True))
        with caplog.at_level(logging.WARNING, logger=_SETTINGS_LOGGER):
            _run_job(_stub_cli(tmp_path, 'print("Login successful.")\n'))
        assert recorder.names == ["login_attempted"]
        assert any("status=complete" in r.getMessage() for r in caplog.records)


class TestLoginJobHistory:
    """Spec 113 A3: the job table keeps the last 20 terminal entries and
    serves them read-only."""

    def test_finished_jobs_are_kept_up_to_the_limit(self, login_env, tmp_path, monkeypatch):
        monkeypatch.setattr(settings_routes, "LOGIN_JOB_HISTORY", 3)
        for i in range(5):
            _run_job(_stub_cli(tmp_path, 'import sys\nsys.exit(1)\n'), job_id=f"job{i}")
        assert list(settings_routes._login_jobs) == ["job2", "job3", "job4"]

    def test_running_jobs_are_never_pruned(self, login_env, tmp_path, monkeypatch):
        monkeypatch.setattr(settings_routes, "LOGIN_JOB_HISTORY", 1)
        settings_routes._login_jobs["live"] = {"slug": "codex", "status": "running"}
        for i in range(3):
            _run_job(_stub_cli(tmp_path, 'import sys\nsys.exit(1)\n'), job_id=f"job{i}")
        assert "live" in settings_routes._login_jobs
        assert "job2" in settings_routes._login_jobs
        assert "job0" not in settings_routes._login_jobs

    def test_read_route_exposes_reason_code_tail_and_nothing_sensitive(
            self, login_env, tmp_path):
        _run_job(_stub_cli(tmp_path, 'import sys\nprint("bye")\nsys.exit(2)\n'))
        rows = asyncio.run(settings_routes.list_login_jobs())
        assert len(rows) == 1
        row = rows[0]
        assert row["job_id"] == "job1"
        assert row["kind"] == "login"
        assert row["status"] == "failed"
        assert row["reason"] == "nonzero_exit"
        assert row["return_code"] == 2
        assert row["tail"] == ["bye"]
        assert "command" not in row and "pid" not in row


# ---------------------------------------------------------------------------
# 7. Spec 116 D2/D3/D8 — the Update job and route
# ---------------------------------------------------------------------------

class _UpdateEngine(_StubEngine):
    """A stub whose re-probed version flips when ``updated`` is set, so the
    test controls what ``claude --version`` says AFTER ``claude update``."""

    def __init__(self, *, min_version="2.1.114", after="2.1.100 (Claude Code)"):
        super().__init__(configured=True)
        self._after = after
        self._min = min_version
        self.resolved_path_invalidations: list[str] = []
        self.check_agent_calls = 0

    def invalidate_resolved_path(self, slug):
        self.resolved_path_invalidations.append(slug)

    def check_agent(self, slug):
        from types import SimpleNamespace
        self.check_agent_calls += 1
        version = self._after
        detected = SetupEngine._extract_version(version) if version else None
        return SimpleNamespace(
            slug=slug, installed=version is not None, version=version,
            update_needed=bool(detected) and not SetupEngine._version_gte(detected, self._min),
            min_version=self._min, binary_path="/fake/bin/claude",
        )

    def cached_status(self, slug):
        return None


def _run_update(command: str, slug: str = "claude-code", job_id: str = "upd1") -> None:
    settings_routes._login_jobs[job_id] = {
        "slug": slug, "status": "running", "command": command, "kind": "update"}
    asyncio.run(settings_routes._run_login_job(slug, job_id, command, kind="update"))


class TestUpdateJob:

    @pytest.fixture
    def recorder(self, monkeypatch):
        rec = _RecordingTelemetry()
        monkeypatch.setattr(settings_routes, "telemetry", rec)
        return rec

    def test_every_event_carries_kind_update(self, login_env, tmp_path, monkeypatch):
        ws, _ = login_env
        monkeypatch.setattr(settings_routes, "_setup_engine", _UpdateEngine())
        _run_update(_stub_cli(tmp_path, 'print("Updating...")\n'))
        assert ws.events, "nothing broadcast"
        assert all(e.get("kind") == "update" for e in ws.events), ws.events
        assert ws.of_type("login.progress")[0]["line"] == "Updating..."

    def test_exit_zero_below_the_floor_is_not_verified(self, login_env, tmp_path,
                                                       monkeypatch, recorder):
        """D3: the 2.1.288 lesson — the npm updater can report success with
        only the stub in place. Only the re-probed version decides."""
        ws, _ = login_env
        engine = _UpdateEngine(after="2.1.100 (Claude Code)")
        monkeypatch.setattr(settings_routes, "_setup_engine", engine)
        _run_update(_stub_cli(tmp_path, 'print("Successfully updated")\n'))
        final = ws.terminal()
        assert final["type"] == "login.complete"
        assert final["verified"] is False
        assert final["kind"] == "update"
        assert settings_routes._login_jobs["upd1"]["status"] == "unverified"
        assert engine.check_agent_calls == 1
        assert ("update_attempted", {"agent": "claude-code"}) in recorder.events
        failed = _field(recorder, "update_failed")
        assert failed["reason"] == "unverified"
        assert failed["exit_code"] == 0
        # The version that matters is the RE-PROBED one.
        assert failed["cli_version"] == "2.1.100"

    def test_exit_zero_at_the_floor_is_verified_and_the_path_is_forgotten(
            self, login_env, tmp_path, monkeypatch, recorder):
        ws, _ = login_env
        engine = _UpdateEngine(after="2.1.302 (Claude Code)")
        monkeypatch.setattr(settings_routes, "_setup_engine", engine)
        _run_update(_stub_cli(tmp_path, 'print("Successfully updated")\n'))
        final = ws.terminal()
        assert final["type"] == "login.complete"
        assert final["verified"] is True
        assert settings_routes._login_jobs["upd1"]["status"] == "complete"
        assert "update_failed" not in recorder.names
        # The update may move the binary (npm → native); the cached path and
        # the status cache are both dropped before the re-probe.
        assert engine.resolved_path_invalidations == ["claude-code"]
        assert engine.invalidations >= 1
        assert "update_failed" not in recorder.names
        assert "login_attempted" not in recorder.names

    def test_stub_only_update_leaves_no_version_and_is_not_verified(
            self, login_env, tmp_path, monkeypatch):
        ws, _ = login_env
        monkeypatch.setattr(settings_routes, "_setup_engine", _UpdateEngine(after=None))
        _run_update(_stub_cli(tmp_path, 'print("done")\n'))
        assert ws.terminal()["verified"] is False

    def test_nonzero_exit_is_an_update_failure(self, login_env, tmp_path,
                                               monkeypatch, recorder):
        ws, _ = login_env
        monkeypatch.setattr(settings_routes, "_setup_engine", _UpdateEngine())
        _run_update(_stub_cli(tmp_path, 'import sys\nsys.exit(1)\n'))
        assert ws.terminal()["type"] == "login.failed"
        assert _field(recorder, "update_failed")["reason"] == "nonzero_exit"


def _real_engine_with(update_command: str | None, binary: str = "/opt/fake/claude"):
    """A real SetupEngine over a one-manifest registry, with the binary
    pre-resolved so no subprocess runs."""
    from agent_os.agents.manifest import (
        AgentManifest, ManifestRuntime, ManifestSetup)
    from agent_os.agents.registry import AgentRegistry

    manifest = AgentManifest(
        manifest_version="1", name="Claude Code", slug="claude-code",
        description="", author="t", version="1.0.0",
        runtime=ManifestRuntime(adapter="cli", command="claude"),
        setup=ManifestSetup(update_command=update_command, min_version="2.1.114"),
    )
    registry = AgentRegistry()
    registry.register(manifest)
    engine = SetupEngine(registry)
    engine._resolved_paths["claude-code"] = binary
    return engine


class TestUpdateRoute:

    @pytest.fixture
    def no_real_job(self, monkeypatch):
        """The route only schedules the job; never let a command run here."""
        calls: list[tuple] = []

        async def _fake(slug, job_id, command, kind="login"):
            calls.append((slug, job_id, command, kind))
        monkeypatch.setattr(settings_routes, "_run_login_job", _fake)
        return calls

    def test_resolves_the_manifest_update_command_with_the_binary(self, monkeypatch):
        monkeypatch.setattr(settings_routes, "_setup_engine",
                            _real_engine_with("claude update"))
        assert settings_routes._resolve_setup_command("claude-code", "update") \
            == "/opt/fake/claude update"

    def test_400_without_an_update_command(self, login_env, monkeypatch, no_real_job):
        from fastapi import HTTPException
        monkeypatch.setattr(settings_routes, "_setup_engine", _real_engine_with(None))
        with pytest.raises(HTTPException) as info:
            asyncio.run(settings_routes.trigger_sub_agent_update("claude-code"))
        assert info.value.status_code == 400
        assert no_real_job == []

    def test_409_while_a_worker_of_that_agent_is_running(self, login_env, monkeypatch,
                                                         no_real_job):
        from types import SimpleNamespace
        from fastapi import HTTPException
        monkeypatch.setattr(settings_routes, "_setup_engine",
                            _real_engine_with("claude update"))
        monkeypatch.setattr(settings_routes, "_sub_agent_manager",
                            SimpleNamespace(has_live_adapter=lambda slug: slug == "claude-code"))
        with pytest.raises(HTTPException) as info:
            asyncio.run(settings_routes.trigger_sub_agent_update("claude-code"))
        assert info.value.status_code == 409
        assert no_real_job == []

    def test_starts_an_update_job(self, login_env, monkeypatch, no_real_job):
        from types import SimpleNamespace
        monkeypatch.setattr(settings_routes, "_setup_engine",
                            _real_engine_with("claude update"))
        monkeypatch.setattr(settings_routes, "_sub_agent_manager",
                            SimpleNamespace(has_live_adapter=lambda slug: False))

        async def _go():
            body = await settings_routes.trigger_sub_agent_update("claude-code")
            await asyncio.sleep(0)  # let the scheduled task run
            return body
        body = asyncio.run(_go())
        assert body["status"] == "running" and body["slug"] == "claude-code"
        assert no_real_job == [("claude-code", body["job_id"], "/opt/fake/claude update", "update")]
        assert settings_routes._login_jobs[body["job_id"]]["kind"] == "update"
