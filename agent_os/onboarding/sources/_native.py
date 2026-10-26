# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""One parsed shape for the pi / Kimi Code / MiniMax Code readers.

Each reader parses its agent's files into a :class:`NativeTranscript` — the
turns as stored (user text NOT yet cleaned), the files the tools touched, and
how each turn ended. Two consumers read it:

* :func:`to_history` — the spec 138 :class:`HistorySession` (scan, grouping,
  left-off cards);
* ``session_import.convert_transcript`` — the rows of an imported session.

So a format is parsed once, in its reader, and both views agree.
"""

from __future__ import annotations

import json
import os
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agent_os.onboarding.history_types import (
    CAUSE_INTERRUPTED, CAUSE_NO_REPLY, HistorySession, StopInfo, classify_error_text,
)
from agent_os.onboarding.text_clean import (
    KIND_AUTOMATION, KIND_INJECTED, KIND_USER, clean_user_text,
)

# Tool names (lowercased) by what they do to the ``path`` argument.
_EDIT_TOOLS = frozenset({"edit", "multiedit", "str_replace", "str_replace_editor",
                         "apply_patch", "strreplacefile"})
_CREATE_TOOLS = frozenset({"write", "writefile", "create", "create_file"})
_READ_TOOLS = frozenset({"read", "readfile", "readmediafile", "view"})
_SKILL_TOOLS = frozenset({"skill"})
_PATH_KEYS = ("path", "file_path", "filePath", "filename")

# A skill used through its files: ~/.claude/skills/<name>/…, ~/.agents/skills/…,
# pi's ~/.pi/agent/skills/…, ~/.codex/skills/…; dot-dirs are not skills.
_SKILL_PATH_RE = re.compile(
    r"/\.(?:claude|codex|agents|pi/agent|kimi-code|minimax)/skills/([^/.][^/]*)/")

# Absolute paths the user named in their own words.
_MENTION_RE = re.compile(
    r"(?<![\w/~.])(/(?:Users|home|private|tmp|var|opt|Volumes|srv|mnt|workspace)/[^\s'\"`<>()\[\]{},;]+)")


@dataclass
class NativeTurn:
    user: str | None = None          # the user row's text as stored (uncleaned)
    user_ts: str | None = None
    texts: list[str] = field(default_factory=list)  # assistant reply text
    tools: "OrderedDict[str, int]" = field(default_factory=OrderedDict)
    reply_ts: str | None = None
    stop: StopInfo | None = None     # recorded abnormal end of this turn

    def has_output(self) -> bool:
        return bool(self.texts or self.tools)

    def add_text(self, text: str, ts: str | None) -> None:
        text = (text or "").strip()
        if text:
            self.texts.append(text)
            self.reply_ts = ts or self.reply_ts

    def add_tool(self, name: str, ts: str | None) -> None:
        name = name or "tool"
        self.tools[name] = self.tools.get(name, 0) + 1
        self.reply_ts = ts or self.reply_ts


@dataclass
class NativeTranscript:
    agent: str
    native_id: str
    path: str
    cwd: str | None = None
    title: str | None = None
    started_at: str | None = None
    resume_path: str | None = None
    turns: list[NativeTurn] = field(default_factory=list)
    edits: dict[str, int] = field(default_factory=dict)
    creates: dict[str, int] = field(default_factory=dict)
    reads: dict[str, int] = field(default_factory=dict)
    skills: list[str] = field(default_factory=list)

    # ---- building ----------------------------------------------------------

    def new_turn(self, user: str | None = None, ts: str | None = None) -> NativeTurn:
        turn = NativeTurn(user=user, user_ts=ts)
        self.turns.append(turn)
        return turn

    def open_turn(self) -> NativeTurn:
        """The turn assistant output belongs to (a user-less one at the start)."""
        return self.turns[-1] if self.turns else self.new_turn()

    def tool_call(self, name: str, args, ts: str | None) -> None:
        """Count the call on the open turn and record the files it touched."""
        self.open_turn().add_tool(name, ts)
        lname = (name or "").lower()
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (ValueError, TypeError):
                args = {}
        if not isinstance(args, dict):
            return
        if lname in _SKILL_TOOLS:
            skill = args.get("skill") or args.get("name")
            if isinstance(skill, str) and skill.strip():
                self._skill(skill.strip())
        path = next((args[k] for k in _PATH_KEYS if isinstance(args.get(k), str)), None)
        if not path:
            return
        path = self._abs(path)
        if path is None:
            return
        target = (self.edits if lname in _EDIT_TOOLS else self.creates if lname in _CREATE_TOOLS
                  else self.reads if lname in _READ_TOOLS else None)
        if target is not None:
            target[path] = target.get(path, 0) + 1
        m = _SKILL_PATH_RE.search(path)
        if m:
            self._skill(m.group(1))

    def _skill(self, name: str) -> None:
        if name not in self.skills:
            self.skills.append(name)

    def _abs(self, path: str) -> str | None:
        path = path.strip()
        if path.startswith("~"):
            return None  # whose home? not worth guessing
        if not os.path.isabs(path):
            if not self.cwd:
                return None
            path = os.path.join(self.cwd, path)
        return os.path.normpath(path)


# --------------------------------------------------------------------------
# NativeTranscript -> HistorySession
# --------------------------------------------------------------------------


def cleaned_turns(transcript: NativeTranscript) -> list[tuple[NativeTurn, str, str | None]]:
    """``(turn, kind, text)`` for the turns that are not Orbital's own.

    A turn whose user row is entirely Orbital's (the memory prompt) is dropped
    with its reply; an automation turn keeps its task as the text; a turn
    with no user row at all (output before any message) has kind None."""
    out = []
    for turn in transcript.turns:
        if turn.user is None:
            out.append((turn, None, None))
            continue
        cleaned = clean_user_text(turn.user)
        if cleaned.kind == KIND_INJECTED:
            continue
        if cleaned.kind == KIND_AUTOMATION and cleaned.text is None:
            continue
        out.append((turn, cleaned.kind, cleaned.text))
    return out


def to_history(t: NativeTranscript, *, orbital_started: bool = False) -> HistorySession | None:
    turns = cleaned_turns(t)
    if not any(kind is not None for _turn, kind, _text in turns):
        return None  # nothing anyone said
    user_texts = [(text, turn.user_ts) for turn, kind, text in turns if kind == KIND_USER]
    said = [kind for _turn, kind, _text in turns if kind is not None]
    mentions: dict[str, int] = {}
    for text, _ts in user_texts:
        for raw in _MENTION_RE.findall(text):
            path = os.path.normpath(raw.rstrip(".:!?"))
            mentions[path] = mentions.get(path, 0) + 1
    stamps = [ts for turn, _k, _x in turns for ts in (turn.user_ts, turn.reply_ts) if ts]
    return HistorySession(
        agent=t.agent, native_id=t.native_id, path=t.path, cwd=t.cwd, title=t.title,
        started_at=t.started_at or (min(stamps) if stamps else None),
        ended_at=max(stamps) if stamps else None,
        first_user_text=user_texts[0][0] if user_texts else None,
        last_user_text=user_texts[-1][0] if user_texts else None,
        last_user_at=user_texts[-1][1] if user_texts else None,
        user_turns=len(user_texts),
        automation=all(kind == KIND_AUTOMATION for kind in said),
        orbital_started=orbital_started,
        edits=dict(t.edits), creates=dict(t.creates), reads=dict(t.reads),
        mentions=mentions, skills=list(t.skills),
        stop=_last_stop(turns), resume_path=t.resume_path,
    )


def _last_stop(turns) -> StopInfo | None:
    for turn, kind, _text in reversed(turns):
        if turn.stop is not None:
            return turn.stop
        if kind is not None and not turn.has_output():
            return StopInfo(CAUSE_NO_REPLY)
        if kind is not None or turn.has_output():
            return None
    return None


def error_stop(text: str | None, at: str | None) -> StopInfo:
    """The stop an agent's recorded error means; an unplaced error is still
    an abnormal end, so it reads as interrupted."""
    return classify_error_text(text, at=at) or StopInfo(
        CAUSE_INTERRUPTED, " ".join((text or "").split())[:200] or None)


# --------------------------------------------------------------------------
# Small helpers shared by the readers
# --------------------------------------------------------------------------


def iso_ms(value) -> str | None:
    """Epoch milliseconds as Orbital's UTC ``isoformat()``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def iso_text(value) -> str | None:
    """An ISO-8601 string as Orbital's UTC ``isoformat()``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def json_lines(path: str):
    """Each JSON object of a JSONL file; torn or non-object lines skipped."""
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(rec, dict):
                yield rec


def text_parts(content, kinds=("text",)) -> str:
    """The text of a message ``content`` (a string or a list of parts)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n\n".join(p["text"] for p in content
                       if isinstance(p, dict) and p.get("type") in kinds
                       and isinstance(p.get("text"), str))


def history_home(home: str | None = None) -> str:
    """``home`` > ``$ORBITAL_HISTORY_HOME`` > ``~`` — the home whose agent
    stores are read (tests and smokes point it at a staged home)."""
    from agent_os.onboarding import history_paths
    return home or history_paths.history_home()


def is_default_home(home: str) -> bool:
    """True when ``home`` is the user's real home, so the agents' own
    relocation variables (``PI_CODING_AGENT_DIR``, ``KIMI_CODE_HOME``) apply."""
    try:
        return os.path.realpath(home) == os.path.realpath(os.path.expanduser("~"))
    except OSError:
        return False
