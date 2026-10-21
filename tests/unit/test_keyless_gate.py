# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Keyless gate: the wizard's skip flag, ``manager_ready`` on project rows,
and background manager work that must skip instead of raising.

A user who never gives Orbital its own LLM key can skip that wizard step
(``setup.api_key_skipped``), and every project row says whether Orbital's own
agent can run there (``manager_ready``) so the UI can show it as "needs a
key" rather than failing on the first start.
"""

from __future__ import annotations

import logging
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_os.daemon_v2.settings_store import SettingsStore
from tests.card_doubles import FakeCardStore
from tests.unit.keyless_harness import build_client, create_project


class _CountingKeys:
    """In-memory per-card key store that counts reads (a Keychain stand-in)."""

    def __init__(self):
        self.keys: dict[str, str] = {}
        self.reads = 0

    def get(self, card_id):
        if card_id == "env":
            # The real store answers the env card from os.environ (no
            # Keychain round trip), so it is not a counted read.
            import os
            return os.environ.get("AGENT_OS_API_KEY") or None
        self.reads += 1
        return self.keys.get(card_id)

    def set(self, card_id, key):
        if not key:
            self.keys.pop(card_id, None)
            return {"source": "none"}
        self.keys[card_id] = key
        return {"source": "keychain"}

    def delete(self, card_id):
        self.keys.pop(card_id, None)

    def source(self, card_id):
        return "keychain" if card_id in self.keys else "none"

    def get_legacy(self):
        return None

    def delete_legacy(self):
        return None


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch):
    # The suite exports AGENT_OS_API_KEY; keyless means no env card either.
    monkeypatch.delenv("AGENT_OS_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# B1 — setup.api_key_skipped
# ---------------------------------------------------------------------------


@pytest.fixture
def settings_client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    from agent_os.api.routes import settings as settings_routes
    store = SettingsStore(data_dir=str(tmp_path / "data"),
                          card_key_store=_CountingKeys(), migrate=False)
    settings_routes.configure(store)
    app = FastAPI()
    app.include_router(settings_routes.router)
    return TestClient(app), store


class TestSetupSkipFlag:
    def test_default_is_false_and_masked_view_carries_it(self, settings_client):
        tc, _store = settings_client
        body = tc.get("/api/v2/settings").json()
        assert body["setup"] == {"api_key_skipped": False}
        assert body["llm"]["api_key_set"] is False

    def test_put_then_get_round_trips(self, settings_client):
        tc, store = settings_client
        r = tc.put("/api/v2/settings", json={"setup_api_key_skipped": True})
        assert r.status_code == 200
        assert r.json()["setup"] == {"api_key_skipped": True}
        assert tc.get("/api/v2/settings").json()["setup"]["api_key_skipped"] is True
        assert store.get().setup_api_key_skipped is True

    def test_a_put_without_the_field_leaves_it_untouched(self, settings_client):
        tc, _store = settings_client
        tc.put("/api/v2/settings", json={"setup_api_key_skipped": True})
        r = tc.put("/api/v2/settings", json={"telemetry_enabled": False})
        assert r.status_code == 200
        assert r.json()["setup"]["api_key_skipped"] is True

    def test_it_can_be_cleared(self, settings_client):
        tc, _store = settings_client
        tc.put("/api/v2/settings", json={"setup_api_key_skipped": True})
        r = tc.put("/api/v2/settings", json={"setup_api_key_skipped": False})
        assert r.json()["setup"]["api_key_skipped"] is False

    def test_saving_a_key_does_not_reset_the_flag(self, settings_client):
        tc, store = settings_client
        tc.put("/api/v2/settings", json={"setup_api_key_skipped": True})
        store.create_card(provider="deepseek", model="deepseek-chat",
                          api_key="sk-abcdefgh12345678", make_default=True)
        body = tc.get("/api/v2/settings").json()
        assert body["llm"]["api_key_set"] is True
        assert body["setup"]["api_key_skipped"] is True


# ---------------------------------------------------------------------------
# B2 — has_key cache on the settings store
# ---------------------------------------------------------------------------


class TestHasKeyCache:
    def _store(self, tmp_path):
        keys = _CountingKeys()
        return SettingsStore(data_dir=str(tmp_path), card_key_store=keys,
                             migrate=False), keys

    def test_repeated_reads_hit_the_key_store_once(self, tmp_path):
        store, keys = self._store(tmp_path)
        card = store.create_card(provider="deepseek", model="m", api_key="sk-1")
        keys.reads = 0
        assert all(store.has_key(card.id) for _ in range(20))
        assert keys.reads == 1

    def test_a_key_write_invalidates(self, tmp_path):
        store, _keys = self._store(tmp_path)
        card = store.create_card(provider="deepseek", model="m", api_key="")
        assert store.has_key(card.id) is False
        store.set_card_key(card.id, "sk-new")
        assert store.has_key(card.id) is True
        store.update_card(card.id, api_key="")
        assert store.has_key(card.id) is False

    def test_delete_invalidates(self, tmp_path):
        store, _keys = self._store(tmp_path)
        card = store.create_card(provider="deepseek", model="m", api_key="sk-1")
        assert store.has_key(card.id) is True
        store.delete_card(card.id)
        assert store.has_key(card.id) is False

    def test_env_card_follows_the_environment(self, tmp_path, monkeypatch):
        store, _keys = self._store(tmp_path)
        assert store.has_key("env") is False
        monkeypatch.setenv("AGENT_OS_API_KEY", "sk-env")
        assert store.has_key("env") is True

    def test_unknown_or_empty_id(self, tmp_path):
        store, _keys = self._store(tmp_path)
        assert store.has_key(None) is False
        assert store.has_key("") is False


# ---------------------------------------------------------------------------
# B2 — manager_ready on the agent manager and on project rows
# ---------------------------------------------------------------------------


class TestManagerReady:
    def test_keyless_default_card(self, tmp_path):
        tc, deps = build_client(tmp_path, keyed=False)
        ws = tmp_path / "ws"; ws.mkdir()
        row = create_project(tc, ws)
        assert row["manager_ready"] is False
        assert deps["agent_manager"].manager_ready(row["project_id"]) is False

    def test_keyed_default_card(self, tmp_path):
        tc, deps = build_client(tmp_path, keyed=True)
        ws = tmp_path / "ws"; ws.mkdir()
        row = create_project(tc, ws)
        assert row["manager_ready"] is True
        assert deps["agent_manager"].manager_ready(row["project_id"]) is True

    def test_no_cards_at_all(self, tmp_path):
        tc, deps = build_client(tmp_path, keyed=False,
                                settings_store=FakeCardStore())
        ws = tmp_path / "ws"; ws.mkdir()
        row = create_project(tc, ws)
        assert row["manager_ready"] is False

    def test_project_card_wins_over_the_default(self, tmp_path):
        store = FakeCardStore.with_default(provider="openai", model="gpt-4o", key="")
        store.add(card_id="card_proj", provider="deepseek", model="m", key="sk-p")
        tc, deps = build_client(tmp_path, keyed=False, settings_store=store)
        ws = tmp_path / "ws"; ws.mkdir()
        row = create_project(tc, ws, card_id="card_proj")
        assert row["manager_ready"] is True
        other = tmp_path / "ws2"; other.mkdir()
        assert create_project(tc, other, name="other")["manager_ready"] is False

    def test_deleted_project_card_falls_back_to_the_default(self, tmp_path):
        store = FakeCardStore.with_default(provider="openai", model="gpt-4o", key="sk-d")
        tc, deps = build_client(tmp_path, keyed=True, settings_store=store)
        ws = tmp_path / "ws"; ws.mkdir()
        pid = create_project(tc, ws)["project_id"]
        deps["project_store"].update_project(pid, {"card_id": "card_gone"})
        assert deps["agent_manager"].manager_ready(pid) is True

    def test_unknown_project_is_not_ready(self, tmp_path):
        _tc, deps = build_client(tmp_path, keyed=True)
        assert deps["agent_manager"].manager_ready("proj_nope") is False

    def test_every_row_route_carries_the_field(self, tmp_path):
        tc, _deps = build_client(tmp_path, keyed=False)
        ws = tmp_path / "ws"; ws.mkdir()
        pid = create_project(tc, ws)["project_id"]
        assert tc.get(f"/api/v2/projects/{pid}").json()["manager_ready"] is False
        assert all("manager_ready" in p for p in tc.get("/api/v2/projects").json())
        put = tc.put(f"/api/v2/projects/{pid}", json={"name": "renamed"}).json()
        assert put["manager_ready"] is False

    def test_a_key_saved_later_shows_on_the_next_read(self, tmp_path):
        store = FakeCardStore.with_default(key="")
        tc, _deps = build_client(tmp_path, keyed=False, settings_store=store)
        ws = tmp_path / "ws"; ws.mkdir()
        pid = create_project(tc, ws)["project_id"]
        assert tc.get(f"/api/v2/projects/{pid}").json()["manager_ready"] is False
        store.keys[store.default_card_id] = "sk-later"
        assert tc.get(f"/api/v2/projects/{pid}").json()["manager_ready"] is True

    def test_list_route_is_cheap_for_twenty_keyless_projects(self, tmp_path):
        keys = _CountingKeys()
        store = SettingsStore(data_dir=str(tmp_path / "settings"),
                              card_key_store=keys, migrate=False)
        store.create_card(provider="deepseek", model="m", api_key="",
                          make_default=True)
        tc, _deps = build_client(tmp_path, keyed=False, settings_store=store)
        for i in range(20):
            ws = tmp_path / f"ws{i}"; ws.mkdir()
            create_project(tc, ws, name=f"p{i}")
        tc.get("/api/v2/projects")  # warm
        keys.reads = 0
        start = time.perf_counter()
        rows = tc.get("/api/v2/projects").json()
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert len(rows) == 20 and not any(r["manager_ready"] for r in rows)
        assert keys.reads == 0, "manager_ready hit the key store per row"
        assert elapsed_ms < 200, f"list route took {elapsed_ms:.1f} ms"


# ---------------------------------------------------------------------------
# B7 — pinned consolidation skips on a keyless project
# ---------------------------------------------------------------------------


class TestPinnedConsolidationKeyless:
    async def test_pass_skips_with_one_info_line(self, tmp_path, caplog):
        from agent_os.daemon_v2.pinned_consolidation import (
            PinnedConsolidationCoordinator,
        )
        _tc, deps = build_client(tmp_path, keyed=False)
        ws = tmp_path / "ws"; ws.mkdir()
        pid = deps["project_store"].create_project(
            {"name": "k", "workspace": str(ws)})
        am = deps["agent_manager"]
        built = []
        orig = am._build_llm_providers
        am._build_llm_providers = lambda cfg: built.append(cfg) or orig(cfg)
        am.get_session = lambda *_a, **_k: object()  # a session exists

        coord = PinnedConsolidationCoordinator(am, deps["project_store"])
        with caplog.at_level(logging.INFO,
                             logger="agent_os.daemon_v2.pinned_consolidation"):
            await coord._run_guarded((pid, "s1"), "quiescence")
        assert built == [], "a provider was built for a keyless project"
        skip_lines = [r for r in caplog.records
                      if "no management-agent key" in r.getMessage()]
        assert len(skip_lines) == 1 and skip_lines[0].levelno == logging.INFO
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert (pid, "s1") not in coord._running
