# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Codex automations → Orbital schedule triggers (session import, contract §6).

``<codex_dir>/automations/<id>/automation.toml`` whose ``target_thread_id`` is
a thread of the project's folder becomes a schedule trigger assigned to
``codex`` that fires into that thread's imported session. Idempotent on
``imported_from.codex_automation_id``.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock

import pytest

from agent_os.onboarding import codex_automations as ca
from agent_os.onboarding import session_import as si
from tests.unit.test_session_import import (
    CODEX_ID, codex_rollout_rows, make_codex_home, write_codex_rollout,
)

CODEX_SID = "imp_codex_019f0000aaaa7bbb"


# --------------------------------------------------------------------------
# RRULE → cron
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rrule,cron,human", [
    ("RRULE:FREQ=DAILY;BYHOUR=10;BYMINUTE=30;BYSECOND=0", "30 10 * * *", "Daily at 10:30"),
    ("FREQ=DAILY;BYHOUR=9,17;BYMINUTE=0", "0 9,17 * * *", "Daily at 09:00, 17:00"),
    ("RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR;BYHOUR=8;BYMINUTE=5", "5 8 * * 1,3,5",
     "Weekly on Mon, Wed, Fri at 08:05"),
    ("RRULE:FREQ=WEEKLY;BYDAY=SU;BYHOUR=23;BYMINUTE=0;WKST=MO", "0 23 * * 0",
     "Weekly on Sun at 23:00"),
    ("RRULE:FREQ=HOURLY;BYMINUTE=15", "15 * * * *", "Hourly at :15"),
    ("RRULE:FREQ=HOURLY;INTERVAL=2;BYMINUTE=0", "0 */2 * * *", "Every 2 hours at :00"),
    ("RRULE:FREQ=HOURLY", "0 * * * *", "Hourly at :00"),
])
def test_rrule_maps_to_cron(rrule, cron, human):
    assert ca.rrule_to_cron(rrule) == (cron, human, True)


@pytest.mark.parametrize("rrule", [
    "RRULE:FREQ=MONTHLY;BYMONTHDAY=1;BYHOUR=9",
    "RRULE:FREQ=DAILY;INTERVAL=2;BYHOUR=9",
    "RRULE:FREQ=WEEKLY;BYHOUR=9",               # no BYDAY: needs DTSTART
    "RRULE:FREQ=DAILY;BYHOUR=9;COUNT=3",
    "RRULE:FREQ=DAILY;BYHOUR=9;BYSECOND=30",
    "RRULE:FREQ=DAILY",                          # no BYHOUR: needs DTSTART
    "RRULE:FREQ=WEEKLY;BYDAY=1MO;BYHOUR=9",      # ordinal weekday
    "garbage",
    "",
])
def test_unmappable_rrule_falls_back_disabled(rrule):
    assert ca.rrule_to_cron(rrule) == ("0 9 * * *", rrule, False)


# --------------------------------------------------------------------------
# Parsing + matching + record building
# --------------------------------------------------------------------------


def _write_automation(codex_dir, aid, *, target=CODEX_ID, status="ACTIVE",
                      rrule="RRULE:FREQ=DAILY;BYHOUR=10;BYMINUTE=30;BYSECOND=0",
                      name="Daily radar", prompt="Reply with the single word PONG"):
    d = os.path.join(codex_dir, "automations", aid)
    os.makedirs(d, exist_ok=True)
    lines = [
        "version = 1", f'id = "{aid}"', 'kind = "heartbeat"', f'name = "{name}"',
        f'prompt = "{prompt}"', f'status = "{status}"', f'rrule = "{rrule}"',
    ]
    if target is not None:
        lines.append(f'target_thread_id = "{target}"')
    lines += ["created_at = 1789529007611", "updated_at = 1792376748916"]
    with open(os.path.join(d, "automation.toml"), "w") as fh:
        fh.write("\n".join(lines) + "\n")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    si.clear_cache()
    codex_dir = str(tmp_path / "codex-home")
    monkeypatch.setenv("ORBITAL_CODEX_HOME", codex_dir)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    ws = tmp_path / "ws"
    ws.mkdir()
    ws = os.path.realpath(str(ws))
    rollout = write_codex_rollout(codex_dir, ws, codex_rollout_rows(ws))
    make_codex_home(codex_dir, [
        {"id": CODEX_ID, "cwd": ws, "rollout_path": rollout,
         "first_user_message": "Rename the helper"},
        {"id": "other-thread", "cwd": str(tmp_path), "rollout_path": rollout,
         "first_user_message": "elsewhere"},
    ])
    yield {"codex_dir": codex_dir, "ws": ws, "tmp": tmp_path}
    si.clear_cache()


def test_load_automations_parses_toml_and_skips_broken_files(setup):
    _write_automation(setup["codex_dir"], "a1")
    broken = os.path.join(setup["codex_dir"], "automations", "broken")
    os.makedirs(broken)
    with open(os.path.join(broken, "automation.toml"), "w") as fh:
        fh.write("this is = = not toml\n")
    autos = ca.load_automations(setup["codex_dir"])
    assert [a["id"] for a in autos] == ["a1"]
    assert autos[0]["target_thread_id"] == CODEX_ID
    assert autos[0]["prompt"] == "Reply with the single word PONG"


def test_plan_imports_only_automations_targeting_this_folder(setup):
    _write_automation(setup["codex_dir"], "mine")
    _write_automation(setup["codex_dir"], "theirs", target="other-thread")
    _write_automation(setup["codex_dir"], "no-target", target=None)
    new, skipped = ca.plan_automation_imports(setup["ws"], existing_triggers=[],
                                              now="2026-10-21T00:00:00+00:00")
    assert skipped == 0
    assert len(new) == 1
    t = new[0]
    assert t["id"].startswith("trg_")
    assert {k: t[k] for k in ("name", "type", "task", "agent", "enabled", "session_id",
                              "imported_from", "last_triggered", "trigger_count",
                              "created_at")} == {
        "name": "Daily radar", "type": "schedule",
        "task": "Reply with the single word PONG", "agent": "codex", "enabled": True,
        "session_id": CODEX_SID, "imported_from": {"codex_automation_id": "mine"},
        "last_triggered": None, "trigger_count": 0,
        "created_at": "2026-10-21T00:00:00+00:00",
    }
    assert t["schedule"] == {"cron": "30 10 * * *", "human": "Daily at 10:30",
                             "timezone": "Asia/Shanghai"}
    from agent_os.daemon_v2.trigger_manager import validate_trigger
    assert validate_trigger(t, workspace=setup["ws"]) is None


def test_paused_or_unmappable_automations_import_disabled(setup):
    _write_automation(setup["codex_dir"], "paused", status="PAUSED")
    _write_automation(setup["codex_dir"], "monthly",
                      rrule="RRULE:FREQ=MONTHLY;BYMONTHDAY=1;BYHOUR=9")
    new, _ = ca.plan_automation_imports(setup["ws"], existing_triggers=[])
    by_id = {t["imported_from"]["codex_automation_id"]: t for t in new}
    assert by_id["paused"]["enabled"] is False
    assert by_id["paused"]["schedule"]["cron"] == "30 10 * * *"
    assert by_id["monthly"]["enabled"] is False
    assert by_id["monthly"]["schedule"] == {
        "cron": "0 9 * * *", "human": "RRULE:FREQ=MONTHLY;BYMONTHDAY=1;BYHOUR=9",
        "timezone": "Asia/Shanghai"}


def test_plan_is_idempotent_on_the_automation_id(setup):
    _write_automation(setup["codex_dir"], "mine")
    existing = [{"id": "trg_x", "imported_from": {"codex_automation_id": "mine"}}]
    new, skipped = ca.plan_automation_imports(setup["ws"], existing_triggers=existing)
    assert (new, skipped) == ([], 1)


def test_target_thread_started_by_orbital_is_not_imported(setup, tmp_path):
    """Its thread is not an imported session, so there is nothing to continue."""
    rollout = write_codex_rollout(setup["codex_dir"], setup["ws"],
                                  codex_rollout_rows(setup["ws"]), tid="orb-thread")
    import sqlite3
    con = sqlite3.connect(os.path.join(setup["codex_dir"], "state_5.sqlite"))
    con.execute("INSERT INTO threads (id, rollout_path, created_at, updated_at, source,"
                " cwd, title, first_user_message, originator) VALUES"
                " ('orb-thread', ?, 1, 1, 'vscode', ?, 't', 'x', 'orbital')",
                (rollout, setup["ws"]))
    con.commit()
    con.close()
    si.clear_cache()
    _write_automation(setup["codex_dir"], "orb", target="orb-thread")
    new, skipped = ca.plan_automation_imports(setup["ws"], existing_triggers=[])
    assert new == [] and skipped == 0


def test_missing_automations_dir_plans_nothing(setup, tmp_path, monkeypatch):
    monkeypatch.setenv("ORBITAL_CODEX_HOME", str(tmp_path / "nowhere"))
    assert ca.plan_automation_imports(setup["ws"], existing_triggers=[]) == ([], 0)


# --------------------------------------------------------------------------
# I6 — the coordinator
# --------------------------------------------------------------------------


def _coordinator(setup):
    from agent_os.daemon_v2.project_store import ProjectStore
    from agent_os.onboarding.import_coordinator import ImportCoordinator
    store = ProjectStore(data_dir=tempfile.mkdtemp(dir=str(setup["tmp"])))
    pid = store.create_project({"name": "P", "workspace": setup["ws"]})
    agent_manager = MagicMock()
    agent_manager.list_sessions.return_value = [
        {"session_id": CODEX_SID, "origin": "imported",
         "imported": {"materialized": False}},
        {"session_id": "imp_claude-code_aaaaaaaaaaaaaaaa", "origin": "imported",
         "imported": {"materialized": True}},
        {"session_id": "p_1", "origin": "chat"},
    ]
    tm = MagicMock()
    coord = ImportCoordinator(store, agent_manager, trigger_manager=lambda: tm)
    return coord, store, pid, tm


def test_sync_imports_counts_and_registers(setup):
    _write_automation(setup["codex_dir"], "mine")
    coord, store, pid, tm = _coordinator(setup)
    result = coord.sync_imports(pid)
    assert result == {"sessions": 1, "automations": {"imported": 1, "skipped": 0}}
    triggers = store.get_project(pid)["triggers"]
    assert len(triggers) == 1 and triggers[0]["session_id"] == CODEX_SID
    tm.register_trigger.assert_called_once()
    assert tm.register_trigger.call_args.args == (pid, triggers[0])
    # Second sync: no duplicate.
    assert coord.sync_imports(pid) == {
        "sessions": 1, "automations": {"imported": 0, "skipped": 1}}
    assert len(store.get_project(pid)["triggers"]) == 1


def test_sync_imports_never_raises(setup, monkeypatch):
    coord, store, pid, tm = _coordinator(setup)
    monkeypatch.setattr(ca, "plan_automation_imports",
                        MagicMock(side_effect=RuntimeError("boom")))
    coord._agent_manager.list_sessions.side_effect = RuntimeError("boom")
    assert coord.sync_imports(pid) == {
        "sessions": 0, "automations": {"imported": 0, "skipped": 0}}
    assert coord.sync_imports("proj_missing") == {
        "sessions": 0, "automations": {"imported": 0, "skipped": 0}}


def test_sync_route(setup):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agent_os.api.routes import imports_v2
    _write_automation(setup["codex_dir"], "mine")
    coord, store, pid, tm = _coordinator(setup)
    agent_manager = MagicMock()
    agent_manager.sync_imports.side_effect = coord.sync_imports
    imports_v2.configure(store, agent_manager)
    app = FastAPI()
    app.include_router(imports_v2.router)
    client = TestClient(app)
    resp = client.post(f"/api/v2/projects/{pid}/imports/sync")
    assert resp.status_code == 200
    assert resp.json() == {"sessions": 1, "automations": {"imported": 1, "skipped": 0}}
    assert client.post("/api/v2/projects/proj_nope/imports/sync").status_code == 404
