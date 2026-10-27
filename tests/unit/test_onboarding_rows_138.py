# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 138 step 2 folder rows (rc DMG, 2026-10-27: 42 rows on a dev Mac).

- The rows came from the legacy folder scanner, which counts the sessions
  Orbital itself ran as a worker. Once the history scan is ready they come
  from its analysis, which drops those (``is_noise``) and agrees with the
  groups.
- Orbital seeds AGENTS.md into every project folder it creates, so any folder
  Orbital was ever pointed at passed the "has AGENTS.md" rule. Orbital's own
  seed no longer counts.
- The Codex app makes one folder per chat under ``~/Documents/Codex/<date>/``.
  Those are not projects unless they are git repos; their sessions go on to
  the files-touched and grouping passes.
"""

import json
import os
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_os.api.routes import agents_v2
from agent_os.api.routes import onboarding as onboarding_routes
from agent_os.daemon_v2 import agent_md_seeder
from agent_os.daemon_v2.project_store import ProjectStore
from agent_os.onboarding import history as h
from agent_os.onboarding import import_scanner
from agent_os.onboarding.history import HistoryScanner, clear_git_cache
from agent_os.onboarding.history_paths import HistoryEnv, has_project_instructions
from agent_os.onboarding.history_types import HistorySession
from agent_os.onboarding.sources import claude_code


def _seed_text() -> str:
    return agent_md_seeder.AGENT_MD_TEMPLATE.format_map(
        {"project_name": "smoke", "agent_name": "Orbital"})


def sess(native_id, cwd, *, first="please build the thing", turns=2, **kw):
    return HistorySession(agent="claude-code", native_id=native_id, path=f"/h/{native_id}",
                          cwd=cwd, first_user_text=first, last_user_text=first,
                          user_turns=turns, started_at="2026-10-20T10:00:00+00:00",
                          ended_at="2026-10-20T10:00:00+00:00", **kw)


# ---------------------------------------------------------------------------
# Orbital's own AGENTS.md seed is not a project marker
# ---------------------------------------------------------------------------

def test_the_seed_marker_is_in_every_template_orbital_wrote():
    assert agent_md_seeder.SEED_MARKER in agent_md_seeder.AGENT_MD_TEMPLATE
    assert agent_md_seeder.SEED_MARKER in agent_md_seeder._TEMPLATE_PRE_ASKS


def test_project_instructions(tmp_path):
    none, claude, user_md, seeded = (tmp_path / n for n in ("none", "claude", "user", "seeded"))
    for d in (none, claude, user_md, seeded):
        d.mkdir()
    (claude / "CLAUDE.md").write_text("# rules")
    (user_md / "AGENTS.md").write_text("# How to work here\nRun the tests.\n")
    (seeded / "AGENTS.md").write_text(_seed_text())
    assert not has_project_instructions(str(none))
    assert has_project_instructions(str(claude))
    assert has_project_instructions(str(user_md))
    assert not has_project_instructions(str(seeded))


def test_a_seeded_folder_with_one_session_is_minor(tmp_path):
    home = tmp_path / "home"
    ws = home / "ws-smoke"
    ws.mkdir(parents=True)
    (ws / "AGENTS.md").write_text(_seed_text())
    env = HistoryEnv.for_home(str(home), temp_roots=("/definitely-not-here",))
    a = h.analyze([sess("s1", str(ws))], env)
    assert a.projects[str(ws)].minor is True


def test_the_legacy_scanner_ignores_the_seed_too(tmp_path):
    home = tmp_path / "home"
    ws = home / "ws-smoke"
    ws.mkdir(parents=True)
    (ws / "AGENTS.md").write_text(_seed_text())
    cand = import_scanner.ImportCandidate(source="claude-code", name="ws-smoke", path=str(ws),
                                          session_count=1, sources=["claude-code"])
    import_scanner._annotate(cand, HistoryEnv.for_home(str(home), temp_roots=("/nope",)))
    assert cand.has_agent_md is False and cand.minor is True


# ---------------------------------------------------------------------------
# Codex app chat folders
# ---------------------------------------------------------------------------

@pytest.fixture
def codex_world(tmp_path):
    home = tmp_path / "home"
    chats = home / "Documents" / "Codex" / "2026-10-22"
    for d in ("help", "built", "wrote"):
        (chats / d).mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(chats / "built")], check=True)
    clear_git_cache()
    env = HistoryEnv.for_home(str(home), temp_roots=("/definitely-not-here",))
    return home, chats, env


def test_codex_chat_folders_are_recognised(codex_world):
    home, chats, env = codex_world
    assert env.is_chat_folder(str(chats / "help"))
    assert not env.is_chat_folder(str(home / "Documents" / "Codex"))
    assert not env.is_chat_folder(str(home / "Documents" / "notes"))


def test_a_codex_chat_folder_is_not_a_project_unless_it_is_a_repo(codex_world):
    home, chats, env = codex_world
    sessions = [sess(f"h{i}", str(chats / "help"), first=f"question number {i} about life")
                for i in range(4)]
    sessions.append(sess("b1", str(chats / "built")))
    a = h.analyze(sessions, env)
    assert set(a.projects) == {str(chats / "built")}
    # The chat folder's sessions went on to grouping instead of being dropped.
    grouped = {s.native_id for g in a.groups for s in g.members}
    assert {"h0", "h1", "h2", "h3"} <= grouped


def test_the_legacy_scanner_drops_non_repo_codex_chat_folders(codex_world):
    home, chats, env = codex_world
    keep = import_scanner._fold_to_repo(
        import_scanner.ImportCandidate(source="codex", name="built", path=str(chats / "built"),
                                       session_count=1, sources=["codex"]), env)
    drop = import_scanner._fold_to_repo(
        import_scanner.ImportCandidate(source="codex", name="help", path=str(chats / "help"),
                                       session_count=9, sources=["codex"]), env)
    assert keep is not None and drop is None


# ---------------------------------------------------------------------------
# Rows come from the analysis once the scan is ready
# ---------------------------------------------------------------------------

def _transcript(path, sid, cwd, first, *, entrypoint="cli"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rows = [{"type": "user", "sessionId": sid, "cwd": cwd, "timestamp": "2026-10-20T10:00:00Z",
             "entrypoint": entrypoint, "message": {"role": "user", "content": first}},
            {"type": "assistant", "sessionId": sid, "cwd": cwd, "timestamp": "2026-10-20T10:00:05Z",
             "message": {"model": "m", "content": [{"type": "text", "text": "done"}]}}]
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo, worker_ws, imported = home / "code" / "app", home / "run" / "ws-smoke", home / "code" / "old"
    for d in (home / "Desktop", repo, worker_ws, imported):
        d.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (worker_ws / "AGENTS.md").write_text(_seed_text())
    clear_git_cache()
    projects = home / ".claude" / "projects"
    _transcript(projects / "app" / "a1.jsonl", "a1", str(repo), "add the settings page")
    # Orbital ran this one as a worker (sdk entrypoint): noise, never a row.
    _transcript(projects / "ws" / "w1.jsonl", "w1", str(worker_ws), "Reply with the word PONG now",
                entrypoint="sdk-py")
    _transcript(projects / "old" / "o1.jsonl", "o1", str(imported), "refactor the old module")
    monkeypatch.setenv("ORBITAL_HISTORY_HOME", str(home))
    monkeypatch.delenv("ORBITAL_CODEX_HOME", raising=False)

    store = ProjectStore(str(tmp_path / "data"))
    store.create_project({"name": "old", "workspace": str(imported)})
    agents_v2.configure(project_store=store, agent_manager=MagicMock(),
                        ws_manager=MagicMock(), settings_store=MagicMock())
    scanner = HistoryScanner(str(tmp_path / "data"), home=str(home),
                             sources=[claude_code.SOURCE], env=HistoryEnv.for_home(str(home)))
    onboarding_routes.configure(project_store=store, scanner=scanner)
    app = FastAPI()
    app.include_router(onboarding_routes.router)
    yield SimpleNamespace(client=TestClient(app), home=home, repo=repo, worker_ws=worker_ws,
                          imported=imported)
    onboarding_routes.configure()


def _ready(client):
    deadline = time.time() + 10
    while time.time() < deadline:
        if client.get("/api/v2/onboarding/history").json()["status"] == "ready":
            return
        time.sleep(0.05)
    raise AssertionError("scan never finished")


def test_ready_rows_come_from_the_analysis(app_env):
    _ready(app_env.client)
    body = app_env.client.get("/api/v2/onboarding/importable-projects").json()
    rows = {c["path"]: c for c in body["candidates"]}
    assert str(app_env.worker_ws) not in rows        # only Orbital's own worker ran there
    app_row = rows[str(app_env.repo)]
    assert app_row["is_git"] and not app_row["minor"] and app_row["kind"] == "folder"
    assert app_row["by_agent"] == {"claude-code": 1} and app_row["session_count"] == 1
    assert app_row["source"] == "claude-code" and app_row["last_activity"]
    assert rows[str(app_env.imported)]["already_imported"] is True
