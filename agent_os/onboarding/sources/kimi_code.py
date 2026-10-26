# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Kimi Code (``kimi``, npm @moonshot-ai/kimi-code 2.x) history reader (spec 138).

Layout (``~/.kimi-code``, or ``$KIMI_CODE_HOME`` for the real home):
``sessions/wd_<folder>_<hash>/session_<uuid>/`` holds ``state.json`` (id, cwd,
createdAt, title, archived, ``custom.child_session_kind`` for sub-sessions)
and ``agents/main/wire.jsonl`` — the main agent's durable event log. The
conversation is its ``context.append_message`` records (``message.role``
user / assistant / tool; a user message's ``origin.kind`` is ``user`` for the
person and ``injection`` / ``retry`` / ``compaction_summary`` for kimi's own),
and each turn closes with ``turn.ended`` (reason completed / cancelled /
failed / blocked, ``error.message``).

The session id (``session_<uuid>``) is what ACP ``session/load`` resumes by,
and what Orbital's ACP transport records. Sessions Orbital's kimi worker ran
are not marked as such here; ``session_import`` tells them apart by the ids
Orbital's own session records hold.
"""

from __future__ import annotations

import json
import os

from agent_os.onboarding.history_types import CAUSE_INTERRUPTED, HistorySession, StopInfo
from agent_os.onboarding.sources._native import (
    NativeTranscript, error_stop, is_default_home, iso_ms, json_lines, text_parts,
    to_history,
)

_PERSON_ORIGINS = (None, "user")


class KimiCodeSource:
    agent = "kimi-code"

    def root(self, home: str) -> str:
        env = os.environ.get("KIMI_CODE_HOME") if is_default_home(home) else None
        return os.path.expanduser(env) if env else os.path.join(home, ".kimi-code")

    def session_dirs(self, home: str) -> list[str]:
        base = os.path.join(self.root(home), "sessions")
        out: list[str] = []
        try:
            workspaces = sorted(n for n in os.listdir(base) if not n.startswith("."))
        except OSError:
            return out
        for ws in workspaces:
            try:
                names = sorted(os.listdir(os.path.join(base, ws)))
            except OSError:
                continue
            out.extend(os.path.join(base, ws, n) for n in names if n.startswith("session_"))
        return out

    def session_files(self, home: str) -> list[str]:
        return [p for p in (os.path.join(d, "agents", "main", "wire.jsonl")
                            for d in self.session_dirs(home)) if os.path.isfile(p)]

    @staticmethod
    def session_dir_of(wire_path: str) -> str:
        return os.path.dirname(os.path.dirname(os.path.dirname(wire_path)))

    @staticmethod
    def state(session_dir: str) -> dict:
        try:
            with open(os.path.join(session_dir, "state.json"), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def is_listed(state: dict) -> bool:
        """Not archived, not a kimi sub-agent's child session."""
        custom = state.get("custom") if isinstance(state.get("custom"), dict) else {}
        return not state.get("archived") and custom.get("child_session_kind") != "child"

    def transcript(self, path: str) -> NativeTranscript | None:
        sdir = self.session_dir_of(path)
        st = self.state(sdir)
        if not self.is_listed(st):
            return None
        native_id = st.get("id") if isinstance(st.get("id"), str) else os.path.basename(sdir)
        title = st.get("title") if isinstance(st.get("title"), str) else None
        t = NativeTranscript(
            agent=self.agent, native_id=native_id, path=path,
            cwd=st.get("cwd") if isinstance(st.get("cwd"), str) else None,
            title=title.strip() or None if title else None,
            started_at=iso_ms(st.get("createdAt")))
        try:
            for rec in json_lines(path):
                rtype = rec.get("type")
                ts = iso_ms(rec.get("time"))
                if rtype == "context.append_message" and isinstance(rec.get("message"), dict):
                    self._message(t, rec["message"], ts)
                elif rtype == "turn.ended" and t.turns:
                    t.turns[-1].stop = self._turn_end(rec, ts)
        except OSError:
            return None
        return t

    @staticmethod
    def _message(t: NativeTranscript, msg: dict, ts: str | None) -> None:
        role = msg.get("role")
        if role == "user":
            origin = msg.get("origin") if isinstance(msg.get("origin"), dict) else {}
            if origin.get("kind") in _PERSON_ORIGINS:
                t.new_turn(text_parts(msg.get("content")), ts)
        elif role == "assistant":
            turn = t.open_turn()
            turn.add_text(text_parts(msg.get("content")), ts)
            for call in msg.get("toolCalls") if isinstance(msg.get("toolCalls"), list) else []:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") if isinstance(call.get("function"), dict) else call
                t.tool_call(fn.get("name"), fn.get("arguments"), ts)
        # tool results and system messages: not the conversation

    @staticmethod
    def _turn_end(rec: dict, ts: str | None) -> StopInfo | None:
        reason = rec.get("reason")
        if reason == "completed":
            return None
        if reason == "cancelled":
            return StopInfo(CAUSE_INTERRUPTED)
        err = rec.get("error")
        text = err.get("message") if isinstance(err, dict) else err if isinstance(err, str) else None
        return error_stop(text or reason, ts)

    def read(self, path: str) -> HistorySession | None:
        try:
            t = self.transcript(path)
            return to_history(t) if t is not None else None
        except Exception:  # never raises (contract): a bad file is skipped
            return None


SOURCE = KimiCodeSource()
