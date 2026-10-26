# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Diagnostic bundle (spec 113 part D): collection + sanitisation.

The bundle is the only free-text thing that ever leaves the machine, and only
when the user presses Send after seeing it. Every rule below is tested with a
concrete string so a regression is a visible diff, not a vague "looks ok".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_os.telemetry import diagnostic_bundle as db
from agent_os.telemetry.spool import Spool


# ---------------------------------------------------------------------------
# sanitise(): one rule, one concrete string each
# ---------------------------------------------------------------------------

class TestSanitiseUrls:
    def test_query_string_is_stripped_from_https_url(self):
        line = ("Open https://auth.openai.com/oauth/callback?code=abc123XYZ"
                "&state=s3cr3t to finish")
        out = db.sanitise(line)
        assert out == "Open https://auth.openai.com/oauth/callback?<redacted> to finish"

    def test_query_string_is_stripped_from_http_url(self):
        assert db.sanitise("http://127.0.0.1:1455/auth?code=x") == "http://127.0.0.1:1455/auth?<redacted>"

    def test_url_without_query_is_untouched(self):
        assert db.sanitise("see https://example.com/docs/page") == "see https://example.com/docs/page"

    def test_fragment_after_query_goes_too(self):
        out = db.sanitise("https://a.b/c?token=1#frag")
        assert "token" not in out and "frag" not in out


class TestSanitiseKeys:
    def test_sk_prefixed_key_is_redacted(self):
        line = "llm_api_key=sk-abcdef0123456789abcdef0123456789 provider=openai"
        out = db.sanitise(line)
        assert out == "llm_api_key=<redacted> provider=openai"

    def test_sk_ant_key_is_redacted(self):
        out = db.sanitise("key sk-ant-api03-Zm9vYmFyYmF6 ok")
        assert out == "key <redacted> ok"

    def test_bearer_token_is_redacted(self):
        out = db.sanitise("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")
        assert out == "Authorization: Bearer <redacted>"

    def test_long_hex_token_is_redacted(self):
        tok = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
        out = db.sanitise(f"session token {tok} issued")
        assert out == "session token <redacted> issued"

    def test_long_base64_token_is_redacted(self):
        tok = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU2Nzg5"
        out = db.sanitise(f"token={tok}")
        assert out == "token=<redacted>"

    def test_short_hex_like_install_id_survives(self):
        # inst_ + 12 hex is 17 chars — far below the 32-char shape.
        assert db.sanitise("install_id: inst_71f071d94e45") == "install_id: inst_71f071d94e45"

    def test_long_identifier_without_digits_survives(self):
        name = "test_windows_provider_run_process_accepts_use_pty"
        assert db.sanitise(name) == name

    def test_file_path_is_not_mistaken_for_a_token(self):
        path = "/opt/orbital/agent_os/telemetry/diagnostic_bundle.py"
        assert db.sanitise(path) == path


class TestSanitiseHomeAndEmail:
    def test_real_home_dir_is_shortened(self, monkeypatch):
        monkeypatch.setattr(db, "_home_dirs", lambda: ["/Users/alice"])
        out = db.sanitise("binary at /Users/alice/.npm-global/bin/claude")
        assert out == "binary at ~/.npm-global/bin/claude"

    def test_other_users_home_is_shortened_too(self, monkeypatch):
        monkeypatch.setattr(db, "_home_dirs", lambda: ["/Users/alice"])
        assert db.sanitise("/Users/bob/x") == "~/x"
        assert db.sanitise("/home/carol/y") == "~/y"

    def test_windows_home_is_shortened(self, monkeypatch):
        monkeypatch.setattr(db, "_home_dirs", lambda: ["C:\\Users\\Dave"])
        out = db.sanitise(r"C:\Users\Dave\AppData\Roaming\Orbital\logs")
        assert out == r"~\AppData\Roaming\Orbital\logs"

    def test_email_is_masked_to_first_char_and_domain(self):
        assert db.sanitise("user zqzq.r0@gmail.com failed") == "user z***@gmail.com failed"

    def test_email_inside_url_query_is_gone_with_the_query(self):
        out = db.sanitise("https://x.y/login?email=someone@example.com")
        assert out == "https://x.y/login?<redacted>"


def test_sanitise_is_idempotent():
    text = "sk-abcdefghijklmnop https://a.b/c?d=e alice@example.com"
    once = db.sanitise(text)
    assert db.sanitise(once) == once


# ---------------------------------------------------------------------------
# collection
# ---------------------------------------------------------------------------

@pytest.fixture
def sources(tmp_path: Path) -> db.BundleSources:
    data = tmp_path / "data"
    (data / "logs").mkdir(parents=True)
    daemon_log = data / "logs" / "daemon.log"
    daemon_log.write_text(
        "2026-10-18 10:00:00 INFO agent_os: boot\n"
        "2026-10-18 10:00:01 WARNING login: open https://auth.example/cb?code=OAUTHCODE123\n"
        "2026-10-18 10:00:02 INFO cfg: key=sk-abcdef0123456789abcdef0123456789\n",
        encoding="utf-8",
    )
    stderr_log = tmp_path / "orbital-stderr.log"
    stderr_log.write_text("Traceback: boom at /Users/alice/x.py\n", encoding="utf-8")
    return db.BundleSources(data_dir=data, daemon_log=daemon_log, stderr_log=stderr_log)


def _build(sources, **kw):
    defaults = dict(
        install_id="inst_0123456789ab",
        version="0.17.0",
        os_name="Darwin 24.6.0 (arm64)",
        telemetry_enabled=True,
        sub_agents=[{"slug": "codex", "installed": True,
                     "binary_path": "/Users/alice/.npm-global/bin/codex"}],
        context=None,
    )
    defaults.update(kw)
    return db.build_bundle(sources, **defaults)


def test_bundle_contains_every_section_and_is_sanitised(sources, monkeypatch):
    monkeypatch.setattr(db, "_home_dirs", lambda: ["/Users/alice"])
    text = _build(sources)
    for header in ("== sub-agents ==", "== recent telemetry failures ==",
                   "== daemon.log", "== orbital-stderr.log"):
        assert header in text
    assert "install_id: inst_0123456789ab" in text
    assert "version: 0.17.0" in text
    assert "Darwin 24.6.0" in text
    # The planted secrets are gone from BOTH the log tail and the status JSON.
    assert "OAUTHCODE123" not in text
    assert "sk-abcdef" not in text
    assert "https://auth.example/cb?<redacted>" in text
    assert "/Users/alice" not in text
    assert "~/.npm-global/bin/codex" in text
    assert "~/x.py" in text


def test_daemon_log_is_capped_to_the_last_200kb(sources):
    big = "x" * 100 + "\n"
    sources.daemon_log.write_text("FIRSTLINE\n" + big * 3000 + "LASTLINE\n", encoding="utf-8")
    text = _build(sources)
    assert "LASTLINE" in text
    assert "FIRSTLINE" not in text
    assert "truncated" in text
    section = text.split("== daemon.log")[1]
    assert len(section.encode("utf-8")) <= db.DAEMON_LOG_TAIL_BYTES + 2048


def test_missing_logs_degrade_to_a_note_not_an_error(tmp_path):
    src = db.BundleSources(data_dir=tmp_path / "nope",
                           daemon_log=tmp_path / "missing.log",
                           stderr_log=None)
    text = _build(src)
    assert "daemon.log: not found" in text
    assert "orbital-stderr.log: not available" in text


def test_telemetry_disabled_is_stated_in_one_line(sources):
    text = _build(sources, telemetry_enabled=False)
    assert "telemetry_enabled: false" in text
    assert "explicit" in text.lower()


def test_context_is_rendered_in_the_header(sources):
    text = _build(sources, context={"kind": "login_failed", "agent": "codex",
                                    "detail": "exit 1 at https://a.b/c?code=zzz"})
    assert "context: login_failed" in text
    assert "agent: codex" in text
    assert "code=zzz" not in text


def test_sub_agent_status_failure_is_noted(sources):
    text = _build(sources, sub_agents=None)
    assert "sub-agent status: unavailable" in text


class TestRecentFailures:
    def test_reads_recent_failures_from_the_pending_ping_when_present(self, sources):
        pending = sources.data_dir / "telemetry" / "pending"
        pending.mkdir(parents=True)
        (pending / "2026-10-18.json").write_text(json.dumps({
            "date": "2026-10-18",
            "counters": {"recent_failures": [
                {"event": "login_failed", "agent": "codex", "reason": "timeout",
                 "ts": "2026-10-18T09:00:00+00:00"}]},
        }), encoding="utf-8")
        text = _build(sources)
        assert '"reason": "timeout"' in text

    def test_falls_back_to_the_spool_when_the_field_is_absent(self, sources):
        spool = Spool(sources.data_dir)
        for _ in range(25):
            spool.append("login_failed", {"agent": "claude-code"})
        spool.append("turn_completed", {"provider": "deepseek"})
        spool.append("subagent_failed", {"agent": "codex"})
        rows = db.recent_failures(sources.data_dir)
        assert len(rows) == db.RECENT_FAILURES_MAX
        assert rows[-1]["event"] == "subagent_failed"
        assert all(r["event"] != "turn_completed" for r in rows)

    def test_no_spool_means_none(self, tmp_path):
        assert db.recent_failures(tmp_path / "empty") == []
        text = _build(db.BundleSources(data_dir=tmp_path / "empty",
                                       daemon_log=None, stderr_log=None))
        assert "recent telemetry failures ==\nnone" in text


def test_default_sources_find_the_daemon_log_through_the_root_logger(tmp_path):
    import logging
    import logging.handlers

    root = logging.getLogger()
    # Another test's create_app() may have left its own flagged handler on
    # the root logger (it is never removed); park those for the duration.
    parked = [h for h in root.handlers if getattr(h, "_orbital_daemon_log", False)]
    for h in parked:
        root.removeHandler(h)
    handler = logging.handlers.RotatingFileHandler(tmp_path / "d.log")
    handler._orbital_daemon_log = True  # same flag app.py sets
    root.addHandler(handler)
    try:
        src = db.default_sources(data_dir=None)
    finally:
        root.removeHandler(handler)
        handler.close()
        for h in parked:
            root.addHandler(h)
    assert src.daemon_log == tmp_path / "d.log"
    assert src.data_dir == tmp_path.parent  # {data_dir}/logs/daemon.log → data_dir


def test_default_sources_fall_back_to_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_daemon_log_from_root_logger", lambda: None)
    src = db.default_sources(data_dir=tmp_path)
    assert src.daemon_log == tmp_path / "logs" / "daemon.log"
    assert src.data_dir == tmp_path


def test_feedback_url_is_derived_from_the_ingest_endpoint():
    assert db.feedback_url("https://orbital-telemetry-production.up.railway.app/ingest") == \
        "https://orbital-telemetry-production.up.railway.app/feedback"
    assert db.feedback_url("http://127.0.0.1:9/ingest/") == "http://127.0.0.1:9/feedback"
    # An endpoint without the /ingest suffix gets /feedback appended.
    assert db.feedback_url("http://127.0.0.1:9") == "http://127.0.0.1:9/feedback"


# ---------------------------------------------------------------------------
# spec 134 A3 — hang.log rides along
# ---------------------------------------------------------------------------

def test_bundle_includes_the_hang_log_tail(tmp_path):
    data = tmp_path / "data"
    (data / "logs").mkdir(parents=True)
    hang = data / "logs" / "hang.log"
    hang.write_text(
        "=== event loop stalled: no answer for 5.2 s ===\n"
        'File "/Users/alice/orbital/agent_os/agent/tools/shell.py", line 9 in execute\n',
        encoding="utf-8")
    src = db.BundleSources(data_dir=data, daemon_log=None, stderr_log=None, hang_log=hang)
    text = _build(src)
    assert "== hang.log" in text
    assert "event loop stalled" in text
    assert "/Users/alice" not in text  # sanitised like the other logs


def test_bundle_says_when_there_is_no_hang_log(tmp_path):
    src = db.BundleSources(data_dir=tmp_path, daemon_log=None, stderr_log=None,
                           hang_log=tmp_path / "logs" / "hang.log")
    assert "hang.log: not found" in _build(src)


def test_default_sources_put_hang_log_next_to_daemon_log(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_daemon_log_from_root_logger", lambda: None)
    src = db.default_sources(data_dir=tmp_path)
    assert src.hang_log == tmp_path / "logs" / "hang.log"


def test_the_three_log_tails_fit_one_upload():
    overhead = 64 * 1024  # header, sub-agents, failures
    assert (db.DAEMON_LOG_TAIL_BYTES + db.STDERR_LOG_MAX_BYTES
            + db.HANG_LOG_TAIL_BYTES + overhead) <= db.MAX_UPLOAD_BYTES
