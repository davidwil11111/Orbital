# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``<use_skill>`` block a composer-picked skill adds to a message (spec 132).

FastAPI-free, like ``_attachment_formatter``. The inject route builds it
after the ``<attached_files>`` block and before the user's text, before the
manager/worker split, so every backend gets the same text:

    <use_skill name="task-planning" path="/abs/ws/orbital/skills/task-planning/SKILL.md">
    The user chose this skill for this message. Read the file at `path` before acting and follow it.
    </use_skill>

A pointer, not the body: skills reference sibling files by relative path,
and the agent reads SKILL.md itself. The one exception is a safe-mode
manager that cannot read the path (outside every read root); it gets the
body inline (capped) instead, marked ``inline="true"``.

Keep in sync: web/src/lib/skill-parsing.ts (buildSkillBlock /
parseSkillBlock) and the title strip in agent_os/agent/session.py.
"""

from __future__ import annotations

SKILL_INLINE_CAP_BYTES = 32 * 1024

_POINTER_LINE = (
    "The user chose this skill for this message. "
    "Read the file at `path` before acting and follow it."
)
_INLINE_LINE = (
    "The user chose this skill for this message. Its SKILL.md is below "
    "(the path is outside what you can read); follow it."
)
_CLOSE = "</use_skill>"


def _attr(value: str) -> str:
    return (value.replace("&", "&amp;").replace('"', "&quot;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _cap(body: str) -> str:
    raw = body.encode("utf-8")
    if len(raw) <= SKILL_INLINE_CAP_BYTES:
        return body
    head = raw[:SKILL_INLINE_CAP_BYTES].decode("utf-8", errors="ignore")
    return (head + f"\n[… truncated: SKILL.md is {len(raw)} bytes, "
            f"only the first {SKILL_INLINE_CAP_BYTES} are shown]")


def format_skill_prefix(name: str, path: str, *, inline_body: str | None = None) -> str:
    """Build the block; always ends with ``\\n\\n`` so the user's text follows
    on its own paragraph (the same contract as ``format_prefix``)."""
    attrs = f'name="{_attr(name)}" path="{_attr(path)}"'
    if inline_body is None:
        return f"<use_skill {attrs}>\n{_POINTER_LINE}\n{_CLOSE}\n\n"
    # A closing tag inside the body would end the block early for every
    # parser downstream (title strip, FE chip); neutralize it.
    body = _cap(inline_body).replace(_CLOSE, "&lt;/use_skill&gt;").rstrip("\n")
    return (f'<use_skill {attrs} inline="true">\n{_INLINE_LINE}\n'
            f"<skill_content>\n{body}\n</skill_content>\n{_CLOSE}\n\n")
