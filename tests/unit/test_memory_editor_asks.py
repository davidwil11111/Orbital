# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 089 v2 §6 under spec 124 §3.7: the Memory session may close an ask
only with the user's words.

The editor's ``close_asks`` JSON field is gone; the Memory session edits
``orbital/ASKS.md`` itself. Two things keep the rule: the task message
states it, and the ASKS write chokepoint (``asks.process_write``, reached
through the write tool like every other ``orbital/*.md`` write) appends the
close stamped ``by:agent`` and warns when no user words are quoted.
"""

from __future__ import annotations

import json
import os

import pytest

from agent_os.agent import asks
from agent_os.agent import memory_editor as E
from agent_os.agent.project_paths import ProjectPaths
from agent_os.agent.tools.write import WriteTool
from agent_os.agent.workspace_files import WorkspaceFileManager

TODAY = "2026-09-19"
ANSWER = "I talked to my cofounder: the webinar will be in English, final."


@pytest.fixture(autouse=True)
def _clear_single_flight():
    E._RUNNING.clear()
    yield
    E._RUNNING.clear()


@pytest.fixture
def ws(tmp_path):
    workspace = str(tmp_path)
    wf = WorkspaceFileManager(workspace)
    wf.write("state", "- Webinar agenda drafted.\n")
    orbital = wf.dir
    ask = asks.open_ask(orbital, "Decide the webinar language (EN or ZH)", today="2026-09-18")
    sessions = ProjectPaths(workspace).sessions_dir
    os.makedirs(sessions, exist_ok=True)
    rows = [
        {"role": "meta", "type": "session_start"},
        {"role": "user", "content": ANSWER, "source": "user"},
        {"role": "assistant", "content": "Noted — English it is, and I will ship it today.",
         "source": "management"},
    ]
    with open(os.path.join(sessions, "proj_aaaa1111.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return workspace, orbital, ask.id


def _state(orbital, ask_id):
    return {a.id: a for a in asks.read_asks(orbital)}[ask_id]


def test_task_message_states_the_quote_rule():
    text = E.build_task_message(
        sizes={k: (1, 10, 5) for k in E.LIVE_KEYS}, folded=[], over=["state"], forced=False,
        pointer_paths=[], last_pass=None, sessions=[], today=TODAY, tool_cap=12,
    )
    assert ("- Close an ask in orbital/ASKS.md only with the user's own words quoted, as\n"
            "  that file's header says.\n") in text


def test_the_memory_session_closes_an_ask_through_the_write_tool(ws):
    workspace, orbital, ask_id = ws
    assert E.memory_tool_guard(workspace)({"name": "write", "arguments": {"path": "orbital/ASKS.md"}}) is None
    current = open(os.path.join(orbital, "ASKS.md"), encoding="utf-8").read()
    out = WriteTool(workspace).execute(
        path="orbital/ASKS.md",
        content=current + f'- done {ask_id} "the webinar will be in English, final"\n',
    )
    assert '"status": "success"' in out.content
    ask = _state(orbital, ask_id)
    assert ask.state == "done" and ask.closed_by == "agent"
    assert '"the webinar will be in English, final"' in ask.note


def test_a_close_without_the_users_words_is_warned_about(ws):
    workspace, orbital, ask_id = ws
    current = open(os.path.join(orbital, "ASKS.md"), encoding="utf-8").read()
    out = WriteTool(workspace).execute(path="orbital/ASKS.md", content=current + f"- done {ask_id} settled\n")
    assert "without quoting the user" in out.content


def test_the_append_only_guard_restores_a_dropped_open_line(ws):
    workspace, orbital, ask_id = ws
    out = WriteTool(workspace).execute(path="orbital/ASKS.md", content="# ASKS\n")
    assert '"status": "success"' in out.content
    assert _state(orbital, ask_id).state == "open"


# ---------------------------------------------------------------------------
# follow-up (coordinator 2026-10-21): a cited session verifies the quote
# ---------------------------------------------------------------------------

def _asks_text(orbital):
    return open(os.path.join(orbital, "ASKS.md"), encoding="utf-8").read()


def test_close_citing_a_session_with_the_users_real_words_is_applied(ws):
    workspace, orbital, ask_id = ws
    out = WriteTool(workspace).execute(
        path="orbital/ASKS.md",
        content=_asks_text(orbital)
        + f'- done {ask_id} session:proj_aaaa1111 "the webinar  will be   in English, final"\n',
    )
    assert '"status": "success"' in out.content and "not closed" not in out.content
    ask = _state(orbital, ask_id)
    assert ask.state == "done" and ask.closed_by == "agent"
    assert "session:proj_aaaa1111" in ask.note


@pytest.mark.parametrize("session, quote, why", [
    ("proj_aaaa1111", "English it is, and I will ship it today", "the assistant's words"),
    ("proj_aaaa1111", "the webinar will be in Spanish", "fabricated"),
    ("proj_bbbb2222", "the webinar will be in English", "another session"),
    ("proj_missing", "the webinar will be in English", "no such session"),
    ("../proj_aaaa1111", "the webinar will be in English", "path escape"),
])
def test_close_citing_a_session_whose_user_rows_lack_the_quote_is_dropped(ws, session, quote, why):
    workspace, orbital, ask_id = ws
    before = _asks_text(orbital)
    out = WriteTool(workspace).execute(
        path="orbital/ASKS.md",
        content=before + f'- done {ask_id} session:{session} "{quote}"\n',
    )
    assert '"status": "success"' in out.content, why
    assert f"ask {ask_id} not closed: the quoted words are not in session {session}" in out.content, why
    assert _state(orbital, ask_id).state == "open", why
    assert _asks_text(orbital) == before, why           # the file is otherwise intact


def test_close_without_a_cited_session_keeps_the_old_behaviour(ws):
    workspace, orbital, ask_id = ws
    out = WriteTool(workspace).execute(
        path="orbital/ASKS.md",
        content=_asks_text(orbital) + f'- done {ask_id} "made-up words"\n',
    )
    assert '"status": "success"' in out.content and "not closed" not in out.content
    assert _state(orbital, ask_id).state == "done"       # applied, as before
    out = WriteTool(workspace).execute(
        path="orbital/ASKS.md", content=_asks_text(orbital) + "- open Pick a venue\n- done 000000 no quote\n")
    assert "without quoting the user" not in out.content   # unknown id: unchanged "no ask with id" path


def test_the_asks_header_documents_the_session_citation(ws):
    workspace, orbital, _ = ws
    assert "session:<session_uuid>" in asks.FORMAT_HEADER
    assert _asks_text(orbital).startswith(asks.FORMAT_HEADER)
