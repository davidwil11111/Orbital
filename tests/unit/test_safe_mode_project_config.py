# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 117 §3.1 — safe mode on the project record.

D1: ``AgentConfig.safe_mode`` defaults to True (an absent key means ON, so
every project created before this build keeps today's sandbox), while the
creation route writes ``False`` explicitly unless the request says otherwise.
The store round-trips ``unsandboxed_executables`` and
``pending_unsandboxed_runs``; the update route replaces the executable list
wholesale (same semantics as ``approved_domains``).
"""

import json
import os

import pytest

from agent_os.daemon_v2.models import AgentConfig
from agent_os.daemon_v2.project_store import ProjectStore


# ---------------------------------------------------------------------------
# Dataclass + store
# ---------------------------------------------------------------------------


def test_agent_config_defaults_to_safe_mode_on():
    cfg = AgentConfig(workspace="/tmp/w", model="m", api_key="k")
    assert cfg.safe_mode is True
    assert cfg.unsandboxed_executables == []
    assert cfg.pending_unsandboxed_runs == []


def test_store_loads_a_record_without_the_key_as_safe_mode_on(tmp_path):
    """A project JSON written by an older build has no ``safe_mode`` key."""
    legacy = {
        "proj_old": {
            "project_id": "proj_old", "name": "Old", "workspace": str(tmp_path),
            "agent_name": "Old",
        }
    }
    with open(tmp_path / "projects.json", "w", encoding="utf-8") as f:
        json.dump(legacy, f)
    store = ProjectStore(data_dir=str(tmp_path))
    project = store.get_project("proj_old")
    assert project["safe_mode"] is True
    assert project["unsandboxed_executables"] == []
    assert project["pending_unsandboxed_runs"] == []


def test_store_list_defaults_do_not_alias_one_list_across_projects(tmp_path):
    store = ProjectStore(data_dir=str(tmp_path))
    a = store.create_project({"name": "A", "workspace": str(tmp_path)})
    b = store.create_project({"name": "B", "workspace": str(tmp_path)})
    store.get_project(a)["unsandboxed_executables"].append("git")
    assert store.get_project(b)["unsandboxed_executables"] == []


def test_store_round_trips_the_three_fields(tmp_path):
    store = ProjectStore(data_dir=str(tmp_path))
    pid = store.create_project({
        "name": "P", "workspace": str(tmp_path), "safe_mode": False,
    })
    store.update_project(pid, {
        "safe_mode": True,
        "unsandboxed_executables": ["git", "gh"],
        "pending_unsandboxed_runs": [{
            "id": "ur_1", "command": "git push", "reason": "denied",
            "created_at": "2026-10-18T00:00:00+00:00", "session_id": None,
        }],
    })
    reloaded = ProjectStore(data_dir=str(tmp_path)).get_project(pid)
    assert reloaded["safe_mode"] is True
    assert reloaded["unsandboxed_executables"] == ["git", "gh"]
    assert reloaded["pending_unsandboxed_runs"][0]["id"] == "ur_1"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    from agent_os.api.app import create_app
    from starlette.testclient import TestClient
    app = create_app(data_dir=str(tmp_path))
    return TestClient(app)


def _ws(tmp_path, name="ws"):
    ws = tmp_path / name
    ws.mkdir(exist_ok=True)
    return str(ws)


def test_create_without_safe_mode_stores_false(client, tmp_path):
    resp = client.post("/api/v2/projects", json={"name": "Open", "workspace": _ws(tmp_path)})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["safe_mode"] is False
    assert body["unsandboxed_executables"] == []
    assert body["pending_unsandboxed_runs"] == []
    got = client.get(f"/api/v2/projects/{body['project_id']}").json()
    assert got["safe_mode"] is False


def test_create_with_safe_mode_true_stores_true(client, tmp_path):
    resp = client.post("/api/v2/projects", json={
        "name": "Safe", "workspace": _ws(tmp_path), "safe_mode": True,
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["safe_mode"] is True


def test_list_entries_carry_the_fields_and_default_legacy_rows_to_on(client, tmp_path):
    """``GET /projects`` bypasses ``get_project`` — the wire shape must still
    default a legacy row (no key on disk) to safe mode ON."""
    from agent_os.api.routes import agents_v2
    store = agents_v2._project_store
    pid = store.create_project({"name": "Legacy", "workspace": _ws(tmp_path)})
    store._projects[pid].pop("safe_mode", None)
    store._save()
    rows = {p["project_id"]: p for p in client.get("/api/v2/projects").json()}
    assert rows[pid]["safe_mode"] is True
    assert rows[pid]["unsandboxed_executables"] == []
    assert rows[pid]["pending_unsandboxed_runs"] == []


def test_update_round_trips_safe_mode_and_the_executable_list(client, tmp_path):
    pid = client.post("/api/v2/projects", json={
        "name": "P", "workspace": _ws(tmp_path),
    }).json()["project_id"]
    resp = client.put(f"/api/v2/projects/{pid}", json={
        "safe_mode": True, "unsandboxed_executables": ["git", " gh ", "git", ""],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["safe_mode"] is True
    # Replace-list semantics, trimmed + deduped, empties dropped.
    assert body["unsandboxed_executables"] == ["git", "gh"]
    resp = client.put(f"/api/v2/projects/{pid}", json={"unsandboxed_executables": []})
    assert resp.json()["unsandboxed_executables"] == []
    # A PUT that does not mention the field leaves it alone.
    resp = client.put(f"/api/v2/projects/{pid}", json={"name": "P2"})
    assert resp.json()["safe_mode"] is True


def test_resolve_unknown_parked_run_is_404(client, tmp_path):
    pid = client.post("/api/v2/projects", json={
        "name": "P", "workspace": _ws(tmp_path),
    }).json()["project_id"]
    resp = client.post(
        f"/api/v2/projects/{pid}/unsandboxed-runs/nope/resolve", json={"decision": "deny"},
    )
    assert resp.status_code == 404


def test_resolve_rejects_an_unknown_decision(client, tmp_path):
    from agent_os.api.routes import agents_v2
    pid = client.post("/api/v2/projects", json={
        "name": "P", "workspace": _ws(tmp_path),
    }).json()["project_id"]
    agents_v2._project_store.update_project(pid, {"pending_unsandboxed_runs": [{
        "id": "ur_1", "command": "ls", "reason": "r",
        "created_at": "2026-10-18T00:00:00+00:00", "session_id": None,
    }]})
    resp = client.post(
        f"/api/v2/projects/{pid}/unsandboxed-runs/ur_1/resolve", json={"decision": "maybe"},
    )
    assert resp.status_code == 422


def test_resolve_deny_drops_the_parked_run(client, tmp_path):
    from agent_os.api.routes import agents_v2
    pid = client.post("/api/v2/projects", json={
        "name": "P", "workspace": _ws(tmp_path), "safe_mode": True,
    }).json()["project_id"]
    agents_v2._project_store.update_project(pid, {"pending_unsandboxed_runs": [{
        "id": "ur_1", "command": "ls ~/.ssh", "reason": "r",
        "created_at": "2026-10-18T00:00:00+00:00", "session_id": None,
    }]})
    resp = client.post(
        f"/api/v2/projects/{pid}/unsandboxed-runs/ur_1/resolve", json={"decision": "deny"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["pending_unsandboxed_runs"] == []
    assert client.get(f"/api/v2/projects/{pid}").json()["pending_unsandboxed_runs"] == []


# ---------------------------------------------------------------------------
# Coordinator additions (2026-10-18): granted_folders on the wire shape, and
# the startup-created scratch project is open like any other new project.
# ---------------------------------------------------------------------------


def _grant(client, pid, path, mode):
    """Grant a portal straight on the daemon's provider under the project's scope."""
    from agent_os.api.routes import agents_v2
    provider = agents_v2._agent_manager._platform_provider
    workspace = agents_v2._project_store.get_project(pid)["workspace"]
    provider.grant_folder_access(path, mode, scope=os.path.realpath(workspace))


def test_granted_folders_on_get_and_list(client, tmp_path):
    from agent_os.api.routes import agents_v2
    if agents_v2._agent_manager is None or agents_v2._agent_manager._platform_provider is None:
        pytest.skip("no platform provider wired in this app build")
    pid = client.post("/api/v2/projects", json={
        "name": "P", "workspace": _ws(tmp_path),
    }).json()["project_id"]
    got = client.get(f"/api/v2/projects/{pid}").json()
    assert got["granted_folders"] == []
    ro = str(tmp_path / "ro"); rw = str(tmp_path / "rw")
    os.makedirs(ro); os.makedirs(rw)
    _grant(client, pid, ro, "read")
    _grant(client, pid, rw, "read_write")
    got = client.get(f"/api/v2/projects/{pid}").json()
    assert sorted(got["granted_folders"], key=lambda f: f["path"]) == [
        {"path": ro, "mode": "read_only"}, {"path": rw, "mode": "read_write"},
    ]
    rows = {p["project_id"]: p for p in client.get("/api/v2/projects").json()}
    assert rows[pid]["granted_folders"] == got["granted_folders"]
    # Another project's grants never leak in.
    other = client.post("/api/v2/projects", json={
        "name": "Q", "workspace": _ws(tmp_path, "ws2"),
    }).json()
    assert other["granted_folders"] == []


def test_scratch_project_created_at_startup_is_open(tmp_path):
    """The daemon's Quick Tasks project is made through the store at startup,
    not the create route — it must be written open (safe_mode False) so a
    fresh install is open everywhere; only pre-existing rows load as on."""
    from unittest.mock import MagicMock
    from agent_os.api import app as app_module
    store = ProjectStore(data_dir=str(tmp_path))
    settings_store = MagicMock()
    settings_store.get.return_value = MagicMock(scratch_workspace=str(tmp_path / "scratch"))
    app_module._ensure_scratch_project(store, settings_store, str(tmp_path))
    scratch = store.find_scratch_project()
    assert scratch is not None
    assert scratch["safe_mode"] is False
    assert "safe_mode" in store._projects[scratch["project_id"]]  # written, not defaulted
