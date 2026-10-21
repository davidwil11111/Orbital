# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Per-project sticky pin: ``default_pinned_target`` / ``effective_pinned_target``.

Each project remembers which agent the user talks to (last choice wins): the
session pin PATCH writes it, PUT /projects edits it, and a new session starts
pinned to it. On a keyless project (``manager_ready`` false) a project with
no usable default still starts on the first available worker, because
Orbital's own agent cannot answer there.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.unit.keyless_harness import (
    agent_status, build_client, create_project,
)


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch):
    monkeypatch.delenv("AGENT_OS_API_KEY", raising=False)


def _project(tmp_path, *, keyed, statuses=None, name="kl"):
    tc, deps = build_client(tmp_path, keyed=keyed, statuses=statuses)
    ws = tmp_path / f"ws-{name}"
    ws.mkdir(parents=True)
    row = create_project(tc, ws, name=name)
    return tc, deps, row


def _materialize(tc, pid, target="codex", content="hello"):
    """A first pinned send — the composer's path for a fresh session."""
    sid = tc.post(f"/api/v2/agents/{pid}/new-session").json()["session_id"]
    r = tc.post(f"/api/v2/agents/{pid}/inject", json={
        "content": content, "target": target, "session_id": sid,
    })
    assert r.status_code == 200, r.text
    return sid


def _session_start_meta(workspace, sid):
    path = os.path.join(workspace, "orbital", "sessions", f"{sid}.jsonl")
    with open(path, encoding="utf-8") as fh:
        first = json.loads(fh.readline())
    assert first["role"] == "meta" and first["event"] == "session_start"
    return first


# ---------------------------------------------------------------------------
# Row fields
# ---------------------------------------------------------------------------


class TestRowFields:
    def test_new_keyless_project_starts_on_the_first_available_worker(self, tmp_path):
        _tc, _deps, row = _project(tmp_path, keyed=False)
        assert row["default_pinned_target"] is None
        assert row["effective_pinned_target"] == "claude-code"

    def test_new_keyed_project_starts_on_orbital(self, tmp_path):
        _tc, _deps, row = _project(tmp_path, keyed=True)
        assert row["default_pinned_target"] is None
        assert row["effective_pinned_target"] is None

    def test_default_wins_when_available(self, tmp_path):
        for keyed in (True, False):
            tc, _deps, row = _project(tmp_path / str(keyed), keyed=keyed)
            pid = row["project_id"]
            put = tc.put(f"/api/v2/projects/{pid}",
                         json={"default_pinned_target": "codex"}).json()
            assert put["default_pinned_target"] == "codex"
            assert put["effective_pinned_target"] == "codex"
            got = tc.get(f"/api/v2/projects/{pid}").json()
            assert got["effective_pinned_target"] == "codex"
            listed = [p for p in tc.get("/api/v2/projects").json()
                      if p["project_id"] == pid][0]
            assert listed["effective_pinned_target"] == "codex"

    def test_effective_is_never_persisted(self, tmp_path):
        tc, deps, row = _project(tmp_path, keyed=False)
        stored = deps["project_store"].get_project(row["project_id"])
        assert "effective_pinned_target" not in stored
        assert "manager_ready" not in stored


class TestAvailability:
    def _statuses(self, **codex):
        return [agent_status("built-in"), agent_status("claude-code"),
                agent_status("codex", **codex)]

    @pytest.mark.parametrize("codex", [
        {"creds": False}, {"installed": False}, {"update_needed": True},
    ])
    def test_unavailable_default_falls_through_only_when_keyless(self, tmp_path, codex):
        for keyed, expected in ((False, "claude-code"), (True, None)):
            tc, deps, row = _project(tmp_path / str(keyed), keyed=keyed,
                                     statuses=self._statuses(**codex))
            pid = row["project_id"]
            deps["project_store"].update_project(
                pid, {"default_pinned_target": "codex"})
            got = tc.get(f"/api/v2/projects/{pid}").json()
            assert got["default_pinned_target"] == "codex"
            assert got["effective_pinned_target"] == expected

    def test_disabled_on_the_project_is_unavailable(self, tmp_path):
        tc, _deps, row = _project(tmp_path, keyed=False)
        pid = row["project_id"]
        got = tc.put(f"/api/v2/projects/{pid}", json={
            "default_pinned_target": "codex",
            "disabled_sub_agents": ["codex"],
        }).json()
        assert got["effective_pinned_target"] == "claude-code"
        got = tc.put(f"/api/v2/projects/{pid}", json={
            "disabled_sub_agents": ["codex", "claude-code"],
        }).json()
        assert got["effective_pinned_target"] is None

    def test_no_worker_at_all(self, tmp_path):
        _tc, _deps, row = _project(
            tmp_path, keyed=False,
            statuses=[agent_status("built-in"),
                      agent_status("codex", creds=False)])
        assert row["effective_pinned_target"] is None

    def test_engine_order_decides_the_first_worker(self, tmp_path):
        _tc, _deps, row = _project(
            tmp_path, keyed=False,
            statuses=[agent_status("codex"), agent_status("claude-code")])
        assert row["effective_pinned_target"] == "codex"


# ---------------------------------------------------------------------------
# PUT /projects/{id} default_pinned_target
# ---------------------------------------------------------------------------


class TestPutDefault:
    def test_tri_state(self, tmp_path):
        tc, _deps, row = _project(tmp_path, keyed=True)
        pid = row["project_id"]
        tc.put(f"/api/v2/projects/{pid}", json={"default_pinned_target": "codex"})
        # Omitted = untouched.
        got = tc.put(f"/api/v2/projects/{pid}", json={"name": "renamed"}).json()
        assert got["default_pinned_target"] == "codex"
        # Explicit null = clear (= Orbital).
        got = tc.put(f"/api/v2/projects/{pid}",
                     json={"default_pinned_target": None}).json()
        assert got["default_pinned_target"] is None
        assert got["effective_pinned_target"] is None

    @pytest.mark.parametrize("slug", ["nope", "built-in", "orbital", "@orbital", ""])
    def test_unknown_slug_is_400(self, tmp_path, slug):
        tc, deps, row = _project(tmp_path, keyed=True)
        pid = row["project_id"]
        r = tc.put(f"/api/v2/projects/{pid}", json={"default_pinned_target": slug})
        assert r.status_code == 400, r.text
        assert deps["project_store"].get_project(pid)["default_pinned_target"] is None

    def test_a_registered_but_logged_out_slug_is_accepted(self, tmp_path):
        tc, _deps, row = _project(
            tmp_path, keyed=True,
            statuses=[agent_status("claude-code"), agent_status("codex", creds=False)])
        pid = row["project_id"]
        r = tc.put(f"/api/v2/projects/{pid}", json={"default_pinned_target": "codex"})
        assert r.status_code == 200
        assert r.json()["default_pinned_target"] == "codex"
        assert r.json()["effective_pinned_target"] is None


# ---------------------------------------------------------------------------
# PATCH /agents/{pid}/sessions/{sid} writes the project default
# ---------------------------------------------------------------------------


class TestPatchWritesDefault:
    def test_last_choice_wins(self, tmp_path):
        tc, deps, row = _project(tmp_path, keyed=True)
        pid = row["project_id"]
        s1 = _materialize(tc, pid, "codex")
        s2 = _materialize(tc, pid, "claude-code")
        assert tc.patch(f"/api/v2/agents/{pid}/sessions/{s1}",
                        json={"pinned_target": "codex"}).status_code == 200
        assert tc.get(f"/api/v2/projects/{pid}").json()["default_pinned_target"] == "codex"
        tc.patch(f"/api/v2/agents/{pid}/sessions/{s2}",
                 json={"pinned_target": "claude-code"})
        got = tc.get(f"/api/v2/projects/{pid}").json()
        assert got["default_pinned_target"] == "claude-code"
        assert got["effective_pinned_target"] == "claude-code"

    def test_null_clears_the_default(self, tmp_path):
        tc, _deps, row = _project(tmp_path, keyed=True)
        pid = row["project_id"]
        sid = _materialize(tc, pid, "codex")
        tc.patch(f"/api/v2/agents/{pid}/sessions/{sid}", json={"pinned_target": "codex"})
        r = tc.patch(f"/api/v2/agents/{pid}/sessions/{sid}", json={"pinned_target": None})
        assert r.status_code == 200
        got = tc.get(f"/api/v2/projects/{pid}").json()
        assert got["default_pinned_target"] is None
        assert got["effective_pinned_target"] is None

    def test_null_on_a_keyless_project_lands_on_the_first_worker(self, tmp_path):
        tc, _deps, row = _project(tmp_path, keyed=False)
        pid = row["project_id"]
        sid = _materialize(tc, pid, "codex")
        tc.patch(f"/api/v2/agents/{pid}/sessions/{sid}", json={"pinned_target": "codex"})
        tc.patch(f"/api/v2/agents/{pid}/sessions/{sid}", json={"pinned_target": None})
        got = tc.get(f"/api/v2/projects/{pid}").json()
        assert got["default_pinned_target"] is None
        assert got["effective_pinned_target"] == "claude-code"
        assert tc.post(f"/api/v2/agents/{pid}/new-session").json()[
            "pinned_target"] == "claude-code"

    def test_a_patch_without_pinned_target_leaves_the_default(self, tmp_path):
        tc, deps, row = _project(tmp_path, keyed=True)
        pid = row["project_id"]
        sid = _materialize(tc, pid, "codex")
        tc.patch(f"/api/v2/agents/{pid}/sessions/{sid}", json={"pinned_target": "codex"})
        tc.patch(f"/api/v2/agents/{pid}/sessions/{sid}", json={"name": "hi"})
        assert deps["project_store"].get_project(pid)["default_pinned_target"] == "codex"

    def test_a_failed_patch_leaves_the_default(self, tmp_path):
        tc, deps, row = _project(tmp_path, keyed=True)
        pid = row["project_id"]
        r = tc.patch(f"/api/v2/agents/{pid}/sessions/no-such-session",
                     json={"pinned_target": "codex"})
        assert r.status_code == 404
        assert deps["project_store"].get_project(pid)["default_pinned_target"] is None
        r = tc.patch(f"/api/v2/agents/{pid}/sessions/whatever",
                     json={"pinned_target": "not-an-agent"})
        assert r.status_code == 422
        assert deps["project_store"].get_project(pid)["default_pinned_target"] is None


# ---------------------------------------------------------------------------
# B4 — new-session seeding and the first pinned inject
# ---------------------------------------------------------------------------


class TestNewSessionSeeding:
    def test_new_session_returns_the_effective_target(self, tmp_path):
        tc, _deps, row = _project(tmp_path, keyed=True)
        pid = row["project_id"]
        body = tc.post(f"/api/v2/agents/{pid}/new-session").json()
        assert body["pinned_target"] is None
        assert body["session_id"] and body["session_uuid"] == body["session_id"]
        tc.put(f"/api/v2/projects/{pid}", json={"default_pinned_target": "codex"})
        assert tc.post(f"/api/v2/agents/{pid}/new-session").json()[
            "pinned_target"] == "codex"

    def test_new_session_on_a_keyless_project(self, tmp_path):
        tc, _deps, row = _project(tmp_path, keyed=False)
        body = tc.post(f"/api/v2/agents/{row['project_id']}/new-session").json()
        assert body["pinned_target"] == "claude-code"

    def test_new_session_still_writes_nothing(self, tmp_path):
        tc, _deps, row = _project(tmp_path, keyed=False)
        pid = row["project_id"]
        sid = tc.post(f"/api/v2/agents/{pid}/new-session").json()["session_id"]
        path = os.path.join(row["workspace"], "orbital", "sessions", f"{sid}.jsonl")
        assert not os.path.exists(path)
        assert tc.get(f"/api/v2/projects/{pid}/sessions").json()["sessions"] == []

    def test_first_pinned_inject_persists_the_pin(self, tmp_path):
        """The frontend sends ``target=<slug>`` on a fresh session's first
        message; the session it materializes is pinned to that slug — in the
        session_start meta row and in the session list — with no PATCH."""
        tc, deps, row = _project(tmp_path, keyed=False)
        pid = row["project_id"]
        sid = _materialize(tc, pid, target="claude-code", content="first")
        meta = _session_start_meta(row["workspace"], sid)
        assert meta["pinned_target"] == "claude-code"
        sessions = tc.get(f"/api/v2/projects/{pid}/sessions").json()["sessions"]
        entry = [s for s in sessions if s["session_id"] == sid][0]
        assert entry["pinned_target"] == "claude-code"
        sent = deps["sub_agent_manager"].send.await_args
        assert sent.args[1] == "claude-code"
        assert sent.kwargs["initiator"] == "user_pinned"
        assert sent.kwargs["session_id"] == sid

    def test_a_later_targeted_inject_does_not_repin(self, tmp_path):
        tc, _deps, row = _project(tmp_path, keyed=True)
        pid = row["project_id"]
        sid = _materialize(tc, pid, target="codex")
        tc.patch(f"/api/v2/agents/{pid}/sessions/{sid}", json={"pinned_target": None})
        tc.post(f"/api/v2/agents/{pid}/inject", json={
            "content": "again", "target": "claude-code", "session_id": sid})
        assert _session_start_meta(row["workspace"], sid).get("pinned_target") is None
