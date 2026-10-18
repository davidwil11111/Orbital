# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 117 §3.4 — the daemon read tools follow the safe-mode switch.

``resolve_safe_read(roots, path, open_reads=True)`` returns the realpath of
any absolute path without the containment check; relative paths still resolve
inside the primary workspace; the default is byte-identical to today. read,
grep and glob honour an ``open_reads`` callback and say so in their
descriptions; write/edit (``resolve_safe``) are untouched.
"""

import os
import sys

import pytest

from agent_os.agent.tools._path_utils import resolve_safe, resolve_safe_read
from agent_os.agent.tools.glob_tool import GlobTool
from agent_os.agent.tools.grep_tool import GrepTool
from agent_os.agent.tools.read import ReadTool

OPEN_TEXT = "Absolute paths anywhere on this computer are allowed"


# ---------------------------------------------------------------------------
# resolve_safe_read
# ---------------------------------------------------------------------------


def test_open_reads_returns_realpath_of_an_absolute_outside_path(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    ws = tmp_path / "ws"
    ws.mkdir()
    assert resolve_safe_read([str(ws)], str(outside), open_reads=True) == os.path.realpath(outside)


def test_open_reads_still_confines_relative_escapes(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    assert resolve_safe_read([str(ws)], "../outside.txt", open_reads=True) is None
    assert resolve_safe_read([str(ws)], "inner/a.py", open_reads=True) == os.path.realpath(
        ws / "inner" / "a.py")


def test_default_is_unchanged(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    assert resolve_safe_read([str(ws)], str(tmp_path / "outside.txt")) is None
    assert resolve_safe_read([str(ws)], str(tmp_path / "outside.txt"), open_reads=False) is None


def test_resolve_safe_for_writes_has_no_open_mode(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    assert resolve_safe(str(ws), str(tmp_path / "outside.txt")) is None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@pytest.fixture
def layout(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "in.txt").write_text("needle in workspace\n")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "out.txt").write_text("needle outside\n")
    return ws, outside


def test_read_tool_open_reads_absolute_outside_path(layout):
    ws, outside = layout
    tool = ReadTool(workspace=str(ws), open_reads=lambda: True)
    result = tool.execute(path=str(outside / "out.txt"))
    assert "needle outside" in result.content


def test_read_tool_safe_mode_still_rejects_outside(layout):
    ws, outside = layout
    tool = ReadTool(workspace=str(ws), open_reads=lambda: False)
    result = tool.execute(path=str(outside / "out.txt"))
    assert "outside workspace" in result.content


def test_read_tool_mode_is_read_per_call(layout):
    ws, outside = layout
    state = {"open": False}
    tool = ReadTool(workspace=str(ws), open_reads=lambda: state["open"])
    assert "outside workspace" in tool.execute(path=str(outside / "out.txt")).content
    state["open"] = True
    assert "needle outside" in tool.execute(path=str(outside / "out.txt")).content


def test_read_tool_description_follows_the_mode(layout):
    ws, _ = layout
    state = {"open": True}
    tool = ReadTool(workspace=str(ws), open_reads=lambda: state["open"])
    assert OPEN_TEXT in tool.schema()["function"]["description"]
    state["open"] = False
    assert tool.schema()["function"]["description"] == (
        "Read a file or list a directory within the workspace.")


@pytest.mark.skipif(sys.platform == "win32", reason="ripgrep layout differs")
def test_grep_tool_open_reads_absolute_outside_path(layout):
    ws, outside = layout
    tool = GrepTool(workspace=str(ws), open_reads=lambda: True)
    result = tool.execute(pattern="needle", path=str(outside))
    assert "needle outside" in result.content
    # The default root list is unchanged: an unscoped search stays in the workspace.
    default = tool.execute(pattern="needle")
    assert "needle in workspace" in default.content
    assert "needle outside" not in default.content


@pytest.mark.skipif(sys.platform == "win32", reason="ripgrep layout differs")
def test_grep_tool_safe_mode_still_rejects_outside(layout):
    ws, outside = layout
    tool = GrepTool(workspace=str(ws), open_reads=lambda: False)
    assert "outside workspace" in tool.execute(pattern="needle", path=str(outside)).content


def test_glob_tool_open_reads_absolute_outside_path(layout):
    ws, outside = layout
    tool = GlobTool(workspace=str(ws), open_reads=lambda: True)
    result = tool.execute(pattern="*.txt", path=str(outside))
    assert "out.txt" in result.content
    default = tool.execute(pattern="*.txt")
    assert "in.txt" in default.content and "out.txt" not in default.content


def test_glob_tool_safe_mode_still_rejects_outside(layout):
    ws, outside = layout
    tool = GlobTool(workspace=str(ws), open_reads=lambda: False)
    assert "outside workspace" in tool.execute(pattern="*.txt", path=str(outside)).content


def test_grep_and_glob_descriptions_follow_the_mode(layout):
    ws, _ = layout
    for cls in (GrepTool, GlobTool):
        state = {"open": True}
        tool = cls(workspace=str(ws), open_reads=lambda: state["open"])
        assert OPEN_TEXT in tool.schema()["function"]["description"]
        state["open"] = False
        assert OPEN_TEXT not in tool.schema()["function"]["description"]


def test_tools_without_the_callback_are_byte_identical(layout):
    ws, _ = layout
    assert ReadTool(workspace=str(ws)).description == (
        "Read a file or list a directory within the workspace.")
    assert OPEN_TEXT not in GrepTool(workspace=str(ws)).description
    assert OPEN_TEXT not in GlobTool(workspace=str(ws)).description
