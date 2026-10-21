# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Imported sessions in the session list, materialization on first open, and
resume through the existing SubAgentManager path (keyless onboarding,
2026-10-21, contract §6).

A project folder's native Claude Code / Codex sessions are listed as virtual
entries (``imp_<agent>_<16>``) until the user opens one; opening writes the
converted transcript as an ordinary Orbital JSONL whose session_start meta
carries ``origin: imported`` + ``imported_from`` and whose last row is the
``sub_agent_thread`` resume record, so the next send resumes the native
thread through ``_determine_resume`` unchanged.
"""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock

import pytest

from agent_os.daemon_v2.agent_manager import AgentManager
from agent_os.daemon_v2.project_store import ProjectStore
from agent_os.daemon_v2 import session_index
from agent_os.onboarding import session_import as si

from tests.unit.test_session_import import (
    CLAUDE_ID, CODEX_ID, basic_claude_rows, codex_rollout_rows, make_codex_home,
    write_claude_session, write_codex_rollout,
)

CLAUDE_SID = "imp_claude-code_1111111122223333"
CODEX_SID = "imp_codex_019f0000aaaa7bbb"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A project whose workspace has one Claude and one Codex session.

    HOME is redirected so SDKTransport.resume_source_exists (which reads
    ~/.claude/projects) sees the fixture store, never the real one."""
    si.clear_cache()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    claude_dir = str(home / ".claude")
    codex_dir = str(tmp_path / "codex-home")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("ORBITAL_CODEX_HOME", codex_dir)
    monkeypatch.setenv("CODEX_HOME", codex_dir)
    ws = tmp_path / "ws"
    ws.mkdir()
    ws = os.path.realpath(str(ws))
    claude_path = write_claude_session(claude_dir, ws, basic_claude_rows(ws))
    rollout = write_codex_rollout(codex_dir, ws, codex_rollout_rows(ws))
    make_codex_home(codex_dir, [{
        "id": CODEX_ID, "cwd": ws, "rollout_path": rollout, "title": "Rename the helper",
        "first_user_message": "Rename the helper", "updated_at_ms": 1791000000000,
    }])
    store = ProjectStore(data_dir=tempfile.mkdtemp(dir=str(tmp_path)))
    pid = store.create_project({"name": "Imp", "workspace": ws})
    mgr = AgentManager(
        project_store=store, ws_manager=MagicMock(), sub_agent_manager=MagicMock(),
        activity_translator=MagicMock(), process_manager=MagicMock(),
        platform_provider=None, registry=MagicMock(), setup_engine=MagicMock(),
        settings_store=None, credential_store=None,
    )
    mgr._state_file = tmp_path / "daemon-state.json"
    yield {"mgr": mgr, "store": store, "pid": pid, "ws": ws,
           "claude_path": claude_path, "rollout": rollout, "codex_dir": codex_dir}
    si.clear_cache()


def _by_id(entries):
    return {e["session_id"]: e for e in entries}


def _read_jsonl(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# --------------------------------------------------------------------------
# I3 — virtual entries
# --------------------------------------------------------------------------


def test_list_sessions_has_virtual_entries_in_the_contract_shape(env):
    entries = _by_id(env["mgr"].list_sessions(env["pid"]))
    assert set(entries) == {CLAUDE_SID, CODEX_SID}
    c = entries[CLAUDE_SID]
    assert c["session_uuid"] == CLAUDE_SID
    assert c["status"] == "idle"
    assert c["origin"] == "imported"
    assert c["name"] == "Flaky test fix"
    assert c["pinned_target"] == "claude-code"
    assert c["imported"] == {"agent": "claude-code", "native_id": CLAUDE_ID,
                             "materialized": False,
                             "size_bytes": os.path.getsize(env["claude_path"])}
    assert c["last_activity_at"] == "2026-10-01T11:00:01+00:00"
    assert (c["last_user_at"], c["last_reply_at"], c["last_terminal_event"]) == (
        None, None, None)
    assert c["scope"] == env["mgr"].get_session_scope(env["pid"], CLAUDE_SID)
    x = entries[CODEX_SID]
    assert x["pinned_target"] == "codex" and x["name"] == "Rename the helper"
    assert x["imported"]["native_id"] == CODEX_ID
    # Never written: listing copies nothing.
    assert not os.path.exists(os.path.join(env["ws"], "orbital", "sessions"))


def test_virtual_entries_never_become_the_default_chat_session(env):
    """origin "imported" keeps the inject None-policy from adopting one.

    The project is named "Imp", so its minted chat ids look like
    ``imp_<hex8>`` — which must not be taken for an imported session id."""
    sid = env["mgr"]._ensure_chat_session(env["pid"])
    assert sid not in (CLAUDE_SID, CODEX_SID)
    assert not si.is_imported_session_id(sid)
    assert env["mgr"].materialize_imported_session(env["pid"], sid) == "not_imported"


def test_derive_disk_entry_exposes_origin_and_imported_from(tmp_path):
    p = tmp_path / "imp_codex_abc.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in [
        {"role": "meta", "event": "session_start", "origin": "imported",
         "imported_from": {"agent": "codex", "native_id": "N1"}, "name": "x",
         "pinned_target": "codex"},
        {"role": "user", "content": "hi", "timestamp": "2026-10-01T00:00:00+00:00"},
    ]) + "\n")
    entry = session_index.derive_disk_entry(str(p), "imp_codex_abc")
    assert entry["origin"] == "imported"
    assert entry["imported_from"] == {"agent": "codex", "native_id": "N1"}
    assert entry["imported"] == {"agent": "codex", "native_id": "N1", "materialized": True}
    # An ordinary log keeps its exact shape (no new keys).
    q = tmp_path / "p_1.jsonl"
    q.write_text(json.dumps({"role": "user", "content": "hi"}) + "\n")
    plain = session_index.derive_disk_entry(str(q), "p_1")
    assert "imported_from" not in plain and "imported" not in plain


# --------------------------------------------------------------------------
# I4 — materialization
# --------------------------------------------------------------------------


def test_materialize_writes_meta_header_rows_and_resume_record(env):
    mgr, pid, ws = env["mgr"], env["pid"], env["ws"]
    assert mgr.materialize_imported_session(pid, CODEX_SID) == "materialized"
    path = os.path.join(ws, "orbital", "sessions", f"{CODEX_SID}.jsonl")
    rows = _read_jsonl(path)
    meta = rows[0]
    assert meta["role"] == "meta" and meta["event"] == "session_start"
    assert meta["session_id"] == meta["session_uuid"] == CODEX_SID
    assert meta["origin"] == "imported"
    assert meta["pinned_target"] == "codex"
    assert meta["imported_from"] == {"agent": "codex", "native_id": CODEX_ID}
    assert meta["name"] == "Rename the helper"
    header = rows[1]
    assert header["_meta"]["event"] == "imported_transcript"
    assert header["session_id"] == CODEX_SID
    assert [r["role"] for r in rows[2:-1]] == ["user", "system"]
    assert rows[-2]["_meta"]["reply"].startswith("Renamed.")
    thread = rows[-1]
    assert thread["role"] == "meta" and thread["event"] == "sub_agent_thread"
    assert thread["handle"] == "codex"
    assert thread["thread"]["session_id"] == CODEX_ID
    assert thread["thread"]["rollout_path"] == env["rollout"]
    # No temp file left behind; nothing written outside sessions/.
    assert sorted(os.listdir(os.path.dirname(path))) == [f"{CODEX_SID}.jsonl"]


def test_materialize_is_idempotent(env):
    mgr, pid, ws = env["mgr"], env["pid"], env["ws"]
    assert mgr.materialize_imported_session(pid, CLAUDE_SID) == "materialized"
    path = os.path.join(ws, "orbital", "sessions", f"{CLAUDE_SID}.jsonl")
    before = open(path).read()
    assert mgr.materialize_imported_session(pid, CLAUDE_SID) == "exists"
    assert open(path).read() == before
    assert mgr.materialize_imported_session(pid, "imp_codex_ffffffffffffffff") == "unknown"
    assert mgr.materialize_imported_session(pid, "proj_abcd1234") == "not_imported"


def test_list_dedupes_after_materialization(env):
    mgr, pid = env["mgr"], env["pid"]
    mgr.materialize_imported_session(pid, CODEX_SID)
    entries = mgr.list_sessions(pid)
    codex = [e for e in entries if e.get("imported", {}).get("agent") == "codex"
             or e["session_id"] == CODEX_SID]
    assert len(codex) == 1
    e = codex[0]
    assert e["session_id"] == CODEX_SID and e["origin"] == "imported"
    assert e["imported"]["materialized"] is True
    assert e["pinned_target"] == "codex"
    assert e["last_activity_at"] is not None


def test_list_dedupes_on_native_id_under_another_stem(env):
    """A materialized import is matched by imported_from.native_id, not only
    by its stem."""
    mgr, pid, ws = env["mgr"], env["pid"], env["ws"]
    sessions = os.path.join(ws, "orbital", "sessions")
    os.makedirs(sessions)
    with open(os.path.join(sessions, "imp_renamed_stem.jsonl"), "w") as fh:
        fh.write(json.dumps({"role": "meta", "event": "session_start",
                             "origin": "imported",
                             "imported_from": {"agent": "claude-code",
                                               "native_id": CLAUDE_ID}}) + "\n")
        fh.write(json.dumps({"role": "user", "content": "x",
                             "timestamp": "2026-10-01T00:00:00+00:00"}) + "\n")
    ids = {e["session_id"] for e in mgr.list_sessions(pid)}
    assert CLAUDE_SID not in ids and "imp_renamed_stem" in ids


def test_chat_get_on_a_virtual_id_materializes_and_returns_rows(env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agent_os.api.routes import agents_v2

    agents_v2.configure(env["store"], env["mgr"], MagicMock())
    app = FastAPI()
    app.include_router(agents_v2.router)
    client = TestClient(app)
    resp = client.get(f"/api/v2/agents/{env['pid']}/chat",
                      params={"session_id": CLAUDE_SID})
    assert resp.status_code == 200
    rows = resp.json()
    convo = [r for r in rows if r.get("role") != "meta"]
    assert convo[0]["_meta"]["event"] == "imported_transcript"
    assert [r["role"] for r in convo[1:]] == ["user", "system", "user", "system"]
    assert convo[2]["_meta"]["event"] == "sub_agent_terminal"
    assert os.path.isfile(os.path.join(env["ws"], "orbital", "sessions",
                                       f"{CLAUDE_SID}.jsonl"))


def test_inject_into_a_virtual_id_materializes_before_persisting(env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from unittest.mock import AsyncMock
    from agent_os.api.routes import agents_v2

    sub = MagicMock()
    sub.send = AsyncMock(return_value="Message sent to codex.")
    agents_v2.configure(env["store"], env["mgr"], MagicMock(), sub_agent_manager=sub)
    app = FastAPI()
    app.include_router(agents_v2.router)
    client = TestClient(app)
    resp = client.post(f"/api/v2/agents/{env['pid']}/inject", json={
        "content": "Now PING", "target": "codex", "session_id": CODEX_SID})
    assert resp.status_code == 200, resp.text
    assert sub.send.await_args.kwargs["session_id"] == CODEX_SID
    rows = _read_jsonl(os.path.join(env["ws"], "orbital", "sessions",
                                    f"{CODEX_SID}.jsonl"))
    # The imported transcript precedes the new user row in the same file.
    events = [r.get("_meta", {}).get("event") for r in rows]
    assert "imported_transcript" in events
    assert rows[-1]["role"] == "user" and rows[-1]["content"] == "Now PING"


# --------------------------------------------------------------------------
# I4 — resume through the existing path
# --------------------------------------------------------------------------


def _sub_agent_manager(mgr, transport):
    from agent_os.daemon_v2.sub_agent_manager import SubAgentManager
    sam = SubAgentManager(process_manager=MagicMock(), registry=MagicMock(),
                          setup_engine=MagicMock(), project_store=MagicMock())
    sam._session_resolver = mgr.resolve_session_for_read
    manifest = MagicMock()
    manifest.runtime.transport = transport
    sam._registry.get.return_value = manifest
    return sam


def test_claude_import_resumes_then_falls_back_when_the_native_file_is_gone(env):
    mgr, pid, ws = env["mgr"], env["pid"], env["ws"]
    mgr.materialize_imported_session(pid, CLAUDE_SID)
    sam = _sub_agent_manager(mgr, "sdk")
    rec, status, reason = sam._determine_resume(ws, pid, "claude-code", CLAUDE_SID)
    assert (status, reason) == ("resumed", None)
    assert rec["session_id"] == CLAUDE_ID
    os.remove(env["claude_path"])  # the fixture store, never ~/.claude
    assert sam._determine_resume(ws, pid, "claude-code", CLAUDE_SID) == (
        None, "fresh", "resume_failed")


def test_codex_import_resumes_then_falls_back_when_the_rollout_is_gone(env):
    mgr, pid, ws = env["mgr"], env["pid"], env["ws"]
    mgr.materialize_imported_session(pid, CODEX_SID)
    sam = _sub_agent_manager(mgr, "codex-appserver")
    rec, status, reason = sam._determine_resume(ws, pid, "codex", CODEX_SID)
    assert (status, reason) == ("resumed", None)
    assert rec["rollout_path"] == env["rollout"]
    os.remove(env["rollout"])
    assert sam._determine_resume(ws, pid, "codex", CODEX_SID) == (
        None, "fresh", "resume_failed")


def test_recap_for_a_resumed_import_skips_the_native_rows(env):
    """Resumed: the native thread already holds the imported turns, so the
    recap block is empty. Fresh (resume failed): the whole imported
    conversation is the recap."""
    from agent_os.daemon_v2.recap import build_recap
    mgr, pid = env["mgr"], env["pid"]
    mgr.materialize_imported_session(pid, CLAUDE_SID)
    session = mgr.resolve_session_for_read(pid, CLAUDE_SID)
    msgs = session.get_messages()
    assert build_recap(msgs, "claude-code", fresh=False) == ""
    fresh = build_recap(msgs, "claude-code", fresh=True)
    assert "user: Fix the flaky test" in fresh
    assert "assistant: Looking." in fresh


# --------------------------------------------------------------------------
# I6 — automatic sync call sites
# --------------------------------------------------------------------------


def test_first_list_per_project_imports_automations_once(env, monkeypatch):
    from agent_os.onboarding import import_coordinator as ic
    calls = []
    monkeypatch.setattr(ic.ImportCoordinator, "import_automations",
                        lambda self, pid: calls.append(pid) or {"imported": 0, "skipped": 0})
    mgr, pid = env["mgr"], env["pid"]
    mgr.list_sessions(pid)
    mgr.list_sessions(pid)
    assert calls == [pid]


def test_lazy_sync_failure_never_breaks_the_list(env, monkeypatch):
    from agent_os.onboarding import import_coordinator as ic
    monkeypatch.setattr(ic.ImportCoordinator, "_import_automations",
                        lambda self, pid: (_ for _ in ()).throw(RuntimeError("boom")))
    assert {e["session_id"] for e in env["mgr"].list_sessions(env["pid"])} == {
        CLAUDE_SID, CODEX_SID}


def test_sync_imports_on_the_manager(env):
    result = env["mgr"].sync_imports(env["pid"])
    assert result == {"sessions": 2, "automations": {"imported": 0, "skipped": 0}}


def test_create_project_runs_the_sync(env, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agent_os.api.routes import agents_v2

    mgr = MagicMock()
    mgr._ensure_dispatcher = MagicMock(side_effect=lambda *a, **k: _noop())
    mgr.sync_imports = MagicMock(return_value={"sessions": 2})
    agents_v2.configure(env["store"], mgr, MagicMock())
    app = FastAPI()
    app.include_router(agents_v2.router)
    client = TestClient(app)
    ws2 = tmp_path / "ws2"
    ws2.mkdir()
    resp = client.post("/api/v2/projects", json={"name": "Second", "workspace": str(ws2)})
    assert resp.status_code == 201, resp.text
    mgr.sync_imports.assert_called_once_with(resp.json()["project_id"])


async def _noop():
    return None
