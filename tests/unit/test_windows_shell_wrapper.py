# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Windows shell commands run from a script file, and denials are reported by
PowerShell itself (bench 2026-10-19, issues 3, 6, 7, 8).

* Issue 8: the command used to travel inline through
  ``cmd /c "… powershell -Command <cmd> …"``; inner double quotes were lost
  (``Write-Output "a b"`` printed two lines) and ``CreateProcessWithLogonW``
  caps the line at 1024 characters. It now runs from a UTF-8 ``.ps1``.
* Issue 3: PowerShell's own denial (``Access to the path '…' is denied.``)
  matched no marker, so most denials raised no ``[sandbox]`` line and no
  card. The script now prints ``[orbital:access-denied]`` when any error
  record is a permission denial, whatever the language or exit code.
* Issue 7: "always allow" keyed on the first word, so on Windows it often
  allowlisted ``cmd`` itself, and ``git status; <anything>`` rode along.
* Issue 6 (tool side): the ``[focus]`` warning told an *open* project's agent
  not to look outside the workspace.
"""

from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from agent_os.platform.proc_runner import read_bounded
from tests.shell_doubles import fake_shell_run
from agent_os.agent.tools.shell import (
    ACCESS_DENIED_MARKER,
    ShellTool,
    build_windows_script,
    detect_sandbox_denial,
)


# --- issue 8: the script wrapper ------------------------------------------------


def test_script_carries_the_command_verbatim():
    cmd = 'Write-Output "a b"; cmd /c "echo x> f.txt"'
    assert cmd in build_windows_script(cmd)


def test_script_keeps_the_command_exit_semantics_and_emits_the_marker():
    script = build_windows_script("Get-Item nope")
    # ``$?`` is captured on the line right after the command, before the
    # trailer's own statements can change it.
    lines = script.splitlines()
    i = lines.index("Get-Item nope")
    assert lines[i + 1].strip().startswith("$__orbital_ok = $?")
    assert ACCESS_DENIED_MARKER in script
    assert "PermissionDenied" in script and "UnauthorizedAccessException" in script
    assert "exit 1" in script


@pytest.mark.skipif(sys.platform != "win32", reason="runs Windows PowerShell")
@pytest.mark.parametrize("command,code", [
    ('python -c "raise SystemExit(7)"', 7),
    ("Get-Item C:\\orbital-no-such-path-x", 1),
    ('python -c "raise SystemExit(0)"', 0),
])
def test_script_exits_with_the_native_programs_own_code(tmp_path, command, code):
    # Batch 1026 Windows pass (D2): a job that ran `raise SystemExit(7)` was
    # reported as exit 1, since the trailer collapsed every failure to 1.
    script = tmp_path / "t.ps1"
    script.write_text(build_windows_script(command), encoding="utf-8-sig")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script)], capture_output=True, timeout=60)
    assert result.returncode == code


def test_script_writes_its_output_as_utf8_before_the_command_runs():
    # Bench 2026-10-20: redirected PowerShell 5.1 output is encoded in the
    # OEM code page (437 on an en-US system locale), so CJK arrived as ``?``.
    lines = build_windows_script("Write-Output x").splitlines()
    assert "[Console]::OutputEncoding" in lines[0] and "UTF8Encoding" in lines[0]
    assert lines.index("Write-Output x") > 0


def test_unsandboxed_output_is_decoded_as_utf8(tmp_path):
    # ``text=True`` alone decoded with the locale code page (cp1252/cp936).
    # Spec 134 A1: the output is now a file the runner reads back as UTF-8
    # (errors replaced), whatever the locale.
    tool = ShellTool(workspace=str(tmp_path), os_type="windows")
    with fake_shell_run(stdout="中文\n"):
        result = tool._execute_unsandboxed("Write-Output 中文", {"network": False, "domains": []})
    assert "中文" in result.content
    log = tmp_path / "raw.log"
    log.write_bytes("中文".encode("utf-8") + b"\xff\n")
    assert read_bounded(str(log)).text == "中文\ufffd\n"


def test_windows_build_cmd_runs_a_script_file(tmp_path):
    tool = ShellTool(workspace=str(tmp_path), os_type="windows")
    cmd, script_path = tool._build_cmd('Write-Output "a b"')
    assert cmd[0] == "powershell" and "-File" in cmd
    assert cmd[cmd.index("-File") + 1] == script_path
    # Inside the workspace, so the sandbox worker can read it.
    assert os.path.commonpath([str(tmp_path), script_path]) == str(tmp_path)
    with open(script_path, "rb") as f:
        raw = f.read()
    assert raw.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM so PowerShell 5.1 reads non-ASCII"
    assert 'Write-Output "a b"' in raw.decode("utf-8-sig")


def test_posix_build_cmd_is_unchanged(tmp_path):
    tool = ShellTool(workspace=str(tmp_path), os_type="macos")
    cmd, script_path = tool._build_cmd("echo hi")
    assert cmd == ["bash", "-c", "echo hi"] and script_path is None


def test_the_script_is_removed_after_the_run(tmp_path):
    tool = ShellTool(workspace=str(tmp_path), os_type="windows")
    seen = {}

    def on_call(cmd):
        seen["path"] = cmd[cmd.index("-File") + 1]
        assert os.path.exists(seen["path"])

    with fake_shell_run(stdout="ok\n", on_call=on_call):
        result = tool._execute_unsandboxed("Write-Output ok", {"network": False, "domains": []})
    assert "ok" in result.content
    assert not os.path.exists(seen["path"])


# --- issue 3: detection -----------------------------------------------------------


def test_the_marker_is_a_denial_even_on_exit_zero():
    # A compound command whose last statement succeeded still hit the wall.
    out = f"some output\n{ACCESS_DENIED_MARKER}\n"
    denial = detect_sandbox_denial(0, out)
    assert denial is not None and denial.kind == "windows_acl"


def test_powershell_wording_is_detected_without_the_marker():
    out = "Set-Content : Access to the path 'D:\\ws\\.git\\hooks\\pre-commit' is denied."
    denial = detect_sandbox_denial(1, out)
    assert denial is not None and denial.kind == "windows_acl"


def test_zh_powershell_wording_is_detected():
    out = "对路径“C:\\Users\\q\\.ssh”的访问被拒绝。"
    assert detect_sandbox_denial(1, out) is not None


def test_a_clean_exit_without_the_marker_is_not_a_denial():
    assert detect_sandbox_denial(0, "Access is denied. (quoted in a log line)") is None


def test_the_marker_never_reaches_the_agent(tmp_path):
    tool = ShellTool(workspace=str(tmp_path), os_type="windows")

    with fake_shell_run(returncode=1, stderr=f"denied\n{ACCESS_DENIED_MARKER}\n"):
        result = tool._execute_unsandboxed("Get-ChildItem x", {"network": False, "domains": []})
    assert ACCESS_DENIED_MARKER not in result.content


# --- issue 7: the allowlist key ---------------------------------------------------


@pytest.mark.parametrize("command, key", [
    ("git status", "git"),
    ("curl.exe -sS https://example.com", "curl.exe"),
    ("cmd /c type private\\note.txt", "type"),
    ("cmd.exe /C curl.exe -sS https://x", "curl.exe"),
    ('powershell -NoProfile -Command "git log -1"', "git"),
    ("bash -c 'npm test'", "npm"),
    ("FOO=1 git push", "git"),
    # A full path keys as its name only when PATH resolves that name to it:
    # tests/unit/test_honest_grants.py.
])
def test_allowlist_key_is_the_program_that_runs(command, key):
    assert ShellTool.executable_of(command) == key


@pytest.mark.parametrize("command", [
    "git status; Remove-Item x",
    "git status && del x",
    "git log | Out-File x",
    "git status & calc",
    "git $(Remove-Item x)",
    "git (Remove-Item x)",
    "git `whoami`",
    "git status\nRemove-Item x",
    "cmd",                         # a bare interpreter
    "powershell -File x.ps1",      # a script: not one program
    'cmd /c "a & b"',
    "",
])
def test_commands_that_are_not_one_program_have_no_key(command):
    assert ShellTool.executable_of(command) == ""


def test_an_allowlisted_program_does_not_carry_a_chained_command_out(tmp_path):
    tool = ShellTool(workspace=str(tmp_path), os_type="windows",
                     safe_mode_cb=lambda: True, unsandboxed_cb=lambda: ["git"])
    assert tool._runs_unsandboxed("git status") is True
    assert tool._runs_unsandboxed("git status; Remove-Item x") is False


# --- issue 6: the [focus] warning follows the switch -----------------------------


def test_open_projects_get_no_focus_warning(tmp_path):
    tool = ShellTool(workspace=str(tmp_path), os_type="windows",
                     safe_mode_cb=lambda: False)
    with patch.object(ShellTool, "_execute_unsandboxed",
                      return_value=__import__("agent_os.agent.tools.base", fromlist=["ToolResult"])
                      .ToolResult(content="Exit code: 0\n", meta={})):
        result = tool.execute(command="dir C:\\Users\\someone\\Desktop")
    assert "[focus]" not in result.content


def test_safe_mode_projects_keep_the_focus_warning(tmp_path):
    from agent_os.agent.tools.base import ToolResult

    tool = ShellTool(workspace=str(tmp_path), os_type="windows",
                     safe_mode_cb=lambda: True)
    with patch.object(ShellTool, "_execute_unsandboxed",
                      return_value=ToolResult(content="Exit code: 0\n", meta={})):
        result = tool.execute(command="dir C:\\Users\\someone\\Desktop")
    assert "[focus]" in result.content


# --- live: the real PowerShell (Windows only) -------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="real PowerShell")
@pytest.mark.parametrize("command, code, expect", [
    ('Write-Output "a b"', 0, "a b"),
    ("cmd /c exit 5", 5, None),          # the program's own code (batch 1026), not -Command's 1
    ("Get-Item C:\\definitely_not_here_x", 1, None),
    ("Get-Item C:\\definitely_not_here_x; Write-Output x", 0, "x"),
    ("exit 7", 7, None),
    ("Write-Output 中文", 0, "中文"),
    ('cmd /c "echo 中文测试 ®"', 0, "中文测试 ®"),
])
def test_live_script_matches_powershell_command_semantics(tmp_path, command, code, expect):
    tool = ShellTool(workspace=str(tmp_path), os_type="windows")
    result = tool._execute_unsandboxed(command, {"network": False, "domains": []})
    assert result.content.startswith(f"Exit code: {code}\n"), result.content
    if expect is not None:
        assert expect in result.content.splitlines()


@pytest.mark.skipif(sys.platform != "win32", reason="real PowerShell")
def test_live_sandboxed_wrapper_output_is_utf8(tmp_path):
    # The worker path: ``WindowsProcessManager.run_command`` redirects the
    # script's output to files through ``cmd /c`` and reads them as UTF-8.
    tool = ShellTool(workspace=str(tmp_path), os_type="windows")
    cmd, script = tool._build_cmd(
        'Write-Output "中文测试 ®"; cmd /c "echo 剪映专业版"')
    out_path = tmp_path / "out.txt"
    try:
        line = subprocess.list2cmdline(cmd)
        subprocess.run(f'cmd /c "{line} > "{out_path}" 2>&1"', cwd=str(tmp_path))
    finally:
        os.remove(script)
    out = out_path.read_text(encoding="utf-8", errors="replace").splitlines()
    assert "中文测试 ®" in out and "剪映专业版" in out


@pytest.mark.skipif(sys.platform != "win32", reason="real ACLs")
def test_live_permission_denial_emits_the_marker(tmp_path):
    from agent_os.platform.windows.acl import account_sid

    locked = tmp_path / "locked"
    locked.mkdir()
    sid = account_sid(os.environ["USERNAME"])
    subprocess.run(["icacls", str(locked), "/deny", f"*{sid}:(RD)", "/Q"],
                   check=True, capture_output=True)
    try:
        tool = ShellTool(workspace=str(tmp_path), os_type="windows")
        cmd, script = tool._build_cmd("Get-ChildItem locked; Write-Output after")
        try:
            proc = subprocess.run(cmd, cwd=str(tmp_path), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
        finally:
            os.remove(script)
        out = proc.stdout + proc.stderr
        assert ACCESS_DENIED_MARKER in out
        assert detect_sandbox_denial(proc.returncode, out) is not None
    finally:
        subprocess.run(["icacls", str(locked), "/remove:d", f"*{sid}", "/Q"], capture_output=True)
