# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 138 telemetry: the allowlisted UI event endpoint, the onboarding
counters in the daily ping, the new milestones, and the settings that feed
them (global hidden agents, the key-step skip)."""

from __future__ import annotations

import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_os import telemetry
from agent_os.api.routes import onboarding as onboarding_routes
from agent_os.telemetry.identity import InstallIdentity
from agent_os.telemetry.rollup import build_ping, history_bucket
from agent_os.telemetry.spool import Spool

DAY = "2026-10-27"


def _ping(tmp_path, rows):
    spool = Spool(tmp_path)
    spool._path.parent.mkdir(parents=True, exist_ok=True)
    with open(spool._path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps({"ts": f"{DAY}T10:00:00+00:00", **r}) + "\n")
    return build_ping(InstallIdentity(tmp_path), spool, DAY, version="0.19.0", os_name="darwin")


def test_onboarding_counters_aggregate(tmp_path):
    c = _ping(tmp_path, [
        {"event": "wizard_step_reached", "step": "agents"},
        {"event": "wizard_step_reached", "step": "projects"},
        {"event": "wizard_step_reached", "step": "/Users/x/secret"},
        {"event": "agents_detected", "n": 3},
        {"event": "agents_detected", "n": 2},
        {"event": "agent_located"},
        {"event": "history_sessions_bucket", "bucket": "100+"},
        {"event": "history_sessions_bucket", "bucket": "lots"},
        {"event": "projects_imported", "folder": 3, "group": 1},
        {"event": "projects_unchecked", "n": 2},
        {"event": "leftoff_shown"},
        {"event": "leftoff_opened", "kind": "unfinished", "cause": "usage_limit"},
        {"event": "leftoff_opened", "kind": "last_time"},
        {"event": "leftoff_opened", "kind": "unfinished", "cause": "made-up"},
        {"event": "agent_switch_after_open"},
    ])["counters"]
    assert c["wizard_step_reached"] == {"agents": 1, "projects": 1, "key": 0,
                                        "accounts": 0, "done": 0}
    assert c["agents_detected"] == 2
    assert c["agents_located"] == 1
    assert c["history_sessions_bucket"] == "100+"
    assert c["projects_imported"] == {"folder": 3, "group": 1}
    assert c["projects_unchecked"] == 2
    assert c["leftoff_shown"] == 1
    assert c["leftoff_opened"] == {"last_time": 1, "unfinished": 2}
    assert c["unfinished_opened_by_cause"] == {
        "usage_limit": 1, "model_unavailable": 0, "network_auth": 0,
        "interrupted": 0, "no_reply": 0}
    assert c["agent_switch_after_open"] == 1


def test_empty_day_onboarding_shape(tmp_path):
    c = _ping(tmp_path, [])["counters"]
    assert c["agents_detected"] is None and c["history_sessions_bucket"] is None
    assert c["projects_imported"] == {"folder": 0, "group": 0}


def test_new_milestones_are_reported(tmp_path):
    identity = InstallIdentity(tmp_path)
    identity.latch_milestone("first_import")
    ping = build_ping(identity, Spool(tmp_path), DAY, version="x", os_name="darwin")
    assert ping["milestones"]["first_import"] is True
    assert ping["milestones"]["key_skipped"] is False
    assert {"first_leftoff_open", "first_imported_send"} <= set(ping["milestones"])


@pytest.mark.parametrize("n,bucket", [(0, "0"), (1, "1-10"), (10, "1-10"), (11, "11-100"),
                                      (100, "11-100"), (101, "100+")])
def test_history_bucket(n, bucket):
    assert history_bucket(n) == bucket


@pytest.fixture
def live_telemetry(tmp_path):
    telemetry.configure(tmp_path, is_enabled=lambda: True)
    yield tmp_path
    telemetry.reset_for_tests()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(onboarding_routes.telemetry_router)
    return TestClient(app)


def _spooled(data_dir):
    path = data_dir / "telemetry" / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_event_endpoint_spools_allowlisted_events(client, live_telemetry):
    for body in ({"event": "wizard_step_reached", "fields": {"step": "key"}},
                 {"event": "leftoff_opened", "fields": {"kind": "unfinished", "cause": "no_reply",
                                                         "extra": "/Users/x"}},
                 {"event": "projects_unchecked", "fields": {"n": 4}},
                 {"event": "agent_located"}):
        assert client.post("/api/v2/telemetry/event", json=body).status_code == 204
    rows = [{k: v for k, v in r.items() if k != "ts"} for r in _spooled(live_telemetry)]
    assert rows == [
        {"event": "wizard_step_reached", "step": "key"},
        {"event": "leftoff_opened", "kind": "unfinished", "cause": "no_reply"},
        {"event": "projects_unchecked", "n": 4},
        {"event": "agent_located"},
    ]
    milestones = json.loads((live_telemetry / "telemetry" / "install.json").read_text())["milestones"]
    assert milestones.get("first_leftoff_open") is True


@pytest.mark.parametrize("body", [
    {"event": "made_up"},
    {"event": "wizard_step_reached", "fields": {"step": "somewhere"}},
    {"event": "leftoff_opened", "fields": {"kind": "unfinished", "cause": "boom"}},
    {"event": "projects_unchecked", "fields": {"n": "many"}},
    {"event": "projects_imported", "fields": {"folder": 1}},  # server-side only
])
def test_event_endpoint_rejects_anything_else(client, live_telemetry, body):
    assert client.post("/api/v2/telemetry/event", json=body).status_code == 400
    assert _spooled(live_telemetry) == []


# ---- Settings ----------------------------------------------------------------


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    os.makedirs(str(tmp_path / "home"), exist_ok=True)
    from agent_os.api.app import create_app
    app = create_app(data_dir=str(tmp_path / "data"))
    return TestClient(app)


def test_settings_carry_sub_agents_hidden(app_client, monkeypatch):
    from agent_os.api.routes import settings as settings_routes

    latched = []
    monkeypatch.setattr(settings_routes.telemetry, "latch", latched.append)
    assert app_client.get("/api/v2/settings").json()["sub_agents_hidden"] == []
    r = app_client.put("/api/v2/settings",
                       json={"sub_agents_hidden": ["gemini-cli", "aider", "gemini-cli"]})
    assert r.status_code == 200
    assert r.json()["sub_agents_hidden"] == ["aider", "gemini-cli"]
    # Another field leaves it alone; an explicit list replaces it.
    app_client.put("/api/v2/settings", json={"telemetry_enabled": True})
    assert app_client.get("/api/v2/settings").json()["sub_agents_hidden"] == ["aider", "gemini-cli"]
    app_client.put("/api/v2/settings", json={"sub_agents_hidden": []})
    assert app_client.get("/api/v2/settings").json()["sub_agents_hidden"] == []
    app_client.put("/api/v2/settings", json={"setup_api_key_skipped": True})
    assert "key_skipped" in latched
