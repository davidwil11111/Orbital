# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""What the user grants on the approval card is what runs (spec 117 D5).

Two ways the grant and the run could differ (review of the 1018 bench fixes):

* "Always allow ``ls``" was keyed on the program's *name*, so ``./evil/ls``,
  ``PATH=./evil:$PATH ls`` and ``./evil/bash -c ls`` all counted as ``ls`` and
  ran a program the agent wrote, as the user, with no card. The key is now the
  program that actually runs; a real ``ls`` / ``/usr/bin/git`` keys exactly as
  before, so nothing the user allowed asks again.
* On Windows a command run as the user (allow once, always allowed, open
  project) was executed from a ``.ps1`` in the workspace, which the sandboxed
  worker can rewrite between the write and PowerShell reading it. Those runs
  now use the user's own temp folder, which the worker cannot open.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from unittest.mock import patch

import pytest

from agent_os.agent.tools.shell import ShellTool
from tests.shell_doubles import fake_shell_run

_WHICH = "agent_os.agent.tools.shell.shutil.which"


# --- the always-allow key names the program that runs ----------------------------


@pytest.mark.parametrize("command, key", [
    ("./evil/ls", "./evil/ls"),
    ("./evil/ls -la ~/.ssh", "./evil/ls"),
    ("bin/git status", "bin/git"),
    (".\\tools\\curl.exe -sS https://x", ".\\tools\\curl.exe"),
    ("bash -c './evil/ls'", "./evil/ls"),
    ("cmd /c .\\evil\\type.exe x", ".\\evil\\type.exe"),
])
def test_a_program_named_by_path_keys_as_that_path(command, key):
    assert ShellTool.executable_of(command) == key


@pytest.mark.parametrize("command", [
    "PATH=./evil:$PATH ls",
    "path=./evil ls",
    "LD_PRELOAD=./x.so git status",
    "DYLD_INSERT_LIBRARIES=./x.dylib git status",
    "bash -c 'PATH=./evil ls'",
])
def test_a_prefix_that_changes_which_program_runs_gets_no_key(command):
    assert ShellTool.executable_of(command) == ""


def test_an_ordinary_prefix_still_keys_on_the_program():
    assert ShellTool.executable_of("FOO=1 GIT_TRACE=1 git push") == "git"


def _abs(*parts: str) -> str:
    """An absolute path on whatever OS runs the test (``/usr/bin/git`` is not
    absolute on Windows, where the key rightly stays the path as written)."""
    return os.path.join(os.path.abspath(os.sep), *parts)


def test_an_absolute_path_to_the_program_path_runs_keys_as_its_name():
    git = _abs("usr", "bin", "git")
    with patch(_WHICH, return_value=git):
        assert ShellTool.executable_of(f'"{git}" status') == "git"
    with patch(_WHICH, return_value="C:\\Program Files\\Git\\cmd\\git.exe"):
        assert ShellTool.executable_of(
            '"C:\\Program Files\\Git\\cmd\\git.exe" status') == "git.exe"


def test_an_absolute_path_to_another_program_keys_as_the_path():
    evil, tool = _abs("tmp", "evil", "git"), _abs("opt", "tool", "run")
    with patch(_WHICH, return_value=_abs("usr", "bin", "git")):
        assert ShellTool.executable_of(f'"{evil}" status') == evil
    with patch(_WHICH, return_value=None):
        assert ShellTool.executable_of(f'"{tool}" x') == tool


def test_a_lookalike_interpreter_is_not_unwrapped():
    # ``./evil/bash`` is the agent's program, not bash: what it runs is
    # irrelevant, the key is the program itself.
    assert ShellTool.executable_of("./evil/bash -c 'ls'") == "./evil/bash"
    bash, evil = _abs("bin", "bash"), _abs("tmp", "evil", "bash")
    with patch(_WHICH, return_value=bash):
        assert ShellTool.executable_of(f'"{evil}" -c ls') == evil
        assert ShellTool.executable_of(f'"{bash}" -c "npm test"') == "npm"


def test_allowing_ls_does_not_allow_a_program_called_ls(tmp_path):
    tool = ShellTool(workspace=str(tmp_path), os_type="macos",
                     safe_mode_cb=lambda: True, unsandboxed_cb=lambda: ["ls"])
    assert tool._runs_unsandboxed("ls -la ~/.ssh") is True
    assert tool._runs_unsandboxed("./evil/ls -la ~/.ssh") is False
    assert tool._runs_unsandboxed("PATH=./evil:$PATH ls") is False
    assert tool._runs_unsandboxed("./evil/bash -c ls") is False


# --- Windows: runs as the user read their script from the user's temp ------------


def test_a_run_as_the_user_reads_its_script_from_the_user_temp(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    tool = ShellTool(workspace=str(workspace), os_type="windows")
    seen = {}

    def on_call(cmd):
        seen["path"] = cmd[cmd.index("-File") + 1]
        with open(seen["path"], encoding="utf-8-sig") as f:
            seen["text"] = f.read()

    with fake_shell_run(stdout="ok\n", on_call=on_call):
        tool._execute_unsandboxed('Write-Output "a b"', {"network": False, "domains": []})

    path = os.path.realpath(seen["path"])
    assert os.path.commonpath([os.path.realpath(str(workspace)), path]) \
        != os.path.realpath(str(workspace)), "not in the worker-writable workspace"
    assert os.path.commonpath([os.path.realpath(tempfile.gettempdir()), path]) \
        == os.path.realpath(tempfile.gettempdir())
    assert 'Write-Output "a b"' in seen["text"]
    assert not os.path.exists(seen["path"])
    assert not os.path.exists(os.path.join(str(workspace), "orbital", ".tmp")), \
        "a run as the user writes nothing into the workspace"


def test_a_sandboxed_run_keeps_its_script_in_the_workspace(tmp_path):
    # The worker must be able to read it; there it gains nothing by editing it.
    tool = ShellTool(workspace=str(tmp_path), os_type="windows")
    cmd, script = tool._build_cmd("Write-Output x")
    try:
        assert os.path.commonpath([str(tmp_path), script]) == str(tmp_path)
    finally:
        os.remove(script)
