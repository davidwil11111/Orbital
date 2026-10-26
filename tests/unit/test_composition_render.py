# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Per-spawn patch rendering for dsh's ``acp`` profile.

The renderer is the ONLY route Orbital config takes into a harness that
configures itself through a file. It must never string-template: a persona is
arbitrary agent-authored prose and would otherwise be a YAML injection vector.

A dsh patch row REPLACES the row's whole ``config`` (verified against
0.2.1-rc.3 with ``--dump-config``), so the renderer only ever sets values
inside rows the template already restates in full.
"""

import os
import time

import pytest
import yaml

from agent_os.agents.composition import (
    CompositionError,
    gc_stale,
    render_composition,
    unlink_rendered,
)


SHIPPED_TEMPLATE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "agent_os", "agents", "assets", "dsh", "acp.patch.yml",
)


@pytest.fixture
def template(tmp_path):
    """A stand-in with the shipped template's rows plus one the renderer
    knows nothing about."""
    path = tmp_path / "acp.patch.yml"
    path.write_text(
        "- id: system-prompt\n"
        "  config:\n"
        "    personaPrefix: You are a coding agent.\n"
        "\n"
        "- id: acp\n"
        "  config:\n"
        "    provider: deepseek-official\n"
        "    model: deepseek-v4-flash\n"
        "\n"
        "- id: session-persistence-jsonl\n"
        "  config:\n"
        "    root: ./.sessions\n"
        "\n"
        "- id: tool-bash\n"
        "  config:\n"
        "    timeoutMs: 60000\n",
        encoding="utf-8",
    )
    return str(path)


def _render(template, tmp_path, **overrides):
    kwargs = {
        "model": "deepseek-v4-pro",
        "persona": "You are a sub-agent.",
        "persistence_root": str(tmp_path / "out" / "dsh-sessions"),
        "out_dir": str(tmp_path / "out"),
    }
    kwargs.update(overrides)
    return render_composition(template, **kwargs)


def _blocks(path):
    with open(path, "r", encoding="utf-8") as f:
        return {b["id"]: b for b in yaml.safe_load(f)}


class TestRenderComposition:
    def test_sets_persona_model_and_session_root(self, template, tmp_path):
        blocks = _blocks(_render(template, tmp_path))
        assert blocks["system-prompt"]["config"]["personaPrefix"] == "You are a sub-agent."
        assert blocks["acp"]["config"]["model"] == "deepseek-v4-pro"
        assert blocks["session-persistence-jsonl"]["config"]["root"] == str(
            tmp_path / "out" / "dsh-sessions")

    def test_acp_row_keeps_its_provider(self, template, tmp_path):
        """The row replaces dsh's own: without ``provider`` the profile has
        no model route at all."""
        cfg = _blocks(_render(template, tmp_path))["acp"]["config"]
        assert cfg == {"provider": "deepseek-official", "model": "deepseek-v4-pro"}

    def test_session_root_is_absolutised(self, template, tmp_path):
        """A cwd-relative root would scatter session stores, and resume needs
        the same directory every spawn."""
        out = _render(template, tmp_path, persistence_root="rel/dsh-sessions")
        root = _blocks(out)["session-persistence-jsonl"]["config"]["root"]
        assert os.path.isabs(root)
        assert root.endswith(os.path.join("rel", "dsh-sessions"))

    def test_none_leaves_the_template_defaults(self, template, tmp_path):
        blocks = _blocks(_render(template, tmp_path, model=None, persona=None))
        assert blocks["acp"]["config"]["model"] == "deepseek-v4-flash"
        assert blocks["system-prompt"]["config"]["personaPrefix"] == "You are a coding agent."

    def test_untouched_rows_survive_verbatim(self, template, tmp_path):
        assert _blocks(_render(template, tmp_path))["tool-bash"]["config"] == {
            "timeoutMs": 60000}

    def test_returns_an_existing_absolute_path(self, template, tmp_path):
        out = _render(template, tmp_path)
        assert os.path.isabs(out)
        assert os.path.isfile(out)
        assert os.path.basename(out).startswith("cordis-")
        assert out.endswith(".yml")

    def test_creates_out_dir_when_missing(self, template, tmp_path):
        out_dir = tmp_path / "nested" / "sub-agents" / "dsh"
        out = _render(template, tmp_path, out_dir=str(out_dir))
        assert os.path.dirname(out) == str(out_dir)

    def test_each_call_writes_a_distinct_file(self, template, tmp_path):
        paths = {_render(template, tmp_path) for _ in range(8)}
        assert len(paths) == 8
        assert all(os.path.isfile(p) for p in paths)

    def test_concurrent_renders_for_one_handle_do_not_collide(
        self, template, tmp_path
    ):
        """Same-slug dispatch is unrestricted across projects and sessions, so
        two renders into the same dir must not share a filename."""
        a = _render(template, tmp_path, persona="alpha")
        b = _render(template, tmp_path, persona="beta")
        assert a != b
        assert _blocks(a)["system-prompt"]["config"]["personaPrefix"] == "alpha"
        assert _blocks(b)["system-prompt"]["config"]["personaPrefix"] == "beta"

    def test_template_file_is_not_modified(self, template, tmp_path):
        before = open(template, encoding="utf-8").read()
        _render(template, tmp_path)
        assert open(template, encoding="utf-8").read() == before


class TestPersonaSafety:
    HOSTILE = (
        "You are a sub-agent.\n"
        "Rules: never say \"done\" unless it is.\n"
        "- item: value  # not a comment\n"
        "\tliteral tab & {braces} and {{model}}\n"
        "yaml: [not, a, list]\n"
        "---\n"
        "...\n"
        "trailing spaces here:   \n"
        "unicode: 中文 — em dash, emoji 🚀\n"
        "*anchor &alias |pipe >fold\n"
    )

    def test_yaml_hostile_persona_round_trips_byte_for_byte(
        self, template, tmp_path
    ):
        out = _render(template, tmp_path, persona=self.HOSTILE)
        assert _blocks(out)["system-prompt"]["config"]["personaPrefix"] == self.HOSTILE

    def test_persona_cannot_inject_a_sibling_key(self, template, tmp_path):
        attack = "hi\nmodel: attacker-model\nroot: /etc\n"
        blocks = _blocks(_render(template, tmp_path, persona=attack))
        assert blocks["system-prompt"]["config"] == {"personaPrefix": attack}
        assert blocks["acp"]["config"]["model"] == "deepseek-v4-pro"
        assert blocks["session-persistence-jsonl"]["config"]["root"] != "/etc"

    def test_persona_cannot_inject_a_new_row(self, template, tmp_path):
        attack = "hi\n- id: evil\n  name: '@evil/plugin'\n"
        out = _render(template, tmp_path, persona=attack)
        with open(out, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
        assert {b["id"] for b in loaded} == {
            "system-prompt", "acp", "session-persistence-jsonl", "tool-bash"}


class TestShippedTemplate:
    def test_shipped_dsh_template_renders(self, tmp_path):
        out = render_composition(
            SHIPPED_TEMPLATE,
            model="deepseek-v4-flash",
            persona="Orbital sub-agent prompt.",
            persistence_root=str(tmp_path / "dsh-sessions"),
            out_dir=str(tmp_path),
        )
        blocks = _blocks(out)
        assert blocks["system-prompt"]["config"]["personaPrefix"] == (
            "Orbital sub-agent prompt.")
        assert blocks["acp"]["config"] == {
            "provider": "deepseek-official", "model": "deepseek-v4-flash"}
        assert os.path.isabs(blocks["session-persistence-jsonl"]["config"]["root"])

    def test_shipped_rows_each_restate_a_whole_config(self):
        """Patch rows replace dsh's row config: a row with no ``config`` (or a
        sandbox-policy row, whose ``workspaceRoot`` is a JS expression Orbital
        cannot restate) would silently strip dsh's own settings."""
        with open(SHIPPED_TEMPLATE, "r", encoding="utf-8") as f:
            rows = yaml.safe_load(f)
        assert [r["id"] for r in rows] == [
            "system-prompt", "acp", "session-persistence-jsonl"]
        assert all(isinstance(r.get("config"), dict) and r["config"] for r in rows)
        assert all("name" not in r for r in rows), (
            "a `name` would insert a new plugin rather than patch dsh's row")


class TestRenderErrors:
    def test_missing_template_raises(self, tmp_path):
        with pytest.raises(CompositionError):
            _render(str(tmp_path / "nope.yml"), tmp_path)

    def test_non_list_template_raises(self, tmp_path):
        bad = tmp_path / "bad.yml"
        bad.write_text("id: not-a-list\n", encoding="utf-8")
        with pytest.raises(CompositionError):
            _render(str(bad), tmp_path)

    def test_row_without_config_gets_one(self, tmp_path):
        bare = tmp_path / "bare.yml"
        bare.write_text("- id: acp\n- id: system-prompt\n", encoding="utf-8")
        blocks = _blocks(_render(str(bare), tmp_path))
        assert blocks["acp"]["config"]["model"] == "deepseek-v4-pro"
        assert blocks["system-prompt"]["config"]["personaPrefix"] == "You are a sub-agent."


class TestGarbageCollection:
    def _plant(self, d, name, age_days):
        p = os.path.join(str(d), name)
        with open(p, "w", encoding="utf-8") as f:
            f.write("- id: x\n")
        old = time.time() - age_days * 86400
        os.utime(p, (old, old))
        return p

    def test_removes_stale_rendered_configs(self, tmp_path):
        stale = self._plant(tmp_path, "cordis-deadbeef.yml", 9)
        fresh = self._plant(tmp_path, "cordis-cafebabe.yml", 1)
        removed = gc_stale(str(tmp_path), max_age_days=7)
        assert removed == 1
        assert not os.path.exists(stale)
        assert os.path.exists(fresh)

    def test_leaves_unrelated_files_alone(self, tmp_path):
        keep = self._plant(tmp_path, "transcript.jsonl", 99)
        keep2 = self._plant(tmp_path, "cordis-notyaml.txt", 99)
        gc_stale(str(tmp_path), max_age_days=7)
        assert os.path.exists(keep)
        assert os.path.exists(keep2)

    def test_missing_dir_is_a_noop(self, tmp_path):
        assert gc_stale(str(tmp_path / "absent")) == 0


class TestUnlink:
    def test_removes_the_rendered_file(self, template, tmp_path):
        out = _render(template, tmp_path)
        unlink_rendered(out)
        assert not os.path.exists(out)

    def test_is_idempotent_and_tolerates_none(self, template, tmp_path):
        out = _render(template, tmp_path)
        unlink_rendered(out)
        unlink_rendered(out)
        unlink_rendered(None)
        assert not os.path.exists(out)
