# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pi (Earendil's pi coding agent) history reader (spec 138).

Pi saves each session as one JSONL file, by default
``~/.pi/agent/sessions/--<cwd with / as ->--/<timestamp>_<id>.jsonl``
(pi 1.4 docs/session-format.md). The first line is the header
``{"type":"session","version":3,"id","timestamp","cwd"}``; ``message`` entries
carry pi's AgentMessage (user / assistant / toolResult; an assistant message
records ``stopReason`` — stop, toolUse, length, error, aborted — and
``errorMessage``); ``session_info`` holds the user-given name.

Relocation, as pi resolves it: ``PI_CODING_AGENT_DIR`` moves the agent dir,
``PI_CODING_AGENT_SESSION_DIR`` or ``sessionDir`` in the agent dir's
``settings.json`` move the sessions (flat, no per-cwd folders). The
variables only apply when reading the user's real home.

Pi resumes by session id or by the file itself (``pi --session <file>``), so
``resume_path`` is the file. Sessions Orbital's pi worker runs live in
Orbital's own ``--session-dir`` inside the project, never here, so nothing
read here is Orbital's own.
"""

from __future__ import annotations

import json
import os

from agent_os.onboarding.history_types import CAUSE_INTERRUPTED, HistorySession, StopInfo
from agent_os.onboarding.sources._native import (
    NativeTranscript, error_stop, is_default_home, iso_text, json_lines, text_parts,
    to_history,
)


class PiSource:
    agent = "pi"

    # ---- locations ---------------------------------------------------------

    def agent_dir(self, home: str) -> str:
        env = os.environ.get("PI_CODING_AGENT_DIR") if is_default_home(home) else None
        return os.path.expanduser(env) if env else os.path.join(home, ".pi", "agent")

    def session_dirs(self, home: str) -> list[str]:
        agent_dir = self.agent_dir(home)
        dirs = [os.path.join(agent_dir, "sessions")]
        custom = os.environ.get("PI_CODING_AGENT_SESSION_DIR") if is_default_home(home) else None
        if not custom:
            try:
                with open(os.path.join(agent_dir, "settings.json"), encoding="utf-8") as fh:
                    value = json.load(fh).get("sessionDir")
                custom = value if isinstance(value, str) and value.strip() else None
            except (OSError, ValueError, AttributeError):
                custom = None
        if custom:
            custom = custom.strip()
            if custom.startswith("~"):
                custom = os.path.join(home, custom[1:].lstrip("/\\"))
            if os.path.isabs(custom) and custom not in dirs:
                dirs.append(custom)
        return dirs

    def session_files(self, home: str) -> list[str]:
        out: list[str] = []
        for root in self.session_dirs(home):
            try:
                entries = sorted(os.scandir(root), key=lambda e: e.name)
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_file() and entry.name.endswith(".jsonl"):
                        out.append(entry.path)  # flat custom sessionDir
                    elif entry.is_dir():
                        out.extend(os.path.join(entry.path, n) for n in sorted(os.listdir(entry.path))
                                   if n.endswith(".jsonl"))
                except OSError:
                    continue
        return out

    @staticmethod
    def header(path: str) -> dict | None:
        """The session header (first line), or None — a one-line read."""
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                rec = json.loads(fh.readline() or "null")
        except (OSError, ValueError):
            return None
        if isinstance(rec, dict) and rec.get("type") == "session" and rec.get("id"):
            return rec
        return None

    # ---- parsing -----------------------------------------------------------

    def transcript(self, path: str) -> NativeTranscript | None:
        head = self.header(path)
        if head is None:
            return None
        t = NativeTranscript(
            agent=self.agent, native_id=str(head["id"]), path=path,
            cwd=head.get("cwd") if isinstance(head.get("cwd"), str) else None,
            started_at=iso_text(head.get("timestamp")), resume_path=path)
        try:
            for rec in json_lines(path):
                rtype = rec.get("type")
                if rtype == "session_info":
                    if isinstance(rec.get("name"), str) and rec["name"].strip():
                        t.title = rec["name"].strip()
                    continue
                if rtype != "message" or not isinstance(rec.get("message"), dict):
                    continue
                self._message(t, rec["message"], iso_text(rec.get("timestamp")))
        except OSError:
            return None
        return t

    @staticmethod
    def _message(t: NativeTranscript, msg: dict, ts: str | None) -> None:
        role = msg.get("role")
        if role == "user":
            t.new_turn(text_parts(msg.get("content")), ts)
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
            # A later successful message (a retry) clears an earlier error.
            turn.stop = (error_stop(msg.get("errorMessage"), ts) if reason == "error"
                         else StopInfo(CAUSE_INTERRUPTED) if reason == "aborted" else None)
        # toolResult, bashExecution, custom / summary messages: not the conversation

    def read(self, path: str) -> HistorySession | None:
        try:
            t = self.transcript(path)
            return to_history(t) if t is not None else None
        except Exception:  # never raises (contract): a bad file is skipped
            return None


SOURCE = PiSource()
