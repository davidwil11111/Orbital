# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``GET /api/v2/feedback/preview`` + ``POST /api/v2/feedback/send`` (spec 113 D).

Mounts only the feedback router; the upload is an injected coroutine so no
test reaches the network. The settings module's singletons are left unset,
which is the "status unavailable" path the bundle must survive.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from agent_os.api.routes import feedback as feedback_routes
from agent_os.telemetry import diagnostic_bundle as db


class FakePost:
    def __init__(self, status: int = 200, exc: Exception | None = None):
        self.status = status
        self.exc = exc
        self.calls: list[tuple[str, dict, dict]] = []

    async def __call__(self, url, payload, headers):
        self.calls.append((url, payload, headers))
        if self.exc is not None:
            raise self.exc
        return self.status


@pytest.fixture
def sources(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    (data / "logs").mkdir(parents=True)
    log = data / "logs" / "daemon.log"
    log.write_text(
        "INFO login: visit https://auth.example/cb?code=OAUTHCODE123\n"
        "INFO cfg: key=sk-abcdef0123456789abcdef0123456789\n",
        encoding="utf-8",
    )
    src = db.BundleSources(data_dir=data, daemon_log=log, stderr_log=None)
    monkeypatch.setattr(feedback_routes, "_sources", lambda: src)
    monkeypatch.setattr(feedback_routes, "_install_id", lambda: "inst_0123456789ab")
    monkeypatch.setattr(feedback_routes, "_ingest_endpoint",
                        lambda: "http://stub.invalid/ingest")
    return src


@pytest.fixture
def post(monkeypatch):
    fake = FakePost()
    monkeypatch.setattr(feedback_routes, "_post", fake)
    return fake


@pytest.fixture
def client(sources):
    app = FastAPI()
    app.include_router(feedback_routes.router)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_preview_returns_sanitised_text_and_its_size(client, post):
    async with client:
        r = await client.get("/api/v2/feedback/preview")
    assert r.status_code == 200
    body = r.json()
    assert body["bytes"] == len(body["text"].encode("utf-8"))
    assert "OAUTHCODE123" not in body["text"]
    assert "sk-abcdef" not in body["text"]
    assert "https://auth.example/cb?<redacted>" in body["text"]
    assert "install_id: inst_0123456789ab" in body["text"]
    assert post.calls == []  # preview never uploads


async def test_preview_carries_the_failure_context(client, post):
    async with client:
        r = await client.get("/api/v2/feedback/preview",
                             params={"kind": "dispatch_failed", "agent": "codex",
                                     "detail": "spawn: exit 127"})
    text = r.json()["text"]
    assert "context: dispatch_failed" in text and "agent: codex" in text
    assert "spawn: exit 127" in text


async def test_send_uploads_the_rebuilt_sanitised_bundle_with_the_message(client, post):
    async with client:
        r = await client.post("/api/v2/feedback/send",
                              json={"message": "codex login never finishes",
                                    "context": {"kind": "login_failed", "agent": "codex"}})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(post.calls) == 1
    url, payload, headers = post.calls[0]
    assert url == "http://stub.invalid/feedback"
    assert headers["X-Orbital-Telemetry-Token"]
    assert payload["install_id"] == "inst_0123456789ab"
    assert payload["message"] == "codex login never finishes"
    assert payload["context"] == {"kind": "login_failed", "agent": "codex"}
    assert "OAUTHCODE123" not in payload["bundle"]
    assert "sk-abcdef" not in payload["bundle"]
    assert body["bytes"] == len(payload["bundle"].encode("utf-8"))
    assert body["bytes"] <= db.MAX_UPLOAD_BYTES


async def test_send_with_an_empty_message_is_allowed(client, post):
    async with client:
        r = await client.post("/api/v2/feedback/send", json={"message": ""})
    assert r.status_code == 200 and post.calls[0][1]["message"] == ""


async def test_network_failure_is_a_502_and_one_warning(client, monkeypatch, caplog):
    fake = FakePost(exc=OSError("connection refused"))
    monkeypatch.setattr(feedback_routes, "_post", fake)
    with caplog.at_level(logging.WARNING, logger=feedback_routes.logger.name):
        async with client:
            r = await client.post("/api/v2/feedback/send", json={"message": "hi"})
    assert r.status_code == 502
    assert r.json()["detail"]["error"] == "upload_failed"
    warnings = [rec for rec in caplog.records
                if rec.levelno == logging.WARNING and "feedback" in rec.getMessage().lower()]
    assert len(warnings) == 1


async def test_old_server_without_the_route_is_a_clean_502(client, monkeypatch):
    """A telemetry server that predates /feedback answers 404; that must be
    the same user-facing "try later" failure, never a crash."""
    monkeypatch.setattr(feedback_routes, "_post", FakePost(status=404))
    async with client:
        r = await client.post("/api/v2/feedback/send", json={"message": "hi"})
    assert r.status_code == 502
    assert r.json()["detail"] == {"error": "upload_failed", "status": 404}


async def test_send_works_when_telemetry_is_disabled(client, post, monkeypatch):
    monkeypatch.setattr(feedback_routes, "_telemetry_enabled", lambda: False)
    async with client:
        p = await client.get("/api/v2/feedback/preview")
        r = await client.post("/api/v2/feedback/send", json={"message": "x"})
    assert p.json()["telemetry_enabled"] is False
    assert "telemetry_enabled: false" in p.json()["text"]
    assert r.status_code == 200 and len(post.calls) == 1


async def test_message_is_capped(client, post):
    async with client:
        r = await client.post("/api/v2/feedback/send", json={"message": "m" * 50_000})
    assert r.status_code == 200
    assert len(post.calls[0][1]["message"]) == db.MAX_MESSAGE_CHARS
