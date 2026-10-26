# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""MiniMax Code (``mcode``, npm @minimax-ai/code) history reader (spec 138).

Layout under ``~/.minimax/v2``:

* ``sessions/YYYY/MM/DD/<time>-session_<base64 id>/`` — ``manifest.json``
  (``sessionId`` ``mvs_…``) and ``messages.jsonl``: one
  ``{message_id, turn_id, message}`` per line, ``message`` being the pi-agent
  AgentMessage mcode runs on (user / assistant / toolResult; assistant
  ``stopReason`` stop, toolUse, error, aborted and ``errorMessage``). A user
  message wraps the person's words in mcode's ``<system-reminder>``
  agent-context block; ``canonicalTextRange`` (JS string offsets) marks the
  words themselves.
* ``sqlite/runtime-state.sqlite`` — ``local_runtime_sessions`` is the ONLY
  record of a session's folder (``workspace_dir``), plus its title, status,
  ``error_message``, archived flag and parent (sub-sessions). A session whose
  first turn failed has an empty messages.jsonl; its words are then only in
  ``local_runtime_message_rows``. Opened read-only (``mode=ro``).

The ``mvs_…`` id is what ACP ``session/load`` resumes by. Sessions Orbital's
mcode worker ran carry no Orbital marker here; ``session_import`` tells them
apart by the ids Orbital's own session records hold.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import threading

from agent_os.onboarding.history_types import CAUSE_INTERRUPTED, HistorySession, StopInfo
from agent_os.onboarding.sources._native import (
    NativeTranscript, error_stop, iso_ms, json_lines, text_parts, to_history,
)

logger = logging.getLogger(__name__)

_LISTED_KINDS = (None, "", "conversation", "unknown")
_ROW_COLUMNS = ("session_id", "workspace_dir", "title", "status", "error_message",
                "archived", "parent_session_id", "session_kind", "created_at_ms",
                "updated_at_ms", "history_relative_dir")

_rows_lock = threading.Lock()
_rows_cache: dict[str, tuple[tuple, dict]] = {}


class MiniMaxCodeSource:
    agent = "minimax-code"

    def root(self, home: str) -> str:
        return os.path.join(home, ".minimax", "v2")

    def session_dirs(self, home: str) -> list[str]:
        base = os.path.join(self.root(home), "sessions")
        out: list[str] = []
        for y in _digit_dirs(base):
            for m in _digit_dirs(y):
                for d in _digit_dirs(m):
                    try:
                        names = sorted(os.listdir(d))
                    except OSError:
                        continue
                    out.extend(os.path.join(d, n) for n in names if "session_" in n)
        return out

    def session_files(self, home: str) -> list[str]:
        return [p for p in (os.path.join(d, "messages.jsonl") for d in self.session_dirs(home))
                if os.path.isfile(p)]

    @staticmethod
    def v2_dir_of(messages_path: str) -> str:
        # v2/sessions/YYYY/MM/DD/<dir>/messages.jsonl
        p = messages_path
        for _ in range(6):
            p = os.path.dirname(p)
        return p

    @staticmethod
    def session_id_of(session_dir: str) -> str | None:
        try:
            with open(os.path.join(session_dir, "manifest.json"), encoding="utf-8") as fh:
                sid = json.load(fh).get("sessionId")
            if isinstance(sid, str) and sid:
                return sid
        except (OSError, ValueError, AttributeError):
            pass
        name = os.path.basename(session_dir)
        if "session_" not in name:
            return None
        b64 = name.split("session_", 1)[1]
        try:
            return base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None

    @staticmethod
    def is_listed(row: dict | None) -> bool:
        if not row:
            return True  # no runtime state: nothing says otherwise
        return (not row.get("archived") and not row.get("parent_session_id")
                and row.get("session_kind") in _LISTED_KINDS)

    # ---- runtime-state sqlite -----------------------------------------------

    def runtime_rows(self, v2_dir: str) -> dict[str, dict]:
        """``session_id -> row`` of ``local_runtime_sessions``, memoized on
        the db + WAL stat. Empty when the db is missing or unexpected."""
        db = os.path.realpath(os.path.join(v2_dir, "sqlite", "runtime-state.sqlite"))
        try:
            st = os.stat(db)
        except OSError:
            return {}
        try:
            wal = os.stat(db + "-wal")
            wal_key = (wal.st_mtime_ns, wal.st_size)
        except OSError:
            wal_key = (0, 0)
        key = (st.st_mtime_ns, st.st_size, *wal_key)
        with _rows_lock:
            cached = _rows_cache.get(db)
            if cached is not None and cached[0] == key:
                return cached[1]
        rows: dict[str, dict] = {}
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
        except sqlite3.Error:
            return {}
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(local_runtime_sessions)")}
            wanted = [c for c in _ROW_COLUMNS if c in cols]
            if "session_id" in wanted:
                for r in con.execute(f"SELECT {', '.join(wanted)} FROM local_runtime_sessions"):
                    row = dict(zip(wanted, r))
                    rows[str(row["session_id"])] = row
        except sqlite3.Error:
            logger.debug("minimax runtime-state unreadable: %s", db, exc_info=True)
            rows = {}
        finally:
            con.close()
        with _rows_lock:
            _rows_cache[db] = (key, rows)
        return rows

    @staticmethod
    def display_rows(v2_dir: str, session_id: str) -> list[dict]:
        """The session's display rows (user / assistant ``msg_content``)."""
        db = os.path.join(v2_dir, "sqlite", "runtime-state.sqlite")
        try:
            con = sqlite3.connect(f"file:{os.path.realpath(db)}?mode=ro", uri=True, timeout=2.0)
        except sqlite3.Error:
            return []
        try:
            out = []
            for role, created, data in con.execute(
                    "SELECT role, created_at_ms, data_json FROM local_runtime_message_rows"
                    " WHERE session_id = ? ORDER BY id", (session_id,)):
                try:
                    d = json.loads(data)
                except (ValueError, TypeError):
                    continue
                if isinstance(d, dict):
                    out.append({"role": role or d.get("role"), "ts": d.get("timestamp") or created,
                                "text": d.get("msg_content")})
            return out
        except sqlite3.Error:
            return []
        finally:
            con.close()

    # ---- parsing -----------------------------------------------------------

    def transcript(self, path: str) -> NativeTranscript | None:
        sdir = os.path.dirname(path)
        sid = self.session_id_of(sdir)
        if not sid:
            return None
        v2 = self.v2_dir_of(path)
        row = self.runtime_rows(v2).get(sid)
        if not self.is_listed(row):
            return None
        row = row or {}
        title = row.get("title") if isinstance(row.get("title"), str) else None
        t = NativeTranscript(
            agent=self.agent, native_id=sid, path=path,
            cwd=row.get("workspace_dir") or None, title=(title or "").strip() or None,
            started_at=iso_ms(row.get("created_at_ms")))
        try:
            for rec in json_lines(path):
                if isinstance(rec.get("message"), dict):
                    self._message(t, rec["message"])
        except OSError:
            return None
        if not t.turns and row:
            # Failed before anything reached messages.jsonl.
            for d in self.display_rows(v2, sid):
                if d["role"] == "user":
                    t.new_turn(d["text"] if isinstance(d["text"], str) else "", iso_ms(d["ts"]))
                elif d["role"] == "assistant" and t.turns:
                    t.turns[-1].add_text(d["text"] if isinstance(d["text"], str) else "",
                                         iso_ms(d["ts"]))
        if t.turns and row.get("status") == "error":
            last = t.turns[-1]
            if last.stop is None and not last.has_output():
                last.stop = error_stop(row.get("error_message"),
                                       iso_ms(row.get("updated_at_ms")))
        return t

    @staticmethod
    def _message(t: NativeTranscript, msg: dict) -> None:
        role = msg.get("role")
        ts = iso_ms(msg.get("timestamp"))
        if role == "user":
            t.new_turn(_canonical_text(msg), ts)
        elif role == "assistant":
            turn = t.open_turn()
            for part in msg.get("content") if isinstance(msg.get("content"), list) else []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    turn.add_text(part.get("text"), ts)
                elif part.get("type") == "toolCall":
                    t.tool_call(part.get("name"), part.get("arguments"), ts)
            reason = msg.get("stopReason")
            turn.stop = (error_stop(msg.get("errorMessage"), ts) if reason == "error"
                         else StopInfo(CAUSE_INTERRUPTED) if reason == "aborted" else None)

    def read(self, path: str) -> HistorySession | None:
        try:
            t = self.transcript(path)
            return to_history(t) if t is not None else None
        except Exception:  # never raises (contract): a bad file is skipped
            return None


def _canonical_text(msg: dict) -> str:
    """The person's words: the ``canonicalTextRange`` slice of the text when
    mcode recorded one (offsets are JS UTF-16 units), else the whole text."""
    text = text_parts(msg.get("content"))
    rng = msg.get("canonicalTextRange")
    if isinstance(rng, dict):
        start, end = rng.get("startOffset"), rng.get("endOffset")
        if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end:
            units = text.encode("utf-16-le")
            if end * 2 <= len(units):
                return units[start * 2:end * 2].decode("utf-16-le", errors="ignore")
    return text


def _digit_dirs(base: str) -> list[str]:
    try:
        return [os.path.join(base, n) for n in sorted(os.listdir(base)) if n.isdigit()]
    except OSError:
        return []


SOURCE = MiniMaxCodeSource()
