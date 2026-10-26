# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""A stand-in for the shell tool's process runner (spec 134 A1).

The shell tool no longer calls ``subprocess.run``; it calls
``agent_os.platform.proc_runner.run_foreground``, which writes the command's
output to a log file. ``fake_shell_run`` patches that one call: the fake
writes the canned output to a real log file and hands it to the real
``collect_result``, so the bounded read-back and truncation are exercised for
real while no process is started.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from agent_os.platform.proc_runner import collect_result, new_log_path


class FakeRun:
    """Records each call; ``calls[-1]["argv"]`` is the argv the tool built."""

    def __init__(self, returncode=0, stdout="", stderr="", timed_out=False,
                 side_effect=None, on_call=None):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.side_effect = side_effect
        self.on_call = on_call
        self.calls: list[dict] = []

    @property
    def called(self) -> bool:
        return bool(self.calls)

    def __call__(self, argv, *, cwd, timeout, env=None, log_dir=None, keep_dir=None):
        self.calls.append({"argv": argv, "cwd": cwd, "timeout": timeout,
                           "env": env, "log_dir": log_dir, "keep_dir": keep_dir})
        if self.on_call is not None:
            self.on_call(argv)
        if self.side_effect is not None:
            raise self.side_effect
        path = new_log_path(log_dir)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write((self.stdout or "") + (self.stderr or ""))
        return collect_result(path, exit_code=self.returncode,
                              timed_out=self.timed_out, keep_dir=keep_dir)


@contextmanager
def fake_shell_run(**kwargs):
    fake = FakeRun(**kwargs)
    with patch("agent_os.agent.tools.shell.run_foreground", side_effect=fake):
        yield fake
