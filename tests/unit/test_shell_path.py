# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 115 D1/D2 — capturing the user's shell PATH for the packaged daemon.

The packaged app starts with the bundle's minimal PATH, so it used to run
``$SHELL -lc 'echo $PATH'`` once at launch. A login, non-interactive shell
never reads ``~/.zshrc`` / ``~/.bashrc`` — where nvm, fnm, volta, pnpm and
``node@XX`` keg installs put their PATH line — so a CLI installed through any
of them was invisible (issue #57). The capture now adds an *interactive* login
probe with sentinels around the value, unions both, and falls back to the
login probe alone whenever the interactive one fails.

The shell is faked throughout: ``runner`` stands in for ``subprocess.run``.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from agent_os.platform import shell_path as sp

SENT = "\x1f"


def _done(stdout: str, code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr="")


def _runner(login="/login/bin:/shared", interactive="/inter/bin:/shared",
            interactive_fails=None, login_fails=None, record=None):
    """A fake ``subprocess.run`` keyed on the shell flags.

    ``*_fails`` is one of "timeout", "exit", "nosentinel", "raise"."""
    def run(cmd, **kwargs):
        if record is not None:
            record.append((list(cmd), kwargs))
        flags = cmd[1]
        is_interactive = "i" in flags
        fails = interactive_fails if is_interactive else login_fails
        value = interactive if is_interactive else login
        if fails == "timeout":
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
        if fails == "raise":
            raise FileNotFoundError(cmd[0])
        if fails == "exit":
            return _done(f"{SENT}ORBITAL_PATH={value}{SENT}", code=1)
        if fails == "nosentinel":
            return _done(value + "\n")
        return _done(f"Welcome!\n{SENT}ORBITAL_PATH={value}{SENT}\n% ")
    return run


# ---------------------------------------------------------------------------
# Sentinel parsing
# ---------------------------------------------------------------------------


def test_sentinel_parse_ignores_banner_and_prompt():
    out = f"Last login: today\n\x1b[1mhi\x1b[0m\n{SENT}ORBITAL_PATH=/a:/b{SENT}\n% \x1b[?2004h"
    assert sp.parse_sentinel_path(out) == "/a:/b"


def test_sentinel_parse_returns_none_without_both_sentinels():
    assert sp.parse_sentinel_path("/a:/b\n") is None
    assert sp.parse_sentinel_path(f"{SENT}ORBITAL_PATH=/a:/b") is None
    assert sp.parse_sentinel_path("") is None


def test_sentinel_parse_keeps_an_empty_path_distinct_from_missing():
    assert sp.parse_sentinel_path(f"{SENT}ORBITAL_PATH={SENT}") == ""


# ---------------------------------------------------------------------------
# The two probes
# ---------------------------------------------------------------------------


def test_capture_runs_login_and_interactive_probes_with_the_right_shape():
    record: list = []
    cap = sp.capture_shell_path(shell="/bin/zsh", environ={"PATH": "/x"},
                                runner=_runner(record=record), platform="darwin")
    assert cap.login == ["/login/bin", "/shared"]
    assert cap.interactive == ["/inter/bin", "/shared"]
    flags = sorted(cmd[1] for cmd, _ in record)
    assert flags == ["-ilc", "-lc"]
    for cmd, kwargs in record:
        assert cmd[0] == "/bin/zsh"
        assert "ORBITAL_PATH=" in cmd[2] and "$PATH" in cmd[2]
        # Never block on a prompt, never let an rc file hang launch, and give
        # prompt code a terminal it will not decorate.
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["timeout"] == sp.PROBE_TIMEOUT
        assert kwargs["env"]["TERM"] == "dumb"
        assert kwargs["encoding"] == "utf-8" and kwargs["errors"] == "replace"


@pytest.mark.parametrize("failure", ["timeout", "exit", "nosentinel", "raise"])
def test_interactive_failure_falls_back_to_the_login_probe(failure):
    cap = sp.capture_shell_path(shell="/bin/zsh", environ={"PATH": "/x"},
                                runner=_runner(interactive_fails=failure),
                                platform="darwin")
    assert cap.interactive == []
    assert cap.interactive_error is not None
    assert cap.login == ["/login/bin", "/shared"]
    # Worst case is exactly today's behaviour: the login PATH.
    assert cap.union(["/x"]) == ["/login/bin", "/shared", "/x"]


def test_both_probes_failing_leaves_the_current_path_alone():
    environ = {"PATH": "/x:/y", "SHELL": "/bin/zsh"}
    cap = sp.merge_shell_path_into_environ(
        environ=environ, runner=_runner(interactive_fails="raise", login_fails="raise"),
        platform="darwin")
    assert environ["PATH"] == "/x:/y"
    assert cap.added == 0


def test_shell_defaults_to_environ_shell_then_bash():
    record: list = []
    sp.capture_shell_path(environ={"SHELL": "/opt/fish", "PATH": ""},
                          runner=_runner(record=record), platform="darwin")
    assert {cmd[0] for cmd, _ in record} == {"/opt/fish"}
    record.clear()
    sp.capture_shell_path(environ={"PATH": ""}, runner=_runner(record=record),
                          platform="darwin")
    assert {cmd[0] for cmd, _ in record} == {"/bin/bash"}


# ---------------------------------------------------------------------------
# Union order and the merge into the environment
# ---------------------------------------------------------------------------


def test_union_is_interactive_then_login_then_existing_without_duplicates():
    # A forced darwin platform: PATH is ':'-separated whatever the CI host.
    environ = {"PATH": ":".join(["/shared", "/exist/bin"]), "SHELL": "/bin/zsh"}
    cap = sp.merge_shell_path_into_environ(
        environ=environ, runner=_runner(login="/login/bin:/shared",
                                        interactive="/inter/bin:/shared:/exist/bin"),
        platform="darwin")
    assert environ["PATH"].split(":") == [
        "/inter/bin", "/shared", "/exist/bin", "/login/bin",
    ]
    assert cap.added == 2  # /inter/bin and /login/bin were new


def test_merge_drops_empty_entries():
    environ = {"PATH": "", "SHELL": "/bin/zsh"}
    sp.merge_shell_path_into_environ(
        environ=environ, runner=_runner(login="/a::/b:", interactive=""),
        platform="darwin")
    assert environ["PATH"].split(":") == ["/a", "/b"]


def test_windows_short_circuit_never_spawns_a_shell():
    record: list = []
    environ = {"PATH": r"C:\Windows", "SHELL": "/bin/bash"}
    cap = sp.merge_shell_path_into_environ(environ=environ, runner=_runner(record=record),
                                           platform="win32")
    assert record == []
    assert environ["PATH"] == r"C:\Windows"
    assert cap.login == [] and cap.interactive == [] and cap.added == 0


def test_merge_logs_what_each_probe_contributed(caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="agent_os.platform.shell_path"):
        sp.merge_shell_path_into_environ(
            environ={"PATH": "/x", "SHELL": "/bin/zsh"},
            runner=_runner(interactive_fails="timeout"), platform="darwin")
    text = " ".join(r.getMessage() for r in caplog.records)
    # The one line support will ask for: did the interactive probe work?
    assert "login" in text and "interactive" in text
    assert "timed out" in text or "timeout" in text
