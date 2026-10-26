# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Per-spawn composition rendering for harnesses configured by a file.

Some agents take part of their runtime configuration from a file rather than
argv. dsh (DeepSeek Harness 0.2) boots its shipped ``acp`` profile and accepts
a Cordis patch-list overlay (``--patch <file>``) that carries Orbital's
persona, model, and per-project session store. Orbital renders one such file
per spawn: unique name, written under the workspace's sub-agent dir, deleted
when the adapter stops. Never one shared file — same-slug dispatch is
unrestricted across projects and sessions, so a shared config is a race by
construction.

A patch row REPLACES the target row's whole ``config``; the template restates
every key a row needs, and the renderer only ever sets values inside it.

Rendering is programmatic (``safe_load`` → set fields → ``safe_dump``), never
string templating. The persona is the real rendered sub-agent prompt: arbitrary
prose that would otherwise be a YAML injection vector.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid

import yaml

logger = logging.getLogger(__name__)

# Rendered files are named ``cordis-<8 hex>.yml``. Both halves are load-bearing:
# the prefix is what ``gc_stale`` is willing to delete, and the random suffix is
# what makes concurrent renders for one handle collision-free.
_RENDERED_PREFIX = "cordis-"
_RENDERED_SUFFIX = ".yml"

# Row ids the renderer knows how to configure. These are profile-local ids
# (the ``id:`` key), not package names. The sandbox mode is not among them:
# dsh reads it from ``DSH_PERMISSION_MODE`` (the ``permission-mode`` param's
# env var), which also drives its approval policy.
_SYSTEM_PROMPT_ID = "system-prompt"
_ACP_ID = "acp"
_SESSION_STORE_ID = "session-persistence-jsonl"


class CompositionError(ValueError):
    """Raised when a composition template is missing or malformed."""


# ---------------------------------------------------------------------------
# YAML that carries dsh's JS expressions through untouched
# ---------------------------------------------------------------------------
#
# dsh profile files hold values like ``!!js process.cwd()`` — a JavaScript
# expression dsh evaluates itself. Orbital copies rows out of DSH Desktop's
# profile verbatim, so such a value must survive the round trip as the same
# tagged expression: never evaluated, never turned into a plain string.

_JS_TAG = "tag:yaml.org,2002:js"


class JsExpression(str):
    """A ``!!js`` scalar, kept as its source text."""


class _Loader(yaml.SafeLoader):
    pass


class _Dumper(yaml.SafeDumper):
    pass


_Loader.add_constructor(
    _JS_TAG, lambda loader, node: JsExpression(loader.construct_scalar(node)))
_Dumper.add_representer(
    JsExpression, lambda dumper, value: dumper.represent_scalar(_JS_TAG, str(value)))


def _yaml_load(stream):
    return yaml.load(stream, Loader=_Loader)  # noqa: S506 — SafeLoader subclass


# ---------------------------------------------------------------------------
# The user's own dsh setup (DSH Desktop's profile)
# ---------------------------------------------------------------------------

def mirror_source_path(spec: dict, env: dict | None = None) -> str | None:
    """The profile file a manifest's ``runtime.config_mirror`` reads:
    ``file`` (relative) under ``$<home_env>``, or under ``home_default``
    when that variable is unset — dsh's own DSH_HOME rule."""
    rel = (spec or {}).get("file")
    if not rel:
        return None
    env = os.environ if env is None else env
    home = env.get(spec.get("home_env") or "") or os.path.expanduser(
        spec.get("home_default") or "~")
    return os.path.join(home, rel)


def read_mirror(spec: dict, env: dict | None = None) -> tuple[list, dict | None, str]:
    """The rows to copy from the user's own dsh profile, its default model,
    and a digest of both.

    ``spec`` is the manifest's ``runtime.config_mirror``: ``rows`` names the
    row ids copied verbatim (provider definitions — what works in DSH Desktop
    works here), ``default_model_row`` the row whose ``{provider, model}`` is
    the user's default there. Missing or unreadable file → nothing to copy;
    the digest still changes whenever what would be copied changes, which
    is what rebuilds a worker after the user edits providers in the app.
    """
    path = mirror_source_path(spec, env)
    rows: list = []
    default_pair: dict | None = None
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                document = _yaml_load(f)
        except (OSError, yaml.YAMLError):
            logger.warning("dsh profile not readable for mirroring: %s", path)
            document = None
        wanted = set(spec.get("rows") or [])
        default_id = spec.get("default_model_row")
        for row in document if isinstance(document, list) else []:
            if not isinstance(row, dict):
                continue
            row_id = row.get("id")
            if row_id in wanted:
                rows.append(row)
            elif default_id and row_id == default_id:
                config = row.get("config")
                if isinstance(config, dict) and config.get("provider") and config.get("model"):
                    default_pair = {"provider": str(config["provider"]),
                                    "model": str(config["model"])}
    digest = hashlib.sha256(yaml.dump(
        {"rows": rows, "default": default_pair}, Dumper=_Dumper,
        sort_keys=True).encode("utf-8")).hexdigest()
    return rows, default_pair, digest


def split_model_choice(value: str | None, default_provider: str) -> tuple[str | None, str | None]:
    """``provider/model`` → (provider, model); a bare model id belongs to
    ``default_provider`` (values saved before providers existed)."""
    value = (value or "").strip()
    if not value:
        return None, None
    if "/" in value:
        provider, model = value.split("/", 1)
        if provider and model:
            return provider, model
    return default_provider, value


def render_composition(
    template_path: str,
    *,
    model: str | None,
    persona: str | None,
    persistence_root: str,
    out_dir: str,
    provider: str | None = None,
    mirror_rows: list | None = None,
) -> str:
    """Render one spawn's patch file and return its absolute path.

    ``persistence_root`` is absolutised: the template's ``./.sessions`` would
    resolve against the harness's cwd and scatter session stores, and it has
    to be the same directory every spawn for ``session/resume`` to find the
    thread again.

    A ``None`` for model, provider or persona leaves the template's own value
    in place — the template ships working defaults precisely so it stays
    bootable when a caller has nothing to say.

    ``mirror_rows`` (rows copied from the user's DSH Desktop profile) are
    appended after the template's: patch overlays apply in order, so the
    user's provider definitions replace the ``acp`` profile's empty ones.
    """
    blocks = _load_template(template_path)

    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_id = block.get("id")
        if block_id == _SYSTEM_PROMPT_ID:
            _set_if(_config_of(block), "personaPrefix", persona)
        elif block_id == _ACP_ID:
            _set_if(_config_of(block), "provider", provider)
            _set_if(_config_of(block), "model", model)
        elif block_id == _SESSION_STORE_ID:
            _config_of(block)["root"] = os.path.abspath(persistence_root)

    blocks.extend(row for row in (mirror_rows or []) if isinstance(row, dict))

    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(
        out_dir, f"{_RENDERED_PREFIX}{uuid.uuid4().hex[:8]}{_RENDERED_SUFFIX}"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(
            blocks, f, Dumper=_Dumper, sort_keys=False, allow_unicode=True,
            default_flow_style=False, width=10**6,
        )
    return out_path


def effective_model(manifest, params: dict) -> tuple[str, str] | None:
    """(provider, model) a spawn runs with: Orbital's ``provider/model``
    setting, else the default model the user picked in their own setup of
    the agent (``runtime.config_mirror``), else None (the template's)."""
    mirror_spec = getattr(manifest.runtime, "config_mirror", None) or {}
    provider, model = split_model_choice(
        params.get("model"), mirror_spec.get("default_provider") or "")
    if model is None and mirror_spec:
        default_pair = read_mirror(mirror_spec)[1]
        if default_pair is not None:
            provider, model = default_pair["provider"], default_pair["model"]
    if model is None:
        return None
    return provider or mirror_spec.get("default_provider") or "", model


def acp_model_value(pair: tuple[str, str]) -> str:
    """dsh's ACP ``model`` option value: the pair as compact JSON, byte for
    byte what dsh advertises (``["provider","model"]``, no spaces)."""
    return json.dumps(list(pair), separators=(",", ":"))


def render_spawn_file(manifest, *, data_dir: str, config_store, persona: str | None,
                      out_dir: str, persistence_root: str) -> str:
    """Render ``manifest``'s per-spawn file — the one route every spawn and
    every probe of the agent takes, so both run the same configuration.

    The template comes from the agent's install dir under ``data_dir`` when
    one was staged there, else from the copy shipped with the build. The
    model setting is ``provider/model``; unset follows the default model the
    user picked in their own setup (``runtime.config_mirror``), whose
    provider rows are copied in unchanged.
    """
    from agent_os.agents.installer import bundled_asset_path
    from agent_os.daemon_v2.sub_agent_config_store import resolve_params

    template_name = manifest.runtime.config_template
    template_path = os.path.join(data_dir, "agents", manifest.slug, template_name)
    if not os.path.isfile(template_path):
        # A launcher outside Orbital's data dir (a desktop app, the locate
        # card; spec 127 V4) never had the template staged; the build ships it.
        bundled = bundled_asset_path(manifest.slug, template_name)
        if bundled is not None:
            template_path = bundled

    params = resolve_params(manifest.slug, config_store)
    mirror_spec = getattr(manifest.runtime, "config_mirror", None) or {}
    mirror_rows = read_mirror(mirror_spec)[0] if mirror_spec else []
    provider, model = effective_model(manifest, params) or (None, None)
    return render_composition(
        template_path,
        model=model,
        provider=provider or None,
        persona=persona,
        persistence_root=persistence_root,
        out_dir=out_dir,
        mirror_rows=mirror_rows,
    )


def unlink_rendered(path: str | None) -> None:
    """Delete a rendered composition. Never raises."""
    if not path:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("Failed to remove rendered composition %s", path,
                       exc_info=True)


def gc_stale(out_dir: str, max_age_days: int = 7) -> int:
    """Delete rendered compositions older than ``max_age_days``.

    Opportunistic cleanup for the paths that never reach ``unlink_rendered``
    (daemon crash, an unkillable adapter that keeps its slot). Returns the
    number removed; never raises.
    """
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    try:
        names = os.listdir(out_dir)
    except OSError:
        return 0
    for name in names:
        if not (name.startswith(_RENDERED_PREFIX)
                and name.endswith(_RENDERED_SUFFIX)):
            continue
        path = os.path.join(out_dir, name)
        try:
            if os.path.getmtime(path) >= cutoff:
                continue
            os.unlink(path)
            removed += 1
        except OSError:
            continue
    return removed


def _load_template(template_path: str) -> list:
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            blocks = _yaml_load(f)
    except OSError as exc:
        raise CompositionError(
            f"Composition template not readable: {template_path} ({exc})"
        ) from exc
    except yaml.YAMLError as exc:
        raise CompositionError(
            f"Composition template is not valid YAML: {template_path} ({exc})"
        ) from exc
    if not isinstance(blocks, list):
        raise CompositionError(
            f"Composition template must be a YAML list of plugin blocks: "
            f"{template_path}"
        )
    return blocks


def _set_if(config: dict, key: str, value) -> None:
    """Set ``key`` unless the caller had nothing to say (``None``)."""
    if value is not None:
        config[key] = value


def _config_of(block: dict) -> dict:
    """The block's ``config`` mapping, created if absent or malformed."""
    config = block.get("config")
    if not isinstance(config, dict):
        config = {}
        block["config"] = config
    return config
