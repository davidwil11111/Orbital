# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Structured failure diagnostics for the login / dispatch events (spec 113).

The direction revision of 2026-10-18 raised the bar from "count the failures"
to "locate and fix a user's problem from our own data, without asking for
logs" — while keeping the schema principle: no free text, no URLs, no CLI
output leaves the machine. Everything here is therefore an **enum or a
number**. The one open-ended value, ``cli_version``, is accepted only when it
parses as a dotted version; anything else is dropped to ``None``.

A failure event (``login_failed`` / ``login_unverified`` / ``update_failed``
/ ``subagent_failed``) carries, beyond ``agent`` and ``reason``:

- ``exit_code``        int | None — the CLI's return code when there was one
- ``cli_version``      "2.1.100" | None — ``claude --version``'s number
- ``install_kind``     npm | native | unknown — judged from the resolved path
- ``duration_ms``      int | None — how long the failing step took
- ``error_class``      exception class name from a fixed set, or ``other``
- ``stderr_category``  what the CLI said, mapped through fixed regexes

The verification bar for these fields is the 2026-10-18 Windows report (spec
116 §0): ``subagent_failed{agent: claude-code, reason: update_needed,
cli_version: "2.1.100", install_kind: "npm"}`` reads as "CLI 2.1.100 → no
claude.exe → below the 2.1.114 floor" with no log in hand.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Enums (the ONLY values that may appear in the transmitted maps/lists)
# ---------------------------------------------------------------------------

# Why a CLI login job (``_run_login_job``) ended without a confirmed login.
# ``unverified`` is tracked separately (``login_unverified``) and is NOT a
# login failure (113 A1, decision P2); it is still listed here because the
# update kind reuses the set, where an exit-0 below the floor IS a failure.
LOGIN_FAILURE_REASONS = ("spawn_error", "timeout", "nonzero_exit", "unverified", "unknown")

# Why a sub-agent dispatch failed (113 A2 + A6, 116 D5).
SUBAGENT_FAILURE_REASONS = (
    "spawn", "runtime_error", "adapter_broken", "not_authenticated",
    "update_needed", "unknown",
)

INSTALL_KINDS = ("npm", "native", "unknown")

STDERR_CATEGORIES = (
    "unknown_command", "not_logged_in", "network", "timeout", "batch_refused",
    "other",
)

# Exception class names worth telling apart; anything else is ``other``.
ERROR_CLASSES = (
    "TimeoutExpired", "TimeoutError", "FileNotFoundError", "PermissionError",
    "OSError", "JSONDecodeError", "ValueError", "RuntimeError",
    "CLIConnectionError", "CLINotFoundError", "ProcessError",
    "CancelledError", "other",
)

# Events whose rows are listed verbatim (sanitised) in ``recent_failures``.
FAILURE_EVENTS = ("login_failed", "login_unverified", "update_failed", "subagent_failed")

RECENT_FAILURES_LIMIT = 20

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

# Order matters: the first match wins, and the specific shapes come before
# the broad ones (a batch-script refusal also contains "script", a timeout
# message may mention the network).
_STDERR_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("batch_refused", re.compile(r"refusing to execute batch script", re.I)),
    ("timeout", re.compile(r"timed?\s*out|TimeoutExpired|ETIMEDOUT", re.I)),
    ("not_logged_in", re.compile(
        r"not (logged|signed) in|not authenticated|please (log|sign) in|"
        r"login required|unauthori[sz]ed|\b401\b|auth(entication)? (failed|required|error)|"
        r"loggedIn\W{0,3}false|is installed but not signed in|missing [A-Z0-9_]+ ",
        re.I)),
    ("unknown_command", re.compile(
        r"unknown (command|option|argument|subcommand)|unrecognized (command|option)|"
        r"not a valid (command|option)|is not recognized as an internal|command not found|"
        r"no such (command|option)|native binary not installed|No such file or directory",
        re.I)),
    ("network", re.compile(
        r"ECONNREFUSED|ECONNRESET|ENOTFOUND|EAI_AGAIN|EPIPE|getaddrinfo|"
        r"network|proxy|fetch failed|socket hang up|certificate|SSL|TLS|"
        r"unreachable|connection (refused|reset|closed)",
        re.I)),
)


# ---------------------------------------------------------------------------
# Classifiers
# ---------------------------------------------------------------------------

def classify_stderr(text: str | None) -> str:
    """Map CLI/exception text to one of ``STDERR_CATEGORIES``. The text itself
    never leaves this function."""
    if not text or not isinstance(text, str):
        return "other"
    for category, pattern in _STDERR_RULES:
        if pattern.search(text):
            return category
    return "other"


def error_class_of(exc: BaseException | str | None) -> str | None:
    """The exception's class name if it is one we enumerate, else ``other``.
    None in → None out (no exception on this path)."""
    if exc is None:
        return None
    name = exc if isinstance(exc, str) else type(exc).__name__
    return name if name in ERROR_CLASSES else "other"


def install_kind_of(binary_path: str | None) -> str:
    """npm | native | unknown, judged from the resolved binary path alone.

    npm-flavoured layouts: a ``node_modules`` tree, an npm prefix (``npm/``,
    ``.npm-global``, ``.npm``), or Claude Code's ``~/.claude/local`` (an
    npm install under the hood). Native: the native installer's
    ``~/.local/bin`` or a bare ``.exe`` outside node_modules. Everything else
    (Homebrew, a custom path) is ``unknown``.
    """
    if not binary_path:
        return "unknown"
    normalised = binary_path.replace("\\", "/").lower()
    if ("node_modules" in normalised or "/npm/" in normalised
            or ".npm-global" in normalised or "/.npm/" in normalised
            or ".claude/local" in normalised or normalised.endswith(".cmd")):
        return "npm"
    if "/.local/bin/" in normalised or normalised.endswith(".exe"):
        return "native"
    return "unknown"


def clean_version(raw: str | None) -> str | None:
    """A dotted version number or None — never the raw ``--version`` line."""
    if not raw:
        return None
    match = re.search(r"(\d+\.\d+\.\d+)", str(raw))
    return match.group(1) if match else None


def _int_or_none(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sanitize_failure_row(row: dict, *, agent_bucket) -> dict:
    """A spool row → the bounded, enum-only shape listed in ``recent_failures``.

    Every field is validated against its enum (or parsed as a number); an
    unexpected value degrades to ``unknown``/``other``/``None`` rather than
    leaking. ``agent_bucket`` is the rollup's slug allowlist function.
    """
    event = str(row.get("event") or "")
    reasons = LOGIN_FAILURE_REASONS if event.startswith(("login_", "update_")) \
        else SUBAGENT_FAILURE_REASONS
    reason = row.get("reason")
    error_class = row.get("error_class")
    stderr_category = row.get("stderr_category")
    install_kind = row.get("install_kind")
    cli_version = clean_version(row.get("cli_version"))
    if cli_version is not None and not _VERSION_RE.match(cli_version):
        cli_version = None
    return {
        "ts": str(row.get("ts") or ""),
        "event": event,
        "agent": agent_bucket(row.get("agent")),
        "reason": reason if reason in reasons else "unknown",
        "exit_code": _int_or_none(row.get("exit_code")),
        "cli_version": cli_version,
        "install_kind": install_kind if install_kind in INSTALL_KINDS else "unknown",
        "duration_ms": _int_or_none(row.get("duration_ms")),
        "error_class": (error_class if error_class in ERROR_CLASSES
                        else ("other" if error_class else None)),
        "stderr_category": (stderr_category if stderr_category in STDERR_CATEGORIES
                            else "other"),
    }
