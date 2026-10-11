# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""A workspace whose path has a space is not "outside the workspace".

The shell tool's path scan stopped at whitespace, so for a workspace like
``C:\\Users\\me\\Documents\\My Project`` a correctly quoted
``dir "C:\\...\\My Project"`` was read as ``C:\\...\\Documents\\My``: outside.
The ``[focus]`` warning then told the agent its own project was "not project
context — do not read or explore them". Spaces in folder names are routine on
Windows ("My Project", "New folder"). Found 2026-10-11 on v0.16.0.
"""

from __future__ import annotations

import sys

import pytest

from agent_os.agent.tools.shell import ShellTool

win_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics")
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX path semantics")

WIN_WS = r"C:\Users\me\Documents\My Project"
POSIX_WS = "/Users/me/Documents/My Project"


def _external(workspace: str, command: str) -> list[str]:
    os_type = "windows" if sys.platform == "win32" else "linux"
    return ShellTool(workspace=workspace, os_type=os_type)._detect_external_paths(command)


@win_only
@pytest.mark.parametrize("command", [
    f'dir "{WIN_WS}"',
    f'type "{WIN_WS}\\notes.txt"',
    f"Get-Content '{WIN_WS}\\src\\main.py'",
    f'cd "{WIN_WS}"; git status',
    f'dir "{WIN_WS.lower()}"',              # Windows paths are case-insensitive
    f'python "{WIN_WS}\\run.py" > "{WIN_WS}\\out.txt"',
])
def test_windows_paths_inside_a_spaced_workspace_are_inside(command):
    assert _external(WIN_WS, command) == []


@win_only
@pytest.mark.parametrize("command, expected", [
    # A sibling that only shares the name prefix is still outside.
    (r'dir "C:\Users\me\Documents\My Project-old\x"', [r"C:\Users\me\Documents\My Project-old\x"]),
    (r'type "C:\Users\me\Documents\Other Folder\a.txt"', [r"C:\Users\me\Documents\Other Folder\a.txt"]),
    (r"dir D:\data\reports", [r"D:\data\reports"]),
])
def test_windows_paths_outside_are_still_reported_whole(command, expected):
    assert _external(WIN_WS, command) == expected


@posix_only
@pytest.mark.parametrize("command", [
    f'ls "{POSIX_WS}"',
    f"cat '{POSIX_WS}/notes.txt'",
])
def test_posix_paths_inside_a_spaced_workspace_are_inside(command):
    assert _external(POSIX_WS, command) == []


@posix_only
def test_posix_sibling_with_a_shared_prefix_is_outside():
    assert _external(POSIX_WS, f'ls "{POSIX_WS}-old/x"') == [f"{POSIX_WS}-old/x"]
