# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Skill metadata scanner for Agent OS.

Scans workspace/skills/{name}/SKILL.md files and returns metadata.
"""

import os

import yaml

# Frontmatter lines read before giving up on a closing ``---``, and the H1
# window after it (the H1 window was the whole parse before spec 132).
_FRONTMATTER_MAX_LINES = 200
_H1_WINDOW_LINES = 20


def _one_line(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def parse_skill_md(filepath: str) -> dict | None:
    """Read a SKILL.md's name and description, metadata only.

    YAML frontmatter (``name:`` / ``description:`` between leading ``---``
    lines, the Claude Code / Codex layout) wins. Whatever it does not supply
    falls back to the first ``# `` H1 and the first non-blank line after it,
    within 20 lines of the frontmatter's end — the only format before spec
    132, so a file without frontmatter parses exactly as it did.

    Returns ``{"name": str | None, "description": str | None}``, or None when
    the file cannot be read.
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = []
            for i, line in enumerate(f):
                if i >= _FRONTMATTER_MAX_LINES + _H1_WINDOW_LINES:
                    break
                lines.append(line.rstrip("\r\n"))
    except OSError:
        return None

    name = None
    description = None
    body_start = 0
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, min(len(lines), _FRONTMATTER_MAX_LINES))
                    if lines[i].strip() == "---"), None)
        if end is not None:
            body_start = end + 1
            try:
                meta = yaml.safe_load("\n".join(lines[1:end]))
            except yaml.YAMLError:
                meta = None
            if isinstance(meta, dict):
                name = _one_line(meta.get("name"))
                description = _one_line(meta.get("description"))

    h1 = None
    for line in lines[body_start:body_start + _H1_WINDOW_LINES]:
        line = line.rstrip()
        if line.startswith("# ") and h1 is None:
            h1 = line[2:].strip()
            if name is None:
                name = h1 or None
        elif h1 is not None and line.strip():
            if description is None:
                description = line.strip()
            break

    return {"name": name, "description": description}


class SkillLoader:
    """Scan for skill definitions in the workspace."""

    def __init__(self, workspace: str):
        from agent_os.agent.project_paths import ProjectPaths
        self.workspace = workspace
        self._skills_dir = ProjectPaths(workspace).skills_dir

    def scan(self) -> list[dict]:
        """Scan for SKILL.md files. Return metadata only (not full content).

        Returns [{"name": str, "description": str, "path": str}, ...]
        Returns [] if no skills directory. Sorted by directory name, so the
        system prompt's skill list is stable across scans.
        """
        if not os.path.isdir(self._skills_dir):
            return []
        results = []
        for entry in sorted(os.scandir(self._skills_dir), key=lambda e: e.name):
            if entry.is_dir():
                skill_md = os.path.join(entry.path, "SKILL.md")
                if os.path.isfile(skill_md):
                    meta = self._parse_front_matter(skill_md)
                    if meta:
                        meta["path"] = skill_md
                        meta["dir_name"] = entry.name
                        results.append(meta)
        return results

    def _parse_front_matter(self, filepath: str) -> dict | None:
        """Name and description, or None unless both are found.

        Expected format: YAML frontmatter with ``name`` / ``description``, or
        # Skill Name
        Description text here.
        """
        meta = parse_skill_md(filepath)
        if meta and meta["name"] and meta["description"]:
            return {"name": meta["name"], "description": meta["description"]}
        return None
