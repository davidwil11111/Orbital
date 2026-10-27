# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Synthetic pi / Kimi Code / MiniMax Code stores for the spec 138 tests.

Each ``install_*`` copies a template from ``tests/fixtures/history_138/``
into a fake home at the agent's real location, substituting ``{CWD}``,
``{ID}`` and ``{DIR}``, and returns the session file the reader keys on.
``extra`` rows are appended after the template (stop-cause variants).
Invented content only.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3

FIXTURES = os.path.abspath(os.path.join(
    os.path.dirname(__file__), os.pardir, "fixtures", "history_138"))

def native(path: str) -> str:
    """A POSIX fixture path as an absolute path on this host. Python 3.13 on
    Windows no longer calls ``/Users/x`` absolute (no drive), so the readers
    rightly drop it there; real Windows histories record ``C:\\…``."""
    return "C:" + path if os.name == "nt" else path


def norm(path: str) -> str:
    """How the readers store a path: normalised (backslashes on Windows)."""
    return os.path.normpath(path)


PI_ID = "0199aaaa-1111-7222-8333-444455556666"
KIMI_ID = "session_7a1b2c3d-0000-4000-8000-00000000abcd"
MINIMAX_ID = "mvs_5e6f7a8b9c0d4e1f8a2b3c4d5e6f7a8b"


def _sub(text: str, **values: str) -> str:
    for key, value in values.items():
        # JSON-escaped, so a Windows path's backslashes stay valid JSON.
        text = text.replace("{" + key + "}", json.dumps(value)[1:-1])
    return text


def _template(name: str, **values: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return _sub(fh.read(), **values)


def _write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _rows(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _dump(rows: list[dict]) -> str:
    return "".join(json.dumps(r) + "\n" for r in rows)


# ---- pi ---------------------------------------------------------------------


def pi_encoded_dir(cwd: str) -> str:
    safe = cwd.lstrip("/\\")
    for ch in "/\\:":
        safe = safe.replace(ch, "-")
    return f"--{safe}--"


def install_pi(home: str, cwd: str, *, sid: str = PI_ID, rows: list[dict] | None = None,
               extra: list[dict] | None = None, stamp: str = "2026-10-20T10-00-00-000Z") -> str:
    """A pi session file under ``home/.pi/agent/sessions/--<cwd>--/``.
    ``rows`` replaces the template body (the header stays)."""
    template = _rows(_template("pi_session.jsonl", ID=sid, CWD=cwd))
    body = template if rows is None else [template[0], *rows]
    path = os.path.join(home, ".pi", "agent", "sessions", pi_encoded_dir(cwd),
                        f"{stamp}_{sid}.jsonl")
    return _write(path, _dump([*body, *(extra or [])]))


def pi_message(role: str, content, *, ts: str = "2026-10-20T10:01:00.000Z", **fields) -> dict:
    return {"type": "message", "id": os.urandom(4).hex(), "parentId": None, "timestamp": ts,
            "message": {"role": role, "content": content, **fields}}


# ---- Kimi Code --------------------------------------------------------------


def install_kimi(home: str, cwd: str, *, sid: str = KIMI_ID, wire: list[dict] | None = None,
                 extra: list[dict] | None = None, state: dict | None = None) -> str:
    """``home/.kimi-code/sessions/wd_<name>_<hash>/<sid>/agents/main/wire.jsonl``
    plus its ``state.json`` and a ``session_index.jsonl`` line."""
    root = os.path.join(home, ".kimi-code")
    name = os.path.basename(cwd.rstrip("/\\")) or "root"
    sdir = os.path.join(root, "sessions", f"wd_{name}_0123456789ab", sid)
    st = json.loads(_template("kimi_state.json", ID=sid, CWD=cwd, DIR=sdir))
    st.update(state or {})
    _write(os.path.join(sdir, "state.json"), json.dumps(st))
    body = (_rows(_template("kimi_wire.jsonl", CWD=cwd)) if wire is None
            else [{"type": "metadata", "protocol_version": "1.5", "created_at": 1792490400000},
                  *wire])
    wire_path = _write(os.path.join(sdir, "agents", "main", "wire.jsonl"),
                       _dump([*body, *(extra or [])]))
    with open(os.path.join(root, "session_index.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"sessionId": sid, "sessionDir": sdir, "workDir": cwd}) + "\n")
    return wire_path


def kimi_message(role: str, text: str | None = None, *, time: int = 1792490500000,
                 origin: str | None = None, tool_calls: list | None = None) -> dict:
    msg = {"role": role, "content": [{"type": "text", "text": text}] if text is not None else [],
           "toolCalls": tool_calls or []}
    if origin:
        msg["origin"] = {"kind": origin}
    return {"type": "context.append_message", "agentId": "main", "message": msg, "time": time}


def kimi_turn_ended(reason: str, *, error: dict | None = None, time: int = 1792490600000) -> dict:
    rec = {"type": "turn.ended", "agentId": "main", "turnId": 1, "reason": reason, "time": time}
    if error:
        rec["error"] = error
    return rec


# ---- MiniMax Code -------------------------------------------------------------


def minimax_session_dir(home: str, sid: str = MINIMAX_ID,
                        day: str = "2026/10/20", stamp: str = "10-00-00-000") -> str:
    b64 = base64.urlsafe_b64encode(sid.encode()).decode().rstrip("=")
    return os.path.join(home, ".minimax", "v2", "sessions", *day.split("/"),
                        f"{stamp}-session_{b64}")


def _with_canonical_range(row: dict) -> dict:
    """mcode stamps where the user's own words sit inside the wrapped text."""
    msg = row.get("message") or {}
    if msg.get("role") == "user":
        text = msg["content"][0]["text"]
        marker = "</system-reminder>\n\n"
        start = text.find(marker)
        start = start + len(marker) if start >= 0 else 0
        units = lambda s: len(s.encode("utf-16-le")) // 2  # JS string offsets
        msg["canonicalTextRange"] = {"startOffset": units(text[:start]),
                                     "endOffset": units(text)}
    return row


def install_minimax(home: str, cwd: str, *, sid: str = MINIMAX_ID,
                    messages: list[dict] | None = None, extra: list[dict] | None = None,
                    status: str = "idle", error_message: str | None = None,
                    title: str | None = "Summarise README", archived: int = 0,
                    parent: str | None = None, kind: str = "conversation",
                    display_rows: list[dict] | None = None, day: str = "2026/10/20",
                    updated_ms: int = 1792490404000) -> str:
    """A dated session dir (manifest + messages.jsonl) and its
    ``runtime-state.sqlite`` row — the only place mcode records the cwd."""
    sdir = minimax_session_dir(home, sid, day)
    _write(os.path.join(sdir, "manifest.json"),
           _template("minimax_manifest.json", ID=sid, DIR=sdir))
    body = (_rows(_template("minimax_messages.jsonl", ID=sid, CWD=cwd))
            if messages is None else messages)
    msgs_path = _write(os.path.join(sdir, "messages.jsonl"),
                       _dump([_with_canonical_range(r) for r in [*body, *(extra or [])]]))
    db = os.path.join(home, ".minimax", "v2", "sqlite", "runtime-state.sqlite")
    os.makedirs(os.path.dirname(db), exist_ok=True)
    fresh = not os.path.exists(db)
    con = sqlite3.connect(db)
    try:
        if fresh:
            with open(os.path.join(FIXTURES, "minimax_sqlite_schema.sql"), encoding="utf-8") as fh:
                con.executescript(fh.read())
        rel = os.path.relpath(sdir, os.path.join(home, ".minimax", "v2", "sessions"))
        con.execute(
            "INSERT OR REPLACE INTO local_runtime_sessions (session_id, record_json, updated_at_ms,"
            " agent_name, runtime, status, archived, session_kind, parent_session_id,"
            " workspace_dir, project_workspace_dir, title, created_at_ms, error_message,"
            " history_relative_dir) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, json.dumps({"sessionId": sid}), updated_ms, "mavis", "pi-agent", status,
             archived, kind, parent, cwd, cwd, title, 1792490400000, error_message,
             rel.replace(os.sep, "/")))
        for i, row in enumerate(display_rows or []):
            con.execute(
                "INSERT INTO local_runtime_message_rows (session_id, msg_id, role,"
                " created_at_ms, data_json, source) VALUES (?,?,?,?,?,?)",
                (sid, f"m{i}", row.get("role"), row.get("timestamp", 1792490401000),
                 json.dumps(row), "api"))
        con.commit()
    finally:
        con.close()
    return msgs_path


def minimax_assistant(content, stop: str, *, error: str | None = None,
                      ts: int = 1792490500000) -> dict:
    msg = {"role": "assistant", "content": content, "stopReason": stop, "timestamp": ts,
           "provider": "minimax", "model": "MiniMax-M3"}
    if error:
        msg["errorMessage"] = error
    return {"message_id": os.urandom(4).hex(), "turn_id": "turn-x", "message": msg}


def minimax_user(text: str, *, ts: int = 1792490450000) -> dict:
    return {"message_id": "msg-user-v1-" + os.urandom(4).hex(), "turn_id": "turn-x",
            "message": {"role": "user", "content": [{"type": "text", "text": text}],
                        "timestamp": ts}}
