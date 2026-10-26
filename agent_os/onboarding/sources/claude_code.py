# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Claude Code history: ``~/.claude/projects/<encoded cwd>/<session>.jsonl``.

One transcript per session. Records this reader uses: ``user`` (the user's
words, tool results, interrupt markers), ``assistant`` (text / tool_use
blocks; ``isApiErrorMessage`` rows carry the error the turn died on), the
title rows, and the per-record ``cwd`` / ``gitBranch`` / ``entrypoint``.
Sidechain (Task sub-agent) records are skipped. Read-only.
"""

from __future__ import annotations

import logging
import os

from agent_os.onboarding.history_paths import claude_dir
from agent_os.onboarding.history_types import (
    CAUSE_INTERRUPTED, CAUSE_NETWORK_AUTH, CAUSE_USAGE_LIMIT, HistorySession,
)
from agent_os.onboarding.sources._common import (
    TurnState, add_skill, bump, iter_json_lines, norm_ts, note_mentions, touch,
)

logger = logging.getLogger(__name__)

AGENT = "claude-code"

# Programmatic SDK sessions are Orbital's own claude-code worker runs.
_ORBITAL_ENTRYPOINTS = frozenset({"sdk-py", "sdk-ts"})

# User rows that are slash-command plumbing, not the user's words.
_MACHINE_PREFIXES = (
    "<command-name>", "<command-message>", "<command-args>",
    "<local-command-stdout>", "<local-command-stderr>", "<local-command-caveat>",
    "Caveat: The messages below were generated",
)
_INTERRUPT_PREFIX = "[Request interrupted by user"

_EDIT_TOOLS = frozenset({"Edit", "MultiEdit", "NotebookEdit"})
_CREATE_TOOLS = frozenset({"Write"})
_READ_TOOLS = frozenset({"Read", "Grep", "Glob", "LS"})

# ``error`` on an API-error row, when its text names no cause.
_ERROR_FIELD_CAUSES = {
    "rate_limit": CAUSE_USAGE_LIMIT,
    "billing_error": CAUSE_USAGE_LIMIT,
    "authentication_failed": CAUSE_NETWORK_AUTH,
}


class ClaudeCodeSource:
    agent = AGENT

    def session_files(self, home: str) -> list[str]:
        projects = os.path.join(claude_dir(home), "projects")
        out: list[str] = []
        try:
            dirs = list(os.scandir(projects))
        except OSError:
            return out
        for d in dirs:
            try:
                if not d.is_dir():
                    continue
                for f in os.scandir(d.path):
                    if f.name.endswith(".jsonl") and f.is_file():
                        out.append(f.path)
            except OSError:
                continue
        return out

    def read(self, path: str) -> HistorySession | None:
        try:
            return _read(path)
        except Exception:
            logger.warning("history: could not read Claude transcript %s", path, exc_info=True)
            return None


def _user_text(content) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text") for b in content
                 if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
        return "\n\n".join(parts) if parts else None
    return None


def _assistant_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text") or "" for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _read(path: str) -> HistorySession | None:
    stem = os.path.basename(path)[:-len(".jsonl")] if path.endswith(".jsonl") else os.path.basename(path)
    s = HistorySession(agent=AGENT, native_id=stem, path=path, resume_path=path)
    turn = TurnState(s)
    title = custom_title = None
    seen_id = False
    any_message = False
    for rec in iter_json_lines(path):
        rtype = rec.get("type")
        if rtype == "custom-title" and isinstance(rec.get("customTitle"), str):
            custom_title = rec["customTitle"]
        elif rtype == "ai-title" and isinstance(rec.get("aiTitle"), str):
            title = rec["aiTitle"]
        elif rtype == "summary" and isinstance(rec.get("summary"), str) and title is None:
            title = rec["summary"]
        if not seen_id and isinstance(rec.get("sessionId"), str):
            s.native_id, seen_id = rec["sessionId"], True
        if rec.get("isSidechain") is True:
            continue
        if s.cwd is None and isinstance(rec.get("cwd"), str) and rec["cwd"]:
            s.cwd = rec["cwd"]
        branch = rec.get("gitBranch")
        if isinstance(branch, str) and branch and branch != "HEAD":
            s.branch = branch
        if rec.get("entrypoint") in _ORBITAL_ENTRYPOINTS:
            s.orbital_started = True
        if rtype not in ("user", "assistant"):
            continue
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        ts = norm_ts(rec.get("timestamp"))
        content = msg.get("content")
        if rtype == "user":
            if rec.get("isMeta") or rec.get("isCompactSummary"):
                continue
            any_message = True
            touch(s, ts)
            text = _user_text(content)
            if text is not None:
                stripped = text.strip()
                if stripped.startswith(_INTERRUPT_PREFIX):
                    turn.interrupted("interrupted by user")
                elif stripped and not stripped.startswith(_MACHINE_PREFIXES):
                    turn.user_text(stripped, ts)
                continue
            if isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                turn.working()
            continue
        # assistant
        if rec.get("isApiErrorMessage"):
            any_message = True
            touch(s, ts)
            fallback = _ERROR_FIELD_CAUSES.get(rec.get("error"), CAUSE_INTERRUPTED)
            turn.error(_assistant_text(content), ts, fallback=fallback)
            continue
        if msg.get("model") == "<synthetic>":
            continue
        any_message = True
        touch(s, ts)
        blocks = content if isinstance(content, list) else (
            [{"type": "text", "text": content}] if isinstance(content, str) else [])
        has_tool = has_text = False
        for block in blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and (block.get("text") or "").strip():
                has_text = True
            elif btype == "tool_use":
                has_tool = True
                _note_tool(s, block.get("name"), block.get("input"))
        if has_tool:
            turn.working()
        elif has_text:
            turn.answered()
    if not any_message:
        return None
    turn.finish()
    s.title = custom_title or title
    return s


def _note_tool(s: HistorySession, name, inp) -> None:
    if not isinstance(inp, dict):
        return
    fp = inp.get("file_path") or inp.get("notebook_path")
    if name in _EDIT_TOOLS:
        bump(s.edits, fp)
    elif name in _CREATE_TOOLS:
        bump(s.creates, fp)
    elif name in _READ_TOOLS:
        bump(s.reads, fp or inp.get("path"))
    elif name == "Skill":
        skill = inp.get("skill") or inp.get("command")
        if isinstance(skill, str):
            add_skill(s, skill.split(":")[-1].strip() or None)
    note_mentions(s, inp)


SOURCE = ClaudeCodeSource()
