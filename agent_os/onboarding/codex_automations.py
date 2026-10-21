# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Codex automations of a project folder, as Orbital schedule triggers.

Codex keeps each automation at ``<codex_dir>/automations/<id>/automation.toml``
(``version``, ``id``, ``kind``, ``name``, ``status``, ``prompt``, ``rrule``,
``target_thread_id``). One whose target thread ran in the project's folder is
imported as a schedule trigger assigned to ``codex`` that fires into that
thread's imported session (contract §6), so the automation keeps continuing
the same conversation. Read-only against ``~/.codex``; idempotent on
``imported_from.codex_automation_id``.
"""

from __future__ import annotations

import logging
import os
import tomllib
from datetime import datetime, timezone

from agent_os.onboarding import session_import as si

logger = logging.getLogger(__name__)

# The disabled fallback schedule for an RRULE that has no cron equivalent.
FALLBACK_CRON = "0 9 * * *"

_DAYS = {"SU": 0, "MO": 1, "TU": 2, "WE": 3, "TH": 4, "FR": 5, "SA": 6}
_DAY_NAMES = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"}

# RRULE parts we can map. Anything else (COUNT, UNTIL, BYMONTHDAY, BYSETPOS, …)
# makes the rule unmappable.
_KNOWN_PARTS = {"FREQ", "INTERVAL", "BYHOUR", "BYMINUTE", "BYSECOND", "BYDAY", "WKST"}


def rrule_to_cron(rrule: str) -> tuple[str, str, bool]:
    """``(cron, human, mappable)`` for an iCalendar RRULE.

    Maps FREQ=DAILY (BYHOUR required), FREQ=WEEKLY (BYDAY + BYHOUR required)
    and FREQ=HOURLY (optional INTERVAL), each with an optional BYMINUTE and a
    BYSECOND of 0. Anything else returns ``(FALLBACK_CRON, rrule, False)`` —
    imported disabled, with the raw rule as the caption so the user sees what
    it was.
    """
    fallback = (FALLBACK_CRON, rrule, False)
    try:
        parts = _parse_rrule(rrule)
    except ValueError:
        return fallback
    if not parts or set(parts) - _KNOWN_PARTS:
        return fallback
    freq = parts.get("FREQ")
    try:
        interval = int(parts.get("INTERVAL", "1"))
        minutes = _int_list(parts.get("BYMINUTE", "0"), 0, 59)
        hours = _int_list(parts["BYHOUR"], 0, 23) if "BYHOUR" in parts else None
        seconds = _int_list(parts.get("BYSECOND", "0"), 0, 59)
    except ValueError:
        return fallback
    if interval < 1 or seconds != [0] or len(minutes) != 1:
        return fallback
    minute = minutes[0]

    if freq == "HOURLY":
        if hours is not None or "BYDAY" in parts:
            return fallback
        if interval == 1:
            return f"{minute} * * * *", f"Hourly at :{minute:02d}", True
        if interval > 23:
            return fallback
        return (f"{minute} */{interval} * * *",
                f"Every {interval} hours at :{minute:02d}", True)

    if hours is None or interval != 1:
        return fallback
    hour_field = ",".join(str(h) for h in hours)
    times = ", ".join(f"{h:02d}:{minute:02d}" for h in hours)
    if freq == "DAILY":
        if "BYDAY" in parts:
            return fallback
        return f"{minute} {hour_field} * * *", f"Daily at {times}", True
    if freq == "WEEKLY":
        days = []
        for token in (parts.get("BYDAY") or "").split(","):
            token = token.strip().upper()
            if token not in _DAYS:
                return fallback  # missing, or an ordinal like "1MO"
            days.append(_DAYS[token])
        if not days:
            return fallback
        day_field = ",".join(str(d) for d in days)
        names = ", ".join(_DAY_NAMES[d] for d in days)
        return f"{minute} {hour_field} * * {day_field}", f"Weekly on {names} at {times}", True
    return fallback


def _parse_rrule(rrule: str) -> dict[str, str]:
    if not isinstance(rrule, str):
        raise ValueError("not a string")
    body = rrule.strip()
    if body.upper().startswith("RRULE:"):
        body = body[len("RRULE:"):]
    parts: dict[str, str] = {}
    for chunk in body.split(";"):
        if not chunk:
            continue
        if "=" not in chunk:
            raise ValueError(chunk)
        key, value = chunk.split("=", 1)
        parts[key.strip().upper()] = value.strip()
    return parts


def _int_list(value: str, lo: int, hi: int) -> list[int]:
    out = []
    for token in value.split(","):
        n = int(token)
        if not lo <= n <= hi:
            raise ValueError(token)
        out.append(n)
    if not out:
        raise ValueError(value)
    return out


def load_automations(codex_dir: str | None = None) -> list[dict]:
    """Every parseable ``automation.toml`` under ``<codex_dir>/automations``,
    sorted by id. A file that does not parse, or has no id, is skipped."""
    root = os.path.join(si.codex_home(codex_dir), "automations")
    if not os.path.isdir(root):
        return []
    out: list[dict] = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    for name in names:
        path = os.path.join(root, name, "automation.toml")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            logger.info("codex automation import: skipping unreadable %s", path)
            continue
        if not isinstance(data, dict) or not data.get("id"):
            continue
        out.append(data)
    return out


def _local_tz_name() -> str:
    from agent_os.agent.workbench_cards import _local_tz_name as local
    return local()


def build_trigger(automation: dict, *, session_id: str, tz: str,
                  now: str | None = None) -> dict:
    """The Orbital trigger record for one Codex automation (contract §6).

    Same field set the create route writes, plus ``session_id`` (fires
    continue the imported thread) and ``imported_from``."""
    from agent_os.daemon_v2.trigger_manager import generate_trigger_id
    cron, human, mappable = rrule_to_cron(automation.get("rrule") or "")
    active = str(automation.get("status") or "").upper() == "ACTIVE"
    return {
        "id": generate_trigger_id(),
        "name": str(automation.get("name") or automation["id"]),
        "enabled": bool(active and mappable),
        "type": "schedule",
        "task": str(automation.get("prompt") or ""),
        "last_triggered": None,
        "trigger_count": 0,
        "created_at": now or datetime.now(timezone.utc).isoformat(),
        "agent": "codex",
        "schedule": {"cron": cron, "human": human, "timezone": tz},
        "session_id": session_id,
        "imported_from": {"codex_automation_id": str(automation["id"])},
    }


def plan_automation_imports(workspace: str, *, existing_triggers: list[dict],
                            codex_dir: str | None = None,
                            now: str | None = None) -> tuple[list[dict], int]:
    """``(new trigger records, skipped)`` for the folder's Codex automations.

    An automation belongs to the folder when its ``target_thread_id`` is one of
    the folder's importable Codex threads (``session_import``). ``skipped``
    counts the folder's automations not imported now: already imported
    (idempotency on ``imported_from.codex_automation_id``) or invalid.
    Automations of other folders are not counted. Pure planning — the caller
    persists and registers.
    """
    from agent_os.daemon_v2.trigger_manager import validate_trigger
    automations = load_automations(codex_dir)
    if not automations:
        return [], 0
    threads = {
        native.native_id: native.session_id
        for native in si.list_native_sessions(workspace, codex_dir=codex_dir)
        if native.agent == si.AGENT_CODEX
    }
    done = {
        str((t.get("imported_from") or {}).get("codex_automation_id"))
        for t in existing_triggers or []
        if isinstance(t.get("imported_from"), dict)
    }
    tz = _local_tz_name()
    new: list[dict] = []
    skipped = 0
    for automation in automations:
        target = automation.get("target_thread_id")
        if not target or str(target) not in threads:
            continue
        aid = str(automation["id"])
        if aid in done:
            skipped += 1
            continue
        record = build_trigger(automation, session_id=threads[str(target)], tz=tz, now=now)
        error = validate_trigger(record, workspace=workspace)
        if error:
            logger.info("codex automation import: %s skipped (%s)", aid, error)
            skipped += 1
            continue
        done.add(aid)
        new.append(record)
    return new, skipped
