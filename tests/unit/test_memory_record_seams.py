# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 112 A6 (7b): the reply to the user is fixed at the moment the run
would have ended. Rows produced by the record turn carry
``_kind: "memory_record"`` and every reader of "the last reply" ignores them:
the shared-chat recap a worker receives, the session list's reply badge
(``is_reply_row``), and the live activity broadcast.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from agent_os.daemon_v2 import session_index as si
from agent_os.daemon_v2.agent_manager import AgentManager
from agent_os.daemon_v2.recap import build_recap


def _assistant(content, **extra):
    return {"role": "assistant", "content": content, "source": "management", **extra}


def _user(content, **extra):
    return {"role": "user", "content": content, "source": "user", **extra}


def _memory_row(trigger="run_end"):
    return {"role": "system", "source": "daemon",
            "content": "[MEMORY] This run is ending. Decide whether anything durable happened in it.",
            "_meta": {"event": "memory_record", "trigger": trigger},
            "_kind": "memory_record"}


def _completed(handle, reply, **extra):
    display = f"[Sub-agent] {handle} completed. Summary: {reply[:500]}. Transcript: /tmp/{handle}.jsonl."
    return {"role": "system", "source": "daemon", "content": display + " (guidance)",
            "_meta": {"event": "sub_agent_terminal", "kind": "completed",
                      "display_content": display, "reply": reply, "handle": handle},
            **extra}


# ---------------------------------------------------------------------------
# Reply badge
# ---------------------------------------------------------------------------


def test_record_turn_rows_are_never_reply_rows():
    assert si.is_reply_row(_assistant("Done, updated state.", _kind="memory_record")) is False
    assert si.is_reply_row(_memory_row()) is False
    assert si.is_reply_row(_completed("codex", "nothing to record", _kind="memory_record")) is False
    # The untagged reply before the [MEMORY] row still is.
    assert si.is_reply_row(_assistant("Here is the plan.")) is True


def test_last_reply_at_is_the_reply_before_the_memory_row(tmp_path):
    sessions = tmp_path / "ws" / "orbital" / "sessions"
    sessions.mkdir(parents=True)
    rows = [
        {"role": "meta", "event": "session_start"},
        {**_user("go"), "timestamp": "2026-10-20T10:00:00+00:00"},
        {**_assistant("Here is the plan."), "timestamp": "2026-10-20T10:00:05+00:00"},
        {**_memory_row(), "timestamp": "2026-10-20T10:00:06+00:00"},
        {**_assistant("Done, updated state.", _kind="memory_record"),
         "timestamp": "2026-10-20T10:00:09+00:00"},
    ]
    import json
    p = sessions / "p_rec0001.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    entry = si.derive_disk_entry(str(p), "p_rec0001")
    assert entry["last_reply_at"] == "2026-10-20T10:00:05+00:00"


# ---------------------------------------------------------------------------
# Shared-chat recap (spec 084)
# ---------------------------------------------------------------------------


def test_recap_skips_every_memory_record_row():
    messages = [
        _user("plan the launch"),
        _assistant("Here is the plan."),
        _memory_row(),
        _assistant("Done, updated state.", _kind="memory_record"),
        # A pinned worker's own record turn: the reminder row, its marker-free
        # completion, both tagged.
        _user("[Orbital memory] Your last turn is ending.", target="codex",
              source="daemon", _kind="memory_record"),
        _completed("codex", "nothing to record", _kind="memory_record"),
        _user("now the copy"),
    ]
    block = build_recap(messages, "claude-code", fresh=True)
    assert "user: plan the launch" in block
    assert "assistant: Here is the plan." in block
    assert "user: now the copy" in block
    assert "Done, updated state." not in block
    assert "[MEMORY]" not in block
    assert "Orbital memory" not in block
    assert "nothing to record" not in block


# ---------------------------------------------------------------------------
# Live broadcast: nothing from the record turn reaches the activity translator
# ---------------------------------------------------------------------------


def _manager():
    return AgentManager(
        project_store=MagicMock(), ws_manager=MagicMock(),
        sub_agent_manager=MagicMock(), activity_translator=MagicMock(),
        process_manager=MagicMock(),
    )


def test_on_message_drops_memory_record_rows():
    mgr = _manager()
    cb = mgr._on_message("proj", "sess")
    cb(_assistant("Here is the plan."))
    cb(_memory_row())
    cb(_assistant(None, _kind="memory_record",
                  tool_calls=[{"id": "t1", "type": "function",
                               "function": {"name": "edit", "arguments": "{}"}}]))
    cb({"role": "tool", "content": "Edited", "tool_call_id": "t1",
        "source": "management", "_kind": "memory_record"})
    cb(_assistant("Done, updated state.", _kind="memory_record"))
    calls = mgr._activity_translator.on_message.call_args_list
    assert len(calls) == 1
    assert calls[0].args[0]["content"] == "Here is the plan."
