# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Spec 111 — telemetry counter loss: the seams outside the sender.

- A2: the daemon exposes a bounded flush, the app's shutdown hook and the
  desktop exit paths call it before ``os._exit``.
- A3: ``first_session`` latched only in ``new_session``; the lazy chat funnel
  and ``_start_loop``'s own mint bypassed it, so a user who just typed into
  the auto-created project reached ``first_turn`` without ``first_session``
  (13 installs in the 2026-10-13 audit). Every uuid mint now counts, and a
  turn latches the session milestone as a backstop.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_os import telemetry


def _manager():
    from agent_os.daemon_v2.agent_manager import AgentManager

    m = AgentManager(
        project_store=MagicMock(),
        ws_manager=MagicMock(),
        sub_agent_manager=MagicMock(),
        activity_translator=MagicMock(),
        process_manager=MagicMock(),
        platform_provider=None,
        settings_store=MagicMock(),
        credential_store=MagicMock(),
        browser_manager=MagicMock(),
        provider_registry=MagicMock(),
    )
    m._project_store.get_project.return_value = {"name": "demo"}
    return m


def _events(tmp_path: Path) -> list[str]:
    path = tmp_path / "telemetry" / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(l)["event"] for l in path.read_text(encoding="utf-8").splitlines()]


def _milestones(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "telemetry" / "install.json").read_text(encoding="utf-8"))["milestones"]


@pytest.fixture
def configured(tmp_path):
    telemetry.reset_for_tests()
    telemetry.configure(tmp_path, is_enabled=lambda: True)
    yield tmp_path
    telemetry.reset_for_tests()


class TestFirstSessionLatchesOnEveryMint:
    def test_lazy_chat_funnel_counts_and_latches(self, configured):
        """The chat-first path: no "+ new session" click ever happens."""
        manager = _manager()
        manager.list_sessions = MagicMock(return_value=[])
        sid = manager._ensure_chat_session("proj_x")
        assert sid
        assert _events(configured) == ["session_created"]
        assert _milestones(configured) == {"first_session": True}

    def test_explicit_mint_primitive_counts(self, configured):
        """``_start_loop`` with neither a session nor an id mints here too."""
        manager = _manager()
        manager._mint_session_uuid("proj_x")
        assert _events(configured) == ["session_created"]

    async def test_new_session_still_counts_exactly_once(self, configured):
        manager = _manager()
        await manager.new_session("proj_x")
        assert _events(configured) == ["session_created"]

    def test_reusing_an_existing_chat_session_does_not_count(self, configured):
        manager = _manager()
        manager.list_sessions = MagicMock(return_value=[
            {"session_id": "demo_abc", "origin": "chat", "last_activity_at": "2026-10-01T00:00:00"},
        ])
        assert manager._ensure_chat_session("proj_x") == "demo_abc"
        assert _events(configured) == []


class TestTurnLatchesSessionBackstop:
    def test_first_turn_also_latches_first_session(self, configured, tmp_path):
        from agent_os.budget.ledger import LedgerEvent, append_event
        from agent_os.budget.normalize import NormalizedUsage

        append_event(str(tmp_path / "proj"), LedgerEvent(
            session_id="s1", source="management", provider="deepseek", model="m",
            usage=NormalizedUsage(uncached_input=1, cache_read=0, cache_write=0, output=1),
        ))
        ms = _milestones(configured)
        assert ms.get("first_turn") is True
        assert ms.get("first_session") is True


class TestFlushRoute:
    async def test_route_returns_sender_status(self, configured, monkeypatch):
        from agent_os.api.routes import settings as settings_routes

        sender = telemetry.get_sender()

        async def fake_flush(timeout=3.0):
            fake_flush.timeout = timeout
            return "sent"

        monkeypatch.setattr(sender, "flush_now", fake_flush)
        assert await settings_routes.flush_telemetry() == {"status": "sent"}
        assert fake_flush.timeout <= 3.0

    async def test_route_without_sender_is_skipped(self):
        from agent_os.api.routes import settings as settings_routes

        telemetry.reset_for_tests()
        assert await settings_routes.flush_telemetry() == {"status": "skipped"}

    def test_route_is_registered_as_post(self):
        from agent_os.api.routes.settings import router

        paths = {(r.path, tuple(sorted(r.methods))) for r in router.routes if hasattr(r, "methods")}
        assert ("/api/v2/settings/telemetry/flush", ("POST",)) in paths


class TestShutdownHookFlushes:
    def test_app_shutdown_flushes_before_stop(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from agent_os.telemetry.sender import TelemetrySender

        calls: list[str] = []

        async def fake_flush(self, timeout=3.0):
            calls.append("flush")
            return "skipped"

        def fake_stop(self):
            calls.append("stop")

        monkeypatch.setattr(TelemetrySender, "flush_now", fake_flush)
        monkeypatch.setattr(TelemetrySender, "stop", fake_stop)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
        telemetry.reset_for_tests()
        from agent_os.api.app import create_app

        app = create_app(data_dir=str(tmp_path / "data"))
        with TestClient(app):
            pass
        telemetry.reset_for_tests()
        assert calls[:2] == ["flush", "stop"]


class TestDesktopExitFlush:
    def test_posts_flush_to_the_local_daemon(self, monkeypatch):
        from agent_os.desktop import main as desktop_main

        seen = {}

        class _Resp:
            status = 200

        class _Opener:
            def open(self, req, timeout=None):
                seen["url"] = req.full_url
                seen["method"] = req.get_method()
                seen["timeout"] = timeout
                return _Resp()

        monkeypatch.setattr(desktop_main, "_local_opener", lambda: _Opener())
        assert desktop_main.flush_telemetry_before_exit(8123) is True
        assert seen["url"] == "http://127.0.0.1:8123/api/v2/settings/telemetry/flush"
        assert seen["method"] == "POST"
        assert seen["timeout"] <= 3.0

    def test_never_raises_when_the_daemon_is_gone(self, monkeypatch):
        from agent_os.desktop import main as desktop_main

        class _Opener:
            def open(self, req, timeout=None):
                raise OSError("connection refused")

        monkeypatch.setattr(desktop_main, "_local_opener", lambda: _Opener())
        assert desktop_main.flush_telemetry_before_exit(8123) is False

    def test_both_exit_paths_flush_before_os_exit(self):
        """Source-level guard: every ``os._exit(0)`` in the desktop entry
        point must be preceded by the flush call (the two exit paths are not
        unit-drivable — they tear down the interpreter)."""
        src = Path(desktop_src()).read_text(encoding="utf-8")
        # Spec 134 Part B: both exit paths of main() (tray shutdown, macOS
        # window close) go through _exit_app, the one os._exit.
        main_body = src[src.index("def main():"):]
        assert "os._exit(0)" not in main_body
        assert main_body.count("_exit_app(port)") == 2
        body = src[src.index("def _exit_app("):src.index("def _handle_window_close(")]
        exits = [i for i in range(len(body)) if body.startswith("os._exit(0)", i)]
        assert exits, "no exit paths found"
        for i in exits:
            window = body[max(0, i - 400):i]
            assert "flush_telemetry_before_exit(" in window, body[max(0, i - 200):i + 20]


def desktop_src() -> Path:
    import agent_os.desktop.main as m

    return Path(m.__file__)
