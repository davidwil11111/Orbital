# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Send feedback with diagnostics (spec 113 part D).

``GET  /api/v2/feedback/preview``   the sanitised bundle as text + its size
``POST /api/v2/feedback/send``      rebuild, sanitise again, upload once

Consent is the preview: the UI shows exactly this text before the user
presses Send, and the send route builds the same text again rather than
trusting anything the client echoes back. The upload is a single POST to the
telemetry service's ``/feedback`` (same host as the ping sender's
``/ingest``), explicit-action only — it ignores the usage-statistics toggle
and never touches the periodic sender. No retry, no queue: a failure is a
502 with one WARNING in the daemon log, and the UI keeps the user's text.
"""

from __future__ import annotations

import logging
import os
import platform

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent_os import telemetry
from agent_os.api.routes import settings as settings_routes
from agent_os.telemetry import diagnostic_bundle as bundle
from agent_os.telemetry.sender import APP_TOKEN, DEFAULT_ENDPOINT
from agent_os.version import get_version

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2")

UPLOAD_TIMEOUT_SECONDS = 20.0


class FeedbackContext(BaseModel):
    kind: str = "general"
    agent: str | None = None
    detail: str | None = None


class SendFeedbackRequest(BaseModel):
    message: str = ""
    context: FeedbackContext | None = None


# -- seams (monkeypatched by tests) -----------------------------------------

async def _httpx_post(url: str, payload: dict, headers: dict) -> int:
    import httpx

    async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT_SECONDS) as client:
        resp = await client.post(url, json=payload, headers=headers)
        return resp.status_code


_post = _httpx_post


def _ingest_endpoint() -> str:
    """The base the ping sender was configured with; same expression app.py
    uses so a stubbed AGENT_OS_TELEMETRY_URL redirects both."""
    sender = telemetry.get_sender()
    configured = getattr(sender, "_endpoint", None) if sender is not None else None
    return configured or os.environ.get("AGENT_OS_TELEMETRY_URL", DEFAULT_ENDPOINT)


def _install_id() -> str:
    sender = telemetry.get_sender()
    identity = getattr(sender, "_identity", None) if sender is not None else None
    try:
        return identity.install_id if identity is not None else "inst_unknown"
    except Exception:
        return "inst_unknown"


def _sources() -> bundle.BundleSources:
    sender = telemetry.get_sender()
    data_dir = getattr(sender, "_data_dir", None) if sender is not None else None
    if data_dir is None:
        data_dir = os.environ.get("AGENT_OS_DATA_DIR") or None
    return bundle.default_sources(data_dir)


def _telemetry_enabled() -> bool:
    store = settings_routes._settings_store
    if store is not None:
        try:
            return bool(store.get().telemetry_enabled)
        except Exception:
            pass
    return True


async def _sub_agent_status() -> list[dict] | None:
    """The exact list ``GET /settings/sub-agents`` returns; None on failure."""
    try:
        return await settings_routes.list_sub_agent_settings()
    except Exception:
        logger.debug("feedback: sub-agent status unavailable", exc_info=True)
        return None


async def _build(context: dict | None) -> tuple[str, bool]:
    enabled = _telemetry_enabled()
    text = bundle.build_bundle(
        _sources(),
        install_id=_install_id(),
        version=get_version(),
        os_name=bundle.os_description(),
        telemetry_enabled=enabled,
        sub_agents=await _sub_agent_status(),
        context=context,
    )
    return text, enabled


# -- routes -----------------------------------------------------------------

@router.get("/feedback/preview")
async def preview_feedback(kind: str | None = None, agent: str | None = None,
                           detail: str | None = None):
    context = {"kind": kind or "general", "agent": agent, "detail": detail} if kind else None
    text, enabled = await _build(context)
    return {
        "text": text,
        "bytes": len(text.encode("utf-8")),
        "telemetry_enabled": enabled,
    }


@router.post("/feedback/send")
async def send_feedback(req: SendFeedbackRequest):
    context = req.context.model_dump(exclude_none=True) if req.context is not None else None
    text, _ = await _build(context)
    payload = {
        "schema": 1,
        "install_id": _install_id(),
        "version": get_version(),
        "os": platform.system().lower(),
        "message": req.message[:bundle.MAX_MESSAGE_CHARS],
        "context": context,
        "bundle": text,
    }
    url = bundle.feedback_url(_ingest_endpoint())
    headers = {"X-Orbital-Telemetry-Token": APP_TOKEN}
    try:
        status = await _post(url, payload, headers)
    except Exception as exc:
        logger.warning("feedback upload failed: %s: %s", type(exc).__name__, exc)
        raise HTTPException(status_code=502,
                            detail={"error": "upload_failed", "status": None})
    if not 200 <= status < 300:
        # An old server has no /feedback (404) and a huge bundle is 413 —
        # both end here as the same "try later" the UI shows.
        logger.warning("feedback upload rejected: server returned %s", status)
        raise HTTPException(status_code=502,
                            detail={"error": "upload_failed", "status": status})
    size = len(text.encode("utf-8"))
    logger.info("feedback bundle sent (%d bytes)", size)
    return {"ok": True, "bytes": size}
