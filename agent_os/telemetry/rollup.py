# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Daily rollup: spool events → one aggregate ping (spec 046 §5).

The ping is the ONLY thing that ever leaves the machine. Counters, enums,
booleans, and ISO dates — no content, no paths, no per-project or per-session
identifiers. The schema is published in the README; the settings payload
viewer renders this exact JSON.
"""

from __future__ import annotations

from .diagnostics import (
    FAILURE_EVENTS,
    LOGIN_FAILURE_REASONS,
    RECENT_FAILURES_LIMIT,
    SUBAGENT_FAILURE_REASONS,
    sanitize_failure_row,
)
from .identity import InstallIdentity
from .spool import Spool

SCHEMA_VERSION = 1

# Milestone names carried in every ping (lifetime booleans, §5).
MILESTONES = ("key_set", "first_project", "first_session", "first_turn")

# Sub-agent slugs allowed to leave the machine. A dispatch handle is chosen by
# the model, so a hallucinated name reaches the spawn and fails — anything not
# built in is reported as ``other``; fanout workers (``worker:<id>-<i>``) as
# ``native``. A parity test fails when a manifest is added without a row here.
SUBAGENT_SLUGS = (
    "aider", "claude-code", "cline", "codebuddy", "codex", "continue-cli",
    "copilot-cli", "cursor", "dsh", "gemini-cli", "goose", "kimi-code",
    "minimax-code", "pi",
)


def _subagent_bucket(agent: object) -> str:
    if isinstance(agent, str):
        if agent in SUBAGENT_SLUGS:
            return agent
        if agent.startswith("worker:"):
            return "native"
    return "other"


def _reason_bucket(reason: object, allowed: tuple[str, ...]) -> str:
    """A reason is an enum, never text: anything outside ``allowed`` — an
    old-format row with no reason, or a value a future build invents — is
    reported as ``unknown`` (spec 113 §3.1 parity rule)."""
    return reason if isinstance(reason, str) and reason in allowed else "unknown"


def _bump(mapping: dict, key: str) -> None:
    mapping[key] = mapping.get(key, 0) + 1


def _login_bucket(counters: dict, agent: object) -> dict:
    return counters["login_by_agent"].setdefault(
        _subagent_bucket(agent), {"attempted": 0, "failed": 0, "unverified": 0})


def _subagent_entry(counters: dict, agent: object) -> dict:
    return counters["subagent_by_agent"].setdefault(
        _subagent_bucket(agent),
        {"dispatched": 0, "failed": 0, "failed_by_reason": {}})


def build_ping(
    identity: InstallIdentity,
    spool: Spool,
    day: str,
    version: str,
    os_name: str,
) -> dict:
    """Aggregate ``day``'s (UTC) spool rows into the §5 ping shape."""
    counters: dict = {
        "app_starts": 0,
        "projects_created": 0,
        "sessions": 0,
        "turns": 0,
        "errors": {},
        # Same failures as ``errors``, keyed by provider instead of by code
        # (spec 063 §7 P1). Both are published: ``errors`` is the only error
        # series with history, so it stays for continuity.
        "errors_by_provider": {},
        "tokens_by_provider": {},
        # Sub-agent setup funnel (spec 063 §12 decision 5): the CLI login jobs
        # emitted nothing, so the product's differentiating setup step was
        # invisible between "key set" and "first turn".
        "login_attempted": 0,
        # Spec 113 A1: ``login_failed`` no longer includes an exit-0 whose
        # auth re-check could not confirm the login (that is
        # ``login_unverified`` — the UI never called it a failure either).
        # Rows from builds before this split counted those as failed.
        "login_failed": 0,
        "login_unverified": 0,
        # The same funnel per CLI (allowlisted slugs, like subagent_by_agent)
        # and the real failures by reason (enum: LOGIN_FAILURE_REASONS).
        "login_by_agent": {},
        "login_failed_by_reason": {},
        # Spec 116 D2 / 113: the in-app "Update <CLI>" job (same runner as
        # login). Failed = the re-probed version is still below the floor.
        "update_attempted": 0,
        "update_failed": 0,
        # Delegation: does anyone hand work to a sub-agent, and does it land?
        # ``failed`` covers a spawn that never started, so it is not a subset
        # of ``dispatches``.
        "subagent_dispatches": 0,
        "subagent_failed": 0,
        # Spec 113 A2: why (enum: SUBAGENT_FAILURE_REASONS) — a spawn that
        # never started, a task that errored, our own adapter breaking, a
        # dispatch refused for a missing login (A6) or an old CLI (116 D5).
        "subagent_failed_by_reason": {},
        "subagent_by_agent": {},
        # Spec 113 (2026-10-18 revision): the last 20 failure events of the
        # day with their structured diagnostics — enums and numbers only, see
        # ``diagnostics.sanitize_failure_row`` — so a user's problem can be
        # located from this payload alone.
        "recent_failures": [],
    }
    recent: list[dict] = []
    for row in spool.read_day(day):
        event = row.get("event")
        if event == "app_start":
            counters["app_starts"] += 1
        elif event == "project_created":
            counters["projects_created"] += 1
        elif event == "session_created":
            counters["sessions"] += 1
        elif event == "turn_completed":
            # "turns" counts management-loop responses only (the error-rate
            # denominator); sub-agent responses still contribute tokens.
            if not str(row.get("source") or "management").startswith("subagent"):
                counters["turns"] += 1
            provider = str(row.get("provider") or "unknown")
            bucket = counters["tokens_by_provider"].setdefault(provider, {"in": 0, "out": 0})
            # The ledger's four disjoint categories: everything that entered the
            # context window counts as "in"; generated tokens as "out".
            bucket["in"] += int(row.get("uncached_input") or 0)
            bucket["in"] += int(row.get("cache_read") or 0)
            bucket["in"] += int(row.get("cache_write") or 0)
            bucket["out"] += int(row.get("output") or 0)
        elif event == "llm_error":
            code = str(row.get("error_code") or "unknown")
            counters["errors"][code] = counters["errors"].get(code, 0) + 1
            # Same "unknown" fallback as tokens above: the provider is
            # unresolved when the failure happens before config resolution.
            provider = str(row.get("provider") or "unknown")
            by_provider = counters["errors_by_provider"]
            by_provider[provider] = by_provider.get(provider, 0) + 1
        elif event == "login_attempted":
            counters["login_attempted"] += 1
            _login_bucket(counters, row.get("agent"))["attempted"] += 1
        elif event == "login_failed":
            counters["login_failed"] += 1
            _login_bucket(counters, row.get("agent"))["failed"] += 1
            _bump(counters["login_failed_by_reason"],
                  _reason_bucket(row.get("reason"), LOGIN_FAILURE_REASONS))
        elif event == "login_unverified":
            counters["login_unverified"] += 1
            _login_bucket(counters, row.get("agent"))["unverified"] += 1
        elif event == "update_attempted":
            counters["update_attempted"] += 1
        elif event == "update_failed":
            counters["update_failed"] += 1
        elif event in ("subagent_dispatched", "subagent_failed"):
            dispatched = event == "subagent_dispatched"
            counters["subagent_dispatches" if dispatched else "subagent_failed"] += 1
            by_agent = _subagent_entry(counters, row.get("agent"))
            by_agent["dispatched" if dispatched else "failed"] += 1
            if not dispatched:
                reason = _reason_bucket(row.get("reason"), SUBAGENT_FAILURE_REASONS)
                _bump(counters["subagent_failed_by_reason"], reason)
                _bump(by_agent["failed_by_reason"], reason)
        if event in FAILURE_EVENTS:
            recent.append(sanitize_failure_row(row, agent_bucket=_subagent_bucket))
    # Bounded and newest-last: the spool is append-only in time order.
    counters["recent_failures"] = recent[-RECENT_FAILURES_LIMIT:]

    milestones = identity.milestones
    return {
        "schema": SCHEMA_VERSION,
        "install_id": identity.install_id,
        "account_id": None,  # nullable from day one (§8)
        "version": version,
        "os": os_name,
        "date": day,
        "first_seen": identity.first_seen,
        "milestones": {name: bool(milestones.get(name)) for name in MILESTONES},
        "counters": counters,
    }
