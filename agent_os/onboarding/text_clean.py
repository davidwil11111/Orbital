# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Separate the user's own words from text Orbital injected into a turn.

Orbital-driven runs of Claude Code, Codex and the other workers store what
Orbital sent as ordinary user turns: the end-of-turn memory prompt, the
"Triggered by schedule" header of an automation run, the recap a worker gets
on dispatch, and system-reminder / pasted-content wrappers. Anything that
shows "your last message" (imported transcripts, left-off cards, grouping by
opening request) must read through :func:`clean_user_text` first, or it shows
Orbital talking to itself.

Pure functions, no I/O.
"""

from __future__ import annotations

import re
from typing import NamedTuple

KIND_USER = "user"              # the user's own words (possibly after stripping)
KIND_AUTOMATION = "automation"  # an Orbital trigger fired this turn
KIND_INJECTED = "injected"      # entirely Orbital's text; nothing of the user's

# End-of-turn memory prompts (agent/loop.py). The whole turn is Orbital's.
_INJECTED_PREFIXES = ("[Orbital memory] ", "[MEMORY] ")

# trigger_manager.py: "[Triggered by schedule 'name' (every day at 9)]\n\n<task>"
# and "[Triggered by file_watch 'name']\n\nChanged files: …\n\n<task>".
_TRIGGER_RE = re.compile(r"^\[Triggered by (?:schedule|file_watch) '[^'\n]*'[^\]\n]*\]\s*")

# daemon_v2/recap.py: the block a worker receives on dispatch. Only the text
# after the footer is what the user typed for this turn.
_RECAP_HEADER = "Conversation so far ("
_RECAP_FOOTER = "--- end of conversation so far ---"

# Older Orbital builds prefixed a dispatch with a bulleted session-log block.
_PREV_LOGS_PREFIX = "Previous session logs"

_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>[\s\S]*?</system-reminder>")
_PASTED_TAG_RE = re.compile(r"</?pasted_content\b[^>]*>")


class CleanedText(NamedTuple):
    kind: str
    # The user-visible words, stripped; None when nothing of the user's is left.
    text: str | None


def clean_user_text(text: object) -> CleanedText:
    """Classify one user-turn text and strip what Orbital injected into it."""
    if not isinstance(text, str):
        return CleanedText(KIND_INJECTED, None)
    t = _SYSTEM_REMINDER_RE.sub("", text)
    t = _PASTED_TAG_RE.sub("", t).strip()
    if not t or t.startswith(_INJECTED_PREFIXES):
        return CleanedText(KIND_INJECTED, None)

    m = _TRIGGER_RE.match(t)
    if m:
        rest = t[m.end():]
        if rest.startswith("Changed files:"):
            # file_watch: the changed-files line is Orbital's, the task follows.
            rest = rest.split("\n\n", 1)[1] if "\n\n" in rest else ""
        return CleanedText(KIND_AUTOMATION, rest.strip() or None)

    if _RECAP_FOOTER in t and _RECAP_HEADER in t:
        t = t.rsplit(_RECAP_FOOTER, 1)[1].strip()

    if t.startswith(_PREV_LOGS_PREFIX):
        lines = t.split("\n")[1:]
        while lines and (not lines[0].strip() or lines[0].lstrip().startswith("- ")):
            lines.pop(0)
        t = "\n".join(lines).strip()

    return CleanedText(KIND_USER, t) if t else CleanedText(KIND_INJECTED, None)
