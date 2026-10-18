# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 117 §3.2 — the shell tool follows the project's safe-mode switch.

Open project → ``_execute_unsandboxed`` (no provider call, no proxy variables
in the child); safe project → the provider; an executable on the project's
``unsandboxed_executables`` list runs open even in safe mode. The denial
detector names the Seatbelt line, the proxy marker and the Windows
access-denied text, and nothing on a plain non-zero exit; in safe mode a
detected denial rides ``ToolResult.meta["sandbox_denial"]``.
"""

import os
import sys
from types import SimpleNamespace

import pytest
from unittest.mock import patch

from agent_os.agent.tools import shell as shell_mod
from agent_os.agent.tools.shell import (
    SandboxDenial,
    ShellTool,
    detect_sandbox_denial,
)
from agent_os.platform.types import CommandResult


class _Provider:
    def __init__(self, exit_code=0, stdout="", stderr=""):
        self.calls: list[dict] = []
        self._result = CommandResult(exit_code=exit_code, stdout=stdout, stderr=stderr)

    def get_capabilities(self):
        return SimpleNamespace(setup_complete=True)

    async def run_command(self, **kwargs):
        self.calls.append(kwargs)
        return self._result


def _tool(tmp_path, provider, *, safe_mode=True, unsandboxed=()):
    return ShellTool(
        workspace=str(tmp_path), os_type="darwin", platform_provider=provider,
        project_id="p1",
        safe_mode_cb=lambda: safe_mode,
        unsandboxed_cb=lambda: list(unsandboxed),
    )


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="runs a real POSIX shell command through the open path")
def test_open_project_runs_unsandboxed_and_never_calls_the_provider(tmp_path):
    provider = _Provider()
    tool = _tool(tmp_path, provider, safe_mode=False)
    result = tool.execute(command="echo open")
    assert provider.calls == []
    assert "Exit code: 0" in result.content
    assert "open" in result.content


def test_safe_project_runs_through_the_provider(tmp_path):
    provider = _Provider(stdout="from provider")
    tool = _tool(tmp_path, provider, safe_mode=True)
    result = tool.execute(command="echo safe")
    assert len(provider.calls) == 1
    assert "from provider" in result.content


@pytest.mark.skipif(sys.platform == "win32", reason="runs a real POSIX shell command through the open path")
def test_listed_executable_runs_open_even_in_safe_mode(tmp_path):
    provider = _Provider(stdout="sandboxed")
    tool = _tool(tmp_path, provider, safe_mode=True, unsandboxed=["echo"])
    result = tool.execute(command="echo listed")
    assert provider.calls == []
    assert "listed" in result.content


def test_listed_executable_matches_the_first_token_only(tmp_path):
    provider = _Provider(stdout="sandboxed")
    tool = _tool(tmp_path, provider, safe_mode=True, unsandboxed=["git"])
    tool.execute(command="echo git")
    assert len(provider.calls) == 1  # "git" is an argument here, not the executable


def test_listed_executable_matches_a_path_basename_and_env_prefix(tmp_path):
    provider = _Provider(stdout="sandboxed")
    tool = _tool(tmp_path, provider, safe_mode=True, unsandboxed=["echo"])
    tool.execute(command="/bin/echo via-path")
    tool.execute(command="FOO=1 echo via-env")
    assert provider.calls == []


def test_no_callbacks_keeps_the_legacy_provider_path(tmp_path):
    """Callers that never wire the callbacks (tests, dev daemon) are unchanged."""
    provider = _Provider(stdout="legacy")
    tool = ShellTool(workspace=str(tmp_path), os_type="darwin",
                     platform_provider=provider, project_id="p1")
    tool.execute(command="echo x")
    assert len(provider.calls) == 1


def test_execute_via_subprocess_alias_still_exists(tmp_path):
    assert ShellTool._execute_via_subprocess is ShellTool._execute_unsandboxed


@pytest.mark.skipif(sys.platform == "win32", reason="runs a real POSIX shell command through the open path")
def test_unsandboxed_child_sees_no_proxy_variables(tmp_path):
    """A previous provider call may have left HTTP(S)_PROXY / NO_PROXY in the
    daemon's environment; the open path must not inherit them."""
    tool = _tool(tmp_path, _Provider(), safe_mode=False)
    with patch.dict(os.environ, {
        "HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1",
        "NO_PROXY": "localhost", "http_proxy": "http://127.0.0.1:1",
        "ORBITAL_KEEP_ME": "yes",
    }):
        result = tool.execute(
            command='echo "P=${HTTP_PROXY:-unset} S=${HTTPS_PROXY:-unset} '
                    'N=${NO_PROXY:-unset} L=${http_proxy:-unset} K=${ORBITAL_KEEP_ME:-unset}"'
        )
    assert "P=unset S=unset N=unset L=unset K=yes" in result.content


def test_open_path_never_adds_the_sandbox_hint(tmp_path):
    """Outside the sandbox an EPERM is a real OS error, not a sandbox denial."""
    tool = _tool(tmp_path, _Provider(), safe_mode=False)
    result = tool.execute(command="echo 'Operation not permitted' >&2; exit 1")
    assert "[sandbox]" not in result.content
    assert not (result.meta or {}).get("sandbox_denial")


# ---------------------------------------------------------------------------
# Denial detection
# ---------------------------------------------------------------------------


def test_detector_ignores_a_plain_non_zero_exit():
    assert detect_sandbox_denial(1, "No such file or directory") is None
    assert detect_sandbox_denial(0, "Operation not permitted") is None


def test_detector_flags_the_seatbelt_line():
    with patch.object(shell_mod, "_lookup_sandbox_denial",
                      return_value="file-read-data /Users/me/.ssh"):
        denial = detect_sandbox_denial(1, "ls: .ssh: Operation not permitted")
    assert isinstance(denial, SandboxDenial)
    assert denial.kind == "seatbelt"
    assert "/Users/me/.ssh" in denial.reason


def test_detector_flags_the_seatbelt_marker_without_a_log_hit():
    with patch.object(shell_mod, "_lookup_sandbox_denial", return_value=None):
        denial = detect_sandbox_denial(1, "Operation not permitted")
    assert denial is not None and denial.kind == "seatbelt"


def test_detector_flags_the_proxy_marker():
    denial = detect_sandbox_denial(56, "curl: (56) CONNECT tunnel failed, response 403")
    assert denial is not None and denial.kind == "proxy"


def test_detector_flags_the_windows_access_denied_text_en_and_zh():
    en = detect_sandbox_denial(1, "Access is denied.")
    zh = detect_sandbox_denial(1, "拒绝访问。")
    assert en is not None and en.kind == "windows_acl"
    assert zh is not None and zh.kind == "windows_acl"


# ---------------------------------------------------------------------------
# meta["sandbox_denial"] + the extra sentence
# ---------------------------------------------------------------------------


def test_safe_mode_denial_lands_in_meta_with_the_wait_sentence(tmp_path):
    provider = _Provider(exit_code=1, stderr="ls: /Users/me/.ssh: Operation not permitted")
    tool = _tool(tmp_path, provider, safe_mode=True)
    with patch.object(shell_mod, "_lookup_sandbox_denial",
                      return_value="file-read-data /Users/me/.ssh"):
        result = tool.execute(command="ls ~/.ssh")
    assert "[sandbox]" in result.content
    assert "wait for the user's decision" in result.content
    denial = result.meta["sandbox_denial"]
    assert denial["command"] == "ls ~/.ssh"
    assert "/Users/me/.ssh" in denial["reason"]


def test_legacy_provider_path_without_callbacks_has_no_meta_or_sentence(tmp_path):
    provider = _Provider(exit_code=1, stderr="Operation not permitted")
    tool = ShellTool(workspace=str(tmp_path), os_type="darwin",
                     platform_provider=provider, project_id="p1")
    with patch.object(shell_mod, "_lookup_sandbox_denial", return_value=None):
        result = tool.execute(command="ls ~/.ssh")
    assert "[sandbox]" in result.content
    assert "wait for the user's decision" not in result.content
    assert "sandbox_denial" not in (result.meta or {})


def test_windows_zh_denial_gets_the_hint_and_the_meta(tmp_path):
    """The hint line and the detector must agree on what counts as a denial."""
    provider = _Provider(exit_code=1, stderr="拒绝访问。")
    tool = _tool(tmp_path, provider, safe_mode=True)
    result = tool.execute(command="type C:\\secret.txt")
    assert "[sandbox]" in result.content
    assert result.meta["sandbox_denial"]["kind"] == "windows_acl"


def test_safe_mode_success_carries_no_denial(tmp_path):
    provider = _Provider(exit_code=0, stdout="ok")
    tool = _tool(tmp_path, provider, safe_mode=True)
    result = tool.execute(command="echo ok")
    assert "sandbox_denial" not in (result.meta or {})
