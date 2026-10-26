# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The models an ACP agent itself offers, for its Settings dropdown.

dsh lists, in ``session/new``'s ``model`` config option, every provider route
that can serve right now — DeepSeek with a key, the DeepSeek account when
signed in, each provider the user set up in DSH Desktop — grouped by provider,
each value a ``["provider","model"]`` pair. That list is the truth about what
works, so Orbital shows it instead of guessing: the agent is started exactly
as a dispatch would start it (same binary, env, and rendered patch, including
the rows mirrored from the user's own setup) against a scratch directory that
is deleted afterwards, so no session lands in the user's store.

Cached per agent until the user's Refresh (``clear_acp_models_cache``) or
five minutes, whichever comes first: a probe costs one agent start.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import threading
import time

logger = logging.getLogger(__name__)

CACHE_TTL_S = 300.0
# dsh registers user-configured provider routes asynchronously after boot; a
# session opened at once listed only the default model in about half the
# runs on 0.2.1-rc.3. The second session, this long after the first, lists them.
SESSION_SETTLE_S = 1.5

_cache: dict[tuple, tuple[float, dict | None]] = {}
_lock = threading.Lock()
# One probe per key at a time: Settings now asks in the background while the
# page may ask again (spec 136 C.2), and each probe is an agent start.
_inflight: dict[tuple, threading.Event] = {}


def clear_acp_models_cache() -> None:
    with _lock:
        _cache.clear()


def model_choices(config_options: list[dict]) -> list[dict]:
    """``[{"value": "provider/model", "label": "Group · Model"}]`` from an
    agent's ``model`` config option. Values that are not a provider/model
    pair are passed through as they are."""
    choices: list[dict] = []
    for option in config_options or []:
        if not isinstance(option, dict) or option.get("id") != "model":
            continue
        for entry in option.get("options") or []:
            if not isinstance(entry, dict):
                continue
            group_name = entry.get("name") or entry.get("group") or ""
            inner = entry.get("options")
            items = inner if isinstance(inner, list) else [entry]
            for item in items:
                value = _choice_value(item.get("value"))
                if not value:
                    continue
                name = item.get("name") or value
                label = f"{group_name} · {name}" if inner is not None and group_name else name
                choices.append({"value": value, "label": label})
    return choices


def effort_choices(config_options: list[dict]) -> list[dict]:
    """``[{"value", "label"}]`` from the agent's ``reasoning_effort`` option
    for the model it runs. The empty value ("provider default") is left out:
    Orbital's unset setting already means that."""
    for option in config_options or []:
        if isinstance(option, dict) and option.get("id") == "reasoning_effort":
            return [{"value": item["value"], "label": item.get("name") or item["value"]}
                    for item in option.get("options") or []
                    if isinstance(item, dict) and isinstance(item.get("value"), str)
                    and item["value"]]
    return []


def _choice_value(raw) -> str | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        pair = json.loads(raw)
    except ValueError:
        return raw
    if (isinstance(pair, list) and len(pair) == 2
            and all(isinstance(p, str) and p for p in pair)):
        return f"{pair[0]}/{pair[1]}"
    return raw


def live_models(engine, slug: str) -> list[dict] | None:
    """The agent's own model choices, or None when it cannot be asked
    (not installed, not ACP, no answer) — callers keep the static list."""
    options = live_options(engine, slug)
    return options["models"] if options else None


def live_options(engine, slug: str) -> dict | None:
    """``{"models": [...], "efforts": [...]}`` — what the agent offers for
    model and, for the model it runs, reasoning effort — or None."""
    # Keyed by what a spawn would run with (binary, env, params, and the
    # rows mirrored from the user's own setup): a saved setting, a new key,
    # or a provider added in DSH Desktop asks again instead of serving the
    # list the previous configuration produced.
    fingerprint_of = getattr(engine, "spawn_fingerprint", None)
    key = (slug, fingerprint_of(slug, "") if callable(fingerprint_of) else None)
    while True:
        with _lock:
            cached = _cache.get(key)
            if cached is not None and time.monotonic() - cached[0] < CACHE_TTL_S:
                return cached[1]
            running = _inflight.get(key)
            if running is None:
                running = _inflight[key] = threading.Event()
                break
        # Another caller is probing this key: take its answer.
        running.wait(timeout=60)
    try:
        choices = _probe(engine, slug)
        with _lock:
            _cache[key] = (time.monotonic(), choices)
        return choices
    finally:
        with _lock:
            _inflight.pop(key, None)
        running.set()


def _probe(engine, slug: str) -> dict | None:
    from agent_os.agents.composition import render_spawn_file
    from agent_os.agents.handshake import acp_handshake

    registry = getattr(engine, "_registry", None)
    manifest = registry.get(slug) if registry is not None else None
    if manifest is None or getattr(manifest.runtime, "transport", "") != "acp-sdk":
        return None
    scratch = tempfile.mkdtemp(prefix="orbital-models-")
    try:
        try:
            config = engine.get_adapter_config(slug, scratch)
        except ValueError:
            return None
        args = list(config.get("args") or [])
        if manifest.runtime.config_template:
            rendered = render_spawn_file(
                manifest, data_dir=engine.data_dir,
                config_store=getattr(engine, "sub_agent_config_store", None),
                persona=None, out_dir=scratch,
                persistence_root=f"{scratch}/sessions")
            args += [manifest.runtime.config_flag or "--config", rendered]
        result = acp_handshake([config["command"]] + args, env=config.get("env"),
                               cwd=scratch, session_cwd=scratch,
                               session_settle_s=SESSION_SETTLE_S)
        if result.get("state") != "ok" or "config_options" not in result:
            logger.info("live model list for %s unavailable: %s", slug,
                        result.get("detail") or result.get("state"))
            return None
        models = model_choices(result["config_options"])
        if not models:
            return None
        return {"models": models, "efforts": effort_choices(result["config_options"])}
    except Exception:  # noqa: BLE001 — a probe failure keeps the static list
        logger.warning("live model probe failed for %s", slug, exc_info=True)
        return None
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
