# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Capture the user's shell PATH for the packaged daemon (spec 115 D1/D2).

A PyInstaller bundle launched from Finder inherits launchd's minimal PATH, so
nothing the user installed (Homebrew, npm globals, ``~/.local/bin``) is
visible to ``shutil.which``. The app used to run ``$SHELL -lc 'echo $PATH'``
once at launch. ``-l`` without ``-i`` is a *login, non-interactive* shell: zsh
reads ``.zshenv`` / ``.zprofile`` / ``.zlogin`` but never ``.zshrc``, bash
reads ``.bash_profile`` but never ``.bashrc`` — and ``.zshrc`` is exactly where
nvm, fnm, volta, pnpm and ``node@XX`` keg installs document putting their PATH
line. A CLI installed through any of them was never detected (issue #57).

Two probes now run, in parallel, each bounded by :data:`PROBE_TIMEOUT`:

* the login probe (``-lc``), as before — cheap, never prompts;
* an interactive login probe (``-ilc``) with ``stdin`` closed and
  ``TERM=dumb``, whose output is read from between ``\\x1f`` sentinels so a
  banner, an ``echo`` or prompt escape codes in an rc file cannot corrupt the
  value.

The union is interactive entries first (the superset the user sees in
Terminal), then login, then whatever PATH the process already had, deduped.
Any failure of the interactive probe — timeout because an rc file blocks,
``exec tmux``, non-zero exit, no sentinel — falls back to the login result,
so the worst case is the previous behaviour. This is the approach VS Code's
shell environment resolution has used for years. The dev daemon (uvicorn
from a terminal) already has the full PATH and is unaffected either way.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger("agent_os.platform.shell_path")

#: Per-probe budget. A hostile rc file (``sleep 30``) must not delay launch.
PROBE_TIMEOUT = 5.0

#: Unit separator — never printed by a sane prompt or banner.
SENTINEL = "\x1f"
_MARKER = "ORBITAL_PATH="
#: ``printf`` with an octal escape is POSIX; ``\\x1f`` is not (dash lacks it).
PROBE_COMMAND = "printf '\\037" + _MARKER + "%s\\037' \"$PATH\""

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass
class ShellPathCapture:
    """What the two probes returned, plus what the merge did with it."""

    login: list[str] = field(default_factory=list)
    interactive: list[str] = field(default_factory=list)
    login_error: str | None = None
    interactive_error: str | None = None
    #: Entries the merge added to the process PATH (0 until merged).
    added: int = 0

    def union(self, existing: list[str]) -> list[str]:
        """Interactive first, then login, then *existing*, no duplicates."""
        seen: set[str] = set()
        merged: list[str] = []
        for entry in [*self.interactive, *self.login, *existing]:
            if entry and entry not in seen:
                seen.add(entry)
                merged.append(entry)
        return merged


def parse_sentinel_path(stdout: str) -> str | None:
    """The PATH between the sentinels, or None when they are not both there.

    An empty PATH between intact sentinels is ``""``, not None: the probe
    ran, it just had nothing to say.
    """
    head = SENTINEL + _MARKER
    start = stdout.find(head)
    if start < 0:
        return None
    start += len(head)
    end = stdout.find(SENTINEL, start)
    if end < 0:
        return None
    return stdout[start:end]


def split_path(value: str) -> list[str]:
    # The probes run a POSIX shell, whose PATH is ':'-separated regardless
    # of the host (and the existing PATH is only merged on POSIX hosts, where
    # os.pathsep is ':' anyway). Splitting on os.pathsep broke the parser's
    # unit tests on Windows CI, where it is ';'.
    return [p for p in value.split(":") if p]


def _run_probe(shell: str, flags: str, environ: dict, timeout: float,
               runner: Runner) -> tuple[list[str], str | None]:
    env = dict(environ)
    # Prompt code decorates a "real" terminal; a dumb one gets plain output.
    env["TERM"] = "dumb"
    try:
        result = runner(
            [shell, flags, PROBE_COMMAND],
            capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL, env=env,
            # Decode-safe (test_subprocess_encoding): a non-UTF-8 byte in a
            # banner must not raise, it must be replaced.
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return [], f"timed out after {timeout:g}s"
    except Exception as exc:  # missing shell, EACCES, ...
        return [], f"{type(exc).__name__}: {exc}"
    value = parse_sentinel_path(result.stdout or "")
    if result.returncode != 0:
        return [], f"exit {result.returncode}"
    if value is None:
        return [], "no sentinel in output"
    return split_path(value), None


def capture_shell_path(
    shell: str | None = None,
    environ: dict | None = None,
    timeout: float = PROBE_TIMEOUT,
    runner: Runner | None = None,
    platform: str | None = None,
) -> ShellPathCapture:
    """Run both probes and return their entries (pure: nothing is merged).

    Windows is a short-circuit: PATH there comes from the registry and is
    already correct for a packaged app.
    """
    platform = platform or sys.platform
    if platform == "win32":
        return ShellPathCapture()
    env = os.environ if environ is None else environ
    shell = shell or env.get("SHELL") or "/bin/bash"
    run = runner or subprocess.run

    with ThreadPoolExecutor(max_workers=2) as pool:
        login_future = pool.submit(_run_probe, shell, "-lc", env, timeout, run)
        inter_future = pool.submit(_run_probe, shell, "-ilc", env, timeout, run)
        login, login_error = login_future.result()
        interactive, interactive_error = inter_future.result()
    return ShellPathCapture(login=login, interactive=interactive,
                            login_error=login_error,
                            interactive_error=interactive_error)


def merge_shell_path_into_environ(
    environ: dict | None = None,
    shell: str | None = None,
    timeout: float = PROBE_TIMEOUT,
    runner: Runner | None = None,
    platform: str | None = None,
) -> ShellPathCapture:
    """Capture and merge into ``environ["PATH"]`` (``os.environ`` by default).

    Called once at launch and again on Settings → Refresh, so a CLI installed
    while Orbital is running appears without a relaunch. Never raises and
    never blocks longer than :data:`PROBE_TIMEOUT`: a failed probe leaves the
    PATH as it was.
    """
    env = os.environ if environ is None else environ
    capture = capture_shell_path(shell=shell, environ=env, timeout=timeout,
                                 runner=runner, platform=platform)
    if (platform or sys.platform) == "win32":
        return capture
    existing = split_path(env.get("PATH", ""))
    merged = capture.union(existing)
    capture.added = len(set(merged) - set(existing))
    if merged != existing:
        env["PATH"] = os.pathsep.join(merged)
    # The one line support will ask for: did the interactive probe work?
    logger.info(
        "Shell PATH capture (%s): login probe %d entries%s; interactive probe "
        "%d entries%s; %d new entries merged",
        shell or env.get("SHELL") or "/bin/bash",
        len(capture.login),
        f" ({capture.login_error})" if capture.login_error else "",
        len(capture.interactive),
        f" ({capture.interactive_error})" if capture.interactive_error else "",
        capture.added,
    )
    return capture
