# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 132 — the aggregated skill catalog behind the composer's `/` picker.

Every test builds its own workspace and fake HOME under tmp_path; nothing
reads the real ~/.claude, ~/.codex or ~/.agents.
"""

from __future__ import annotations

import os

import pytest

from agent_os.agent.skill_catalog import SkillCatalog
from agent_os.agent.skills import SkillLoader, parse_skill_md


def _symlink_or_skip(target, link):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        # Windows without Developer Mode / symlink privilege can't create one.
        pytest.skip("symlink creation not permitted on this platform")


def _skill(root, dir_name, text):
    d = os.path.join(str(root), dir_name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(text)
    return os.path.join(d, "SKILL.md")


FM = "---\nname: {name}\ndescription: {desc}\n---\n\n# Heading {name}\n\nBody.\n"


@pytest.fixture
def env(tmp_path):
    ws = tmp_path / "ws"
    home = tmp_path / "home"
    ws.mkdir()
    home.mkdir()
    return ws, home


def _catalog(ws, home):
    return SkillCatalog(str(ws), home=str(home)).scan()


def _names(entries):
    return [e["name"] for e in entries]


# ---- parser -----------------------------------------------------------------


class TestParser:
    def test_frontmatter_wins_over_h1(self, tmp_path):
        p = _skill(tmp_path, "x", FM.format(name="wecomcli-msg", desc="Send messages"))
        meta = parse_skill_md(p)
        assert meta == {"name": "wecomcli-msg", "description": "Send messages"}

    def test_h1_fallback_without_frontmatter(self, tmp_path):
        p = _skill(tmp_path, "x", "# Task Planning\n\nMatch planning effort.\n")
        assert parse_skill_md(p) == {"name": "Task Planning",
                                     "description": "Match planning effort."}

    def test_folded_yaml_description_is_one_line(self, tmp_path):
        p = _skill(tmp_path, "x",
                   "---\nname: folded\ndescription: >\n  first part\n  second part\n---\n")
        assert parse_skill_md(p)["description"] == "first part second part"

    def test_frontmatter_without_description_falls_back_to_h1_line(self, tmp_path):
        p = _skill(tmp_path, "x", "---\nname: only-name\n---\n\n# Title\n\nThe line.\n")
        assert parse_skill_md(p) == {"name": "only-name", "description": "The line."}

    def test_broken_yaml_falls_back_to_h1(self, tmp_path):
        p = _skill(tmp_path, "x", "---\nname: [unclosed\n---\n\n# Real Name\n\nDesc.\n")
        assert parse_skill_md(p) == {"name": "Real Name", "description": "Desc."}

    def test_skill_loader_reads_frontmatter_too(self, tmp_path):
        from agent_os.agent.project_paths import ProjectPaths
        root = ProjectPaths(str(tmp_path)).skills_dir
        _skill(root, "fm", FM.format(name="fm-skill", desc="From frontmatter"))
        _skill(root, "plain", "# Plain\nPlain desc.\n")
        out = SkillLoader(str(tmp_path)).scan()
        assert [s["name"] for s in out] == ["fm-skill", "Plain"]
        assert out[0]["description"] == "From frontmatter"

    def test_skill_loader_scan_is_sorted(self, tmp_path):
        from agent_os.agent.project_paths import ProjectPaths
        root = ProjectPaths(str(tmp_path)).skills_dir
        for n in ["zeta", "alpha", "mid"]:
            _skill(root, n, f"# {n}\n{n} desc.\n")
        assert [s["dir_name"] for s in SkillLoader(str(tmp_path)).scan()] == [
            "alpha", "mid", "zeta"]


# ---- sources, precedence, dedupe ---------------------------------------------


class TestCatalog:
    def test_every_source_is_aggregated(self, env):
        ws, home = env
        _skill(ws / "orbital" / "skills", "task-planning", "# Task Planning\nPlan.\n")
        _skill(ws / ".claude" / "skills", "ws-claude", FM.format(name="ws-claude", desc="a"))
        _skill(ws / ".agents" / "skills", "ws-agents", FM.format(name="ws-agents", desc="b"))
        _skill(home / ".agents" / "skills", "u-agents", FM.format(name="u-agents", desc="c"))
        _skill(home / ".claude" / "skills", "u-claude", FM.format(name="u-claude", desc="d"))
        _skill(home / ".codex" / "skills", "u-codex", FM.format(name="u-codex", desc="e"))
        entries = _catalog(ws, home)
        assert sorted(_names(entries)) == sorted([
            "Task Planning", "ws-claude", "ws-agents", "u-agents", "u-claude", "u-codex"])
        by_name = {e["name"]: e for e in entries}
        assert by_name["Task Planning"]["source"] == "project"
        assert by_name["Task Planning"]["dir_name"] == "task-planning"
        assert by_name["u-codex"]["source"] == "codex"
        for e in entries:
            assert set(e) >= {"key", "name", "description", "source", "path", "dir_name"}
            assert e["path"].endswith("SKILL.md")
            assert os.path.isabs(e["path"])

    def test_output_is_sorted_by_name(self, env):
        ws, home = env
        for n in ["zz", "aa", "mm"]:
            _skill(home / ".agents" / "skills", n, FM.format(name=n, desc=n))
        assert _names(_catalog(ws, home)) == ["aa", "mm", "zz"]

    def test_same_name_highest_source_wins(self, env):
        ws, home = env
        proj = _skill(ws / "orbital" / "skills", "dup", FM.format(name="Dup", desc="project copy"))
        _skill(home / ".claude" / "skills", "dup", FM.format(name="dup", desc="global copy"))
        _skill(home / ".codex" / "skills", "dup2", FM.format(name=" DUP ", desc="codex copy"))
        entries = _catalog(ws, home)
        assert len(entries) == 1
        assert entries[0]["path"] == proj
        assert entries[0]["description"] == "project copy"

    def test_symlinked_dir_dedupes_by_realpath(self, env):
        """~/.claude/skills/x -> ~/.agents/skills/x (the dev-Mac layout): one row,
        from the higher source (~/.agents)."""
        ws, home = env
        real = _skill(home / ".agents" / "skills", "shared", FM.format(name="shared", desc="s"))
        (home / ".claude" / "skills").mkdir(parents=True)
        _symlink_or_skip(os.path.dirname(real), str(home / ".claude" / "skills" / "shared"))
        entries = _catalog(ws, home)
        assert len(entries) == 1
        assert entries[0]["source"] == "agents"
        assert entries[0]["path"] == real

    def test_realpath_dedupe_beats_a_renamed_frontmatter(self, env):
        """Same directory reached twice, even when nothing else matches."""
        ws, home = env
        real = _skill(home / ".agents" / "skills", "a", FM.format(name="alpha", desc="x"))
        (home / ".codex" / "skills").mkdir(parents=True)
        _symlink_or_skip(os.path.dirname(real), str(home / ".codex" / "skills" / "other-name"))
        assert len(_catalog(ws, home)) == 1

    def test_symlinked_dir_outside_any_root_is_followed(self, env, tmp_path):
        """A dotfiles-style link (~/.claude/skills/x -> ~/dotfiles/x) is a real
        install; the directory link is followed."""
        ws, home = env
        real = _skill(tmp_path / "dotfiles", "mine", FM.format(name="mine", desc="m"))
        (home / ".claude" / "skills").mkdir(parents=True)
        _symlink_or_skip(os.path.dirname(real), str(home / ".claude" / "skills" / "mine"))
        assert _names(_catalog(ws, home)) == ["mine"]

    def test_skill_md_symlink_escaping_its_dir_is_rejected(self, env, tmp_path):
        """The SKILL.md itself must live inside its skill dir (realpath): a link
        to an arbitrary file is never parsed, listed or handed to an agent."""
        ws, home = env
        secret = tmp_path / "secret.txt"
        secret.write_text("# Secret\nprivate key material\n")
        d = ws / ".claude" / "skills" / "evil"
        d.mkdir(parents=True)
        _symlink_or_skip(str(secret), str(d / "SKILL.md"))
        assert _catalog(ws, home) == []

    def test_dot_dirs_and_dirs_without_skill_md_are_skipped(self, env):
        ws, home = env
        _skill(home / ".codex" / "skills" / ".system", "hidden", FM.format(name="hidden", desc="h"))
        _skill(home / ".codex" / "skills", ".dotted", FM.format(name="dotted", desc="d"))
        (home / ".claude" / "skills" / "synced" / "nested").mkdir(parents=True)
        _skill(home / ".claude" / "skills", "ok", FM.format(name="ok", desc="ok"))
        assert _names(_catalog(ws, home)) == ["ok"]

    def test_files_in_a_source_root_are_skipped(self, env):
        ws, home = env
        (home / ".agents" / "skills").mkdir(parents=True)
        (home / ".agents" / "skills" / "README.md").write_text("# not a skill\n")
        assert _catalog(ws, home) == []

    def test_name_falls_back_to_dir_name(self, env):
        ws, home = env
        _skill(home / ".agents" / "skills", "bare-dir", "no heading at all\n")
        entries = _catalog(ws, home)
        assert _names(entries) == ["bare-dir"]
        assert entries[0]["description"] == ""

    def test_missing_roots_yield_empty(self, env):
        ws, home = env
        assert _catalog(ws, home) == []

    def test_resolve_accepts_only_catalog_paths(self, env, tmp_path):
        ws, home = env
        good = _skill(ws / "orbital" / "skills", "good", "# Good\nGood.\n")
        stray = tmp_path / "stray" / "SKILL.md"
        stray.parent.mkdir()
        stray.write_text("# Stray\nStray.\n")
        cat = SkillCatalog(str(ws), home=str(home))
        assert cat.resolve(good)["name"] == "Good"
        assert cat.resolve(str(stray)) is None
        assert cat.resolve(os.path.join(os.path.dirname(good), "..", "good", "SKILL.md")) is None
        assert cat.resolve("") is None
