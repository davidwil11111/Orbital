# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""scripts/diag/first_divergence.py (spec 140 I2): renders two request dumps
in DeepSeek's prompt order and names the segment of the first differing byte."""

import importlib.util
import json
import pathlib

_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "diag" / "first_divergence.py"
_spec = importlib.util.spec_from_file_location("first_divergence", _PATH)
fd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fd)

SEP = "\n\n---\n\n"
TOOLS = [{"type": "function", "function": {"name": "read", "parameters": {}}},
         {"type": "function", "function": {"name": "write", "parameters": {}}}]


def _body(semi=("## Memory", "## Sub-agents"), l1="[DECISIONS.md — snapshot taken]\nd",
          tools=TOOLS, user="hi", mid_system=None):
    msgs = [
        {"role": "system", "content": "You are Orbital." + SEP + "## Safety"},
        {"role": "system", "content": SEP.join(semi)},
        {"role": "system", "content": "[Project Instructions]\nbe nice"},
        {"role": "system", "content": l1},
        {"role": "user", "content": user},
    ]
    if mid_system:
        msgs.append({"role": "system", "content": mid_system})
    return {"messages": msgs, "tools": tools}


def test_identical():
    r = fd.divergence(_body(), _body())
    assert r["identical"]


def test_semi_stable_section_named():
    r = fd.divergence(_body(), _body(semi=("## Memory", "## Sub-agents\nclaude running")))
    assert not r["identical"]
    assert r["segment_b"].startswith("semi_stable §1 '## Sub-agents")
    # A ends that section exactly where B keeps going: the label says so.
    assert r["segment_a"].startswith("end of semi_stable §1")


def test_tools_follow_the_first_system_message():
    """Measured on deepseek-v4.1-flash via OpenCode Go: the tools block sits
    right after the first system message, ahead of semi_stable and Layer-1,
    so a tool-set difference breaks the prefix at ~the static prefix's end."""
    other = [TOOLS[0], {"type": "function", "function": {"name": "browser", "parameters": {}}}]
    r = fd.divergence(_body(), _body(tools=other))
    assert r["segment_a"] == "tool #1 write"
    assert r["byte"] < len(("You are Orbital." + SEP + "## Safety").encode()) + 200
    r_all = fd.divergence(_body(), _body(tools=other), tools_after="all")
    assert r_all["segment_a"] == "tool #1 write"
    assert r_all["byte"] > r["byte"]


def test_mid_history_system_rows_render_in_place_by_default():
    r = fd.divergence(_body(mid_system="event A"), _body(mid_system="event B"))
    assert r["segment_a"] == "system row #4"
    hoisted = fd.divergence(_body(mid_system="event A"), _body(mid_system="event B"), hoist=True)
    assert hoisted["segment_a"] == "system row #4"
    assert r["byte"] > hoisted["byte"]


def test_layer1_labelled_and_cjk_safe():
    a = _body(l1="[DECISIONS.md — snapshot taken]\n决定甲")
    b = _body(l1="[DECISIONS.md — snapshot taken]\n决定乙")
    r = fd.divergence(a, b)
    assert r["segment_a"] == "Layer-1 DECISIONS.md"
    assert r["context_a"][1].startswith("甲")
    assert r["context_b"][1].startswith("乙")


def test_table_over_dump_dir(tmp_path, capsys):
    for stem, body in (("s1", _body()), ("s2", _body(user="yo"))):
        (tmp_path / f"{stem}_1.json").write_text(
            json.dumps({"meta": {"session_id": stem}, "body": body}), encoding="utf-8")
    fd.main(["--table", str(tmp_path)])
    out = capsys.readouterr().out
    assert "| s1 | s2 |" in out
    assert "history #4 user" in out
