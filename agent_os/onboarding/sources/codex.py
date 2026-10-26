# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Codex history: ``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``.

One rollout per thread. ``session_meta`` carries the thread id, cwd, git
branch and ``originator`` ("orbital" = Orbital's own app-server worker);
``response_item`` rows carry the conversation and tool calls; ``event_msg``
rows carry the turn ends (``task_complete``, ``turn_aborted``, ``error``) and
file changes. Codex's own sub-agent and reviewer threads are not sessions.
Read-only.
"""

from __future__ import annotations

import json
import logging
import os
import re

from agent_os.onboarding.history_paths import codex_dir
from agent_os.onboarding.history_types import HistorySession
from agent_os.onboarding.sources._common import (
    PATCH_RE, TurnState, bump, iter_json_lines, norm_ts, note_mentions, touch,
)

logger = logging.getLogger(__name__)

AGENT = "codex"

_ORBITAL_ORIGINATORS = frozenset({"orbital"})

# Codex wraps injected context in tagged blocks (<environment_context>,
# <user_instructions>, …) and prefixes the AGENTS.md preamble; neither is the
# user's words.
_TAG_BLOCK_RE = re.compile(r"^</?[A-Za-z_][\w-]*(\s[^>]*)?>")
_AGENTS_MD_PREFIX = "# AGENTS.md instructions for"


class CodexSource:
    agent = AGENT

    def session_files(self, home: str) -> list[str]:
        root = os.path.join(codex_dir(home), "sessions")
        out: list[str] = []
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if name.startswith("rollout-") and name.endswith(".jsonl"):
                    out.append(os.path.join(dirpath, name))
        return out

    def read(self, path: str) -> HistorySession | None:
        try:
            return _read(path)
        except Exception:
            logger.warning("history: could not read Codex rollout %s", path, exc_info=True)
            return None


def _user_text(payload: dict) -> str:
    parts = []
    for block in payload.get("content") or []:
        if not isinstance(block, dict) or block.get("type") not in ("input_text", "text"):
            continue
        text = block.get("text")
        if not isinstance(text, str):
            continue
        stripped = text.strip()
        if not stripped or _TAG_BLOCK_RE.match(stripped) or stripped.startswith(_AGENTS_MD_PREFIX):
            continue
        parts.append(stripped)
    return "\n\n".join(parts).strip()


def _assistant_has_text(payload: dict) -> bool:
    return any(isinstance(b, dict) and b.get("type") in ("output_text", "text")
               and isinstance(b.get("text"), str) and b["text"].strip()
               for b in payload.get("content") or [])


def _is_helper_thread(payload: dict) -> bool:
    source = payload.get("source")
    if isinstance(source, dict) and "subagent" in source:
        return True
    return isinstance(source, str) and "subagent" in source


def _read(path: str) -> HistorySession | None:
    s = HistorySession(agent=AGENT, native_id="", path=path, resume_path=path)
    turn = TurnState(s)
    any_message = False
    for rec in iter_json_lines(path):
        rtype = rec.get("type")
        payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        ts = norm_ts(rec.get("timestamp"))
        if rtype == "session_meta":
            if s.native_id:
                continue
            if _is_helper_thread(payload):
                return None
            s.native_id = str(payload.get("id") or "")
            if isinstance(payload.get("cwd"), str) and payload["cwd"]:
                s.cwd = payload["cwd"]
            if payload.get("originator") in _ORBITAL_ORIGINATORS:
                s.orbital_started = True
            git = payload.get("git")
            if isinstance(git, dict) and isinstance(git.get("branch"), str) and git["branch"]:
                s.branch = git["branch"]
            touch(s, norm_ts(payload.get("timestamp")) or ts)
            continue
        if rtype == "turn_context":
            if s.cwd is None and isinstance(payload.get("cwd"), str):
                s.cwd = payload["cwd"]
            continue
        ptype = payload.get("type")
        if rtype == "response_item":
            if ptype == "message":
                role = payload.get("role")
                if role == "user":
                    text = _user_text(payload)
                    if text:
                        any_message = True
                        touch(s, ts)
                        turn.user_text(text, ts)
                elif role == "assistant" and _assistant_has_text(payload):
                    any_message = True
                    touch(s, ts)
                    turn.answered()
            elif isinstance(ptype, str) and ptype.endswith("_call"):
                touch(s, ts)
                turn.working()
                blob = payload.get("arguments") or payload.get("input") or ""
                _note_patch(s, blob)
                note_mentions(s, blob)
            continue
        if rtype != "event_msg":
            continue
        if ptype == "task_complete":
            touch(s, ts)
            err = payload.get("error")
            if err:
                turn.error(err if isinstance(err, str) else json.dumps(err), ts)
            elif turn.state != "stopped":
                # The turn closed normally (an error event before it keeps
                # the turn stopped).
                turn.answered()
        elif ptype == "turn_aborted":
            touch(s, ts)
            turn.interrupted(str(payload.get("reason") or "aborted"))
        elif ptype == "error":
            touch(s, ts)
            turn.error(str(payload.get("message") or ""), ts)
        elif ptype == "item_completed":
            item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
            if item.get("type") == "FileChange":
                _note_file_change(s, item.get("changes"))
    if not s.native_id or not any_message:
        return None
    turn.finish()
    return s


def _abs(s: HistorySession, rel: str) -> str:
    rel = rel.strip()
    return rel if os.path.isabs(rel) else os.path.join(s.cwd or "/", rel)


def _note_patch(s: HistorySession, blob) -> None:
    if not isinstance(blob, str):
        return
    for kind, rel in PATCH_RE.findall(blob):
        bump(s.creates if kind == "Add" else s.edits, _abs(s, rel))


def _note_file_change(s: HistorySession, changes) -> None:
    if not isinstance(changes, dict):
        return
    for rel, change in changes.items():
        if not isinstance(rel, str):
            continue
        kind = change.get("type") if isinstance(change, dict) else None
        bump(s.creates if kind == "add" else s.edits, _abs(s, rel))


SOURCE = CodexSource()
