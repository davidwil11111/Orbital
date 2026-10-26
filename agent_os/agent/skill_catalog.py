# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Every skill a project can use, as one flat list (spec 132).

The composer's ``/`` picker lists it, and the inject route re-resolves a
picked skill against it: a ``skill.path`` this catalog did not return is
refused, so a forged path can never point an agent at an arbitrary file.

Sources, highest precedence first::

    {ws}/orbital/skills   Orbital's own (the 4 defaults are seeded here)
    {ws}/.claude/skills   workspace-native
    {ws}/.agents/skills
    ~/.agents/skills      user-global; Claude Code and Codex share these
    ~/.claude/skills
    ~/.codex/skills

A skill is a directory holding a ``SKILL.md``. Dot-dirs (``~/.codex/skills/
.system``) and directories without one (``~/.claude/skills/synced``) are
skipped. Plugin skills (``~/.claude/plugins``) are not listed.

Dedupe runs in source order: first by the realpath of the skill dir (the dev
Mac's ``~/.claude/skills/x`` is a symlink into ``~/.agents/skills/x``), then
by normalized name. The first one seen wins.

Containment: a symlinked skill DIRECTORY is followed wherever it points (a
dotfiles checkout is a normal install), but the ``SKILL.md`` inside it must
resolve inside that directory's realpath. A ``SKILL.md`` that is a link out to
some other file is never parsed, listed, or handed to an agent.
"""

from __future__ import annotations

import os

from agent_os.agent.skills import parse_skill_md


def _normalize(name: str) -> str:
    return name.strip().lower()


def _inside(child_real: str, parent_real: str) -> bool:
    return child_real.startswith(parent_real.rstrip(os.sep) + os.sep)


class SkillCatalog:
    """Aggregate the skill sources for one project workspace."""

    def __init__(self, workspace: str, *, home: str | None = None):
        from agent_os.agent.project_paths import ProjectPaths
        self.workspace = workspace
        home = home if home is not None else os.path.expanduser("~")
        self._sources: list[tuple[str, str]] = []
        if workspace:
            self._sources += [
                ("project", ProjectPaths(workspace).skills_dir),
                ("workspace", os.path.join(workspace, ".claude", "skills")),
                ("workspace", os.path.join(workspace, ".agents", "skills")),
            ]
        self._sources += [
            ("agents", os.path.join(home, ".agents", "skills")),
            ("claude", os.path.join(home, ".claude", "skills")),
            ("codex", os.path.join(home, ".codex", "skills")),
        ]

    def scan(self) -> list[dict]:
        """``[{key, name, description, source, path, dir_name}]``, sorted by key.

        ``path`` is the absolute SKILL.md path inside its source root (not the
        resolved symlink target), which is what an agent is told to read.
        """
        seen_dirs: set[str] = set()
        seen_keys: set[str] = set()
        out: list[dict] = []
        for source, root in self._sources:
            try:
                entries = sorted(os.scandir(root), key=lambda e: e.name)
            except OSError:
                continue
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                try:
                    if not entry.is_dir():
                        continue
                except OSError:
                    continue
                skill_md = os.path.join(entry.path, "SKILL.md")
                if not os.path.isfile(skill_md):
                    continue
                dir_real = os.path.realpath(entry.path)
                if not _inside(os.path.realpath(skill_md), dir_real):
                    continue
                if dir_real in seen_dirs:
                    continue
                meta = parse_skill_md(skill_md)
                if meta is None:
                    continue
                name = meta["name"] or entry.name
                key = _normalize(name)
                if not key or key in seen_keys:
                    continue
                seen_dirs.add(dir_real)
                seen_keys.add(key)
                out.append({
                    "key": key,
                    "name": name,
                    "description": meta["description"] or "",
                    "source": source,
                    "path": os.path.abspath(skill_md),
                    "dir_name": entry.name,
                })
        out.sort(key=lambda e: e["key"])
        return out

    def resolve(self, path: str) -> dict | None:
        """The catalog entry whose ``path`` is exactly ``path``, else None."""
        if not path:
            return None
        for entry in self.scan():
            if entry["path"] == path:
                return entry
        return None
