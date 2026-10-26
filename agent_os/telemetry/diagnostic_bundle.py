# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Diagnostic bundle for "Send feedback" (spec 113 part D).

The daily telemetry ping carries counters and enums only. When that is not
enough to see why a user's install fails, the user can send a bundle: a
sanitised text document the daemon assembles from local state, shown to the
user in full before anything leaves the machine. There is no silent path —
``build_bundle`` is called by the preview route and again by the send route,
and nothing else.

What goes in (and nothing else): Orbital version, OS, the sub-agent status the
settings page shows, the recent telemetry failure rows if the spool has them,
the tail of ``daemon.log``, the desktop app's ``orbital-stderr.log``, the
stall detector's ``hang.log`` (spec 134 A3), the failure context the UI was
opened from, and the install id the ping sender
uses. Never a workspace file, a session transcript, or a project path on
purpose; the sanitiser catches the ones that leak through log lines.

Sanitisation is a pure function over the whole rendered text, so every
section gets the same rules and a planted secret in any of them is caught
by the same tests (``tests/unit/test_diagnostic_bundle.py``).
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .spool import Spool

logger = logging.getLogger(__name__)

DAEMON_LOG_TAIL_BYTES = 200 * 1024
# The server rejects bodies above 1 MB (413); the three log tails together
# stay well under it. The stderr log is appended across launches (size-capped
# by the desktop app, spec 134 A4), so its tail is what matters.
STDERR_LOG_MAX_BYTES = 300 * 1024
# Spec 134 A3: the stall detector's thread dumps. Newest at the end.
HANG_LOG_TAIL_BYTES = 300 * 1024
MAX_UPLOAD_BYTES = 1024 * 1024
MAX_MESSAGE_CHARS = 4000
RECENT_FAILURES_MAX = 20
FAILURE_EVENTS = ("login_failed", "login_unverified", "subagent_failed")

REDACTED = "<redacted>"


# ---------------------------------------------------------------------------
# sanitiser
# ---------------------------------------------------------------------------

# A URL's query (and anything after it) — login output carries OAuth
# ``code=``/``state=`` here. The scheme + host + path survive so a reader still
# knows which endpoint was involved.
_URL_QUERY_RE = re.compile(r"(https?://[^\s\"'<>?#]+)\?[^\s\"'<>]+")
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+")
# Vendor-prefixed keys: OpenAI/Anthropic/DeepSeek/etc. ``sk-…``, GitHub
# ``ghp_``/``gho_``, Slack ``xox?-``, AWS ``AKIA…``.
_PREFIXED_KEY_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:sk-[A-Za-z0-9_-]{6,}|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[A-Z0-9]{16})"
)
_LONG_HEX_RE = re.compile(r"(?<![A-Za-z0-9])[A-Fa-f0-9]{32,}(?![A-Za-z0-9])")
# base64 / urlsafe-base64 shapes of 32+ chars with at least one digit and
# one letter. ``/`` is deliberately excluded from the body and included in
# the boundaries so a long file path is never eaten as a token; a token that
# contains ``/`` and sits inside a URL query is already gone by this point.
_LONG_B64_RE = re.compile(
    r"(?<![A-Za-z0-9+/_-])(?=[A-Za-z0-9+_-]*\d)(?=[A-Za-z0-9+_-]*[A-Za-z])"
    r"[A-Za-z0-9+_-]{32,}={0,2}(?![A-Za-z0-9+/_-])"
)
_EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_GENERIC_HOME_RE = re.compile(r"(?:/Users|/home)/[^/\s\\\"']+")
_GENERIC_WIN_HOME_RE = re.compile(r"[A-Za-z]:\\Users\\[^\\\s/\"']+")


def _home_dirs() -> list[str]:
    """The current user's home in every spelling it shows up with. A seam
    for tests (the real home must not appear in assertions)."""
    homes: list[str] = []
    for candidate in (os.path.expanduser("~"), os.environ.get("HOME"),
                      os.environ.get("USERPROFILE")):
        if candidate and candidate not in ("~", "") and candidate not in homes:
            homes.append(candidate)
    return homes


def sanitise(text: str) -> str:
    """Strip credentials, OAuth codes, home paths and emails from ``text``.

    Order matters only for readability: the URL rule runs first so a token
    inside a query leaves a clean ``?<redacted>`` instead of a redacted
    fragment inside a redacted query.
    """
    out = _URL_QUERY_RE.sub(lambda m: f"{m.group(1)}?{REDACTED}", text)
    out = _BEARER_RE.sub(f"Bearer {REDACTED}", out)
    out = _PREFIXED_KEY_RE.sub(REDACTED, out)
    out = _LONG_HEX_RE.sub(REDACTED, out)
    out = _LONG_B64_RE.sub(REDACTED, out)
    for home in sorted(_home_dirs(), key=len, reverse=True):
        out = out.replace(home, "~")
        alt = home.replace("\\", "/") if "\\" in home else home.replace("/", "\\")
        if alt != home:
            out = out.replace(alt, "~")
    out = _GENERIC_HOME_RE.sub("~", out)
    out = _GENERIC_WIN_HOME_RE.sub("~", out)
    out = _EMAIL_RE.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", out)
    return out


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------

@dataclass
class BundleSources:
    data_dir: Path | None
    daemon_log: Path | None
    stderr_log: Path | None
    hang_log: Path | None = None


def _daemon_log_from_root_logger() -> Path | None:
    """``app.py`` flags its RotatingFileHandler with ``_orbital_daemon_log``;
    its ``baseFilename`` is the one true location whatever the data dir."""
    for handler in logging.getLogger().handlers:
        if getattr(handler, "_orbital_daemon_log", False):
            name = getattr(handler, "baseFilename", None)
            if name:
                return Path(name)
    return None


def desktop_log_dir() -> Path:
    """Where ``agent_os/desktop/main.py`` redirects the frozen app's stderr.

    Mirrors ``_get_log_path`` there; importing that module is not an option
    (it redirects stderr and touches the Windows shell at import time).
    """
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", "")) / "Orbital" / "logs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "Orbital"
    return Path.home() / ".orbital" / "logs"


def default_sources(data_dir: Path | str | None) -> BundleSources:
    base = Path(data_dir) if data_dir else None
    daemon_log = _daemon_log_from_root_logger()
    if daemon_log is None and base is not None:
        daemon_log = base / "logs" / "daemon.log"
    if base is None and daemon_log is not None:
        # {data_dir}/logs/daemon.log — recover the data dir for the spool.
        base = daemon_log.parent.parent
    return BundleSources(
        data_dir=base,
        daemon_log=daemon_log,
        stderr_log=desktop_log_dir() / "orbital-stderr.log",
        # Spec 134 A3: written by agent_os/utils/stall_watch.py next to
        # daemon.log.
        hang_log=daemon_log.parent / "hang.log" if daemon_log is not None else None,
    )


# ---------------------------------------------------------------------------
# collection
# ---------------------------------------------------------------------------

def _read_tail(path: Path | None, max_bytes: int, label: str) -> str:
    if path is None:
        return f"{label}: not available"
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                data = f.read()
                # Drop the partial first line.
                nl = data.find(b"\n")
                data = data[nl + 1:] if nl >= 0 else data
                note = (f"[truncated: showing the last {len(data)} of {size} bytes]\n")
            else:
                data = f.read()
                note = ""
        return note + data.decode("utf-8", errors="replace")
    except FileNotFoundError:
        return f"{label}: not found"
    except Exception as exc:  # unreadable: say so, never fail the bundle
        return f"{label}: unreadable ({type(exc).__name__})"


def recent_failures(data_dir: Path | None) -> list[dict]:
    """The last ``RECENT_FAILURES_MAX`` failure rows.

    Prefers the ``recent_failures`` list in today's pending ping when the
    rollup publishes one (spec 113 parts A–C); otherwise walks the spool for
    the same event names. Both are optional — an empty list is a valid
    answer, not an error.
    """
    if data_dir is None:
        return []
    try:
        pending = Path(data_dir) / "telemetry" / "pending"
        for path in sorted(pending.glob("*.json"), reverse=True):
            try:
                ping = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            rows = (ping.get("counters") or {}).get("recent_failures")
            if isinstance(rows, list) and rows:
                return rows[-RECENT_FAILURES_MAX:]
    except Exception:
        pass
    try:
        spool = Spool(Path(data_dir))
        found: list[dict] = []
        for day in spool.days():
            for row in spool.read_day(day):
                if row.get("event") in FAILURE_EVENTS:
                    found.append(row)
        return found[-RECENT_FAILURES_MAX:]
    except Exception:
        return []


def os_description() -> str:
    try:
        return f"{platform.system()} {platform.release()} ({platform.machine()})"
    except Exception:
        return sys.platform


def feedback_url(ingest_endpoint: str) -> str:
    """``…/ingest`` → ``…/feedback`` on the same host the ping sender uses."""
    base = ingest_endpoint.rstrip("/")
    if base.endswith("/ingest"):
        base = base[: -len("/ingest")]
    return base + "/feedback"


def _dump(value) -> str:
    try:
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)
    except Exception:
        return repr(value)


def build_bundle(
    sources: BundleSources,
    *,
    install_id: str,
    version: str,
    os_name: str,
    telemetry_enabled: bool,
    sub_agents: list[dict] | None,
    context: dict | None,
) -> str:
    """Render and sanitise the bundle text. Pure apart from the file reads."""
    lines = [
        "Orbital diagnostic bundle",
        f"generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"install_id: {install_id}",
        f"version: {version}",
        f"os: {os_name}",
        f"telemetry_enabled: {'true' if telemetry_enabled else 'false'}",
    ]
    if not telemetry_enabled:
        lines.append("note: anonymous usage statistics are off; this bundle is "
                     "sent only as an explicit user action")
    if context:
        lines.append(f"context: {context.get('kind') or 'general'}")
        if context.get("agent"):
            lines.append(f"agent: {context['agent']}")
        if context.get("detail"):
            lines.append(f"detail: {str(context['detail'])[:2000]}")

    lines += ["", "== sub-agents =="]
    lines.append(_dump(sub_agents) if sub_agents is not None
                 else "sub-agent status: unavailable")

    lines += ["", "== recent telemetry failures =="]
    rows = recent_failures(sources.data_dir)
    lines.append(_dump(rows) if rows else "none")

    lines += ["", f"== daemon.log (last {DAEMON_LOG_TAIL_BYTES // 1024} KB) =="]
    lines.append(_read_tail(sources.daemon_log, DAEMON_LOG_TAIL_BYTES, "daemon.log"))

    lines += ["", "== orbital-stderr.log =="]
    lines.append(_read_tail(sources.stderr_log, STDERR_LOG_MAX_BYTES, "orbital-stderr.log"))

    lines += ["", f"== hang.log (last {HANG_LOG_TAIL_BYTES // 1024} KB) =="]
    lines.append(_read_tail(sources.hang_log, HANG_LOG_TAIL_BYTES, "hang.log"))

    return sanitise("\n".join(lines)) + "\n"
