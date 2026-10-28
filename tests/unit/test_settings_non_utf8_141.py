"""Spec 141 follow-up — GET /settings reads the user-owned preferences and
user-memory files leniently: a GBK file must not 500 the settings page, and
the file is never rewritten (user-owned, no self-heal)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from agent_os.utils import text_io

GBK_TEXT = "回答请用简体中文。\n"


@pytest.fixture(autouse=True)
def _windows_cp936(monkeypatch):
    text_io._reset_warned_for_tests()
    monkeypatch.setattr(text_io, "_is_windows", lambda: True)
    monkeypatch.setattr(text_io, "_preferred_encoding", lambda: "cp936")
    yield
    text_io._reset_warned_for_tests()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    os.makedirs(str(tmp_path / "home"), exist_ok=True)
    from agent_os.api.app import create_app
    app = create_app(data_dir=str(tmp_path / "data"))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_gbk_preferences_file_returns_200_decoded_and_untouched(client, tmp_path):
    prefs = tmp_path / "prefs.md"
    raw = GBK_TEXT.encode("gbk")
    prefs.write_bytes(raw)
    assert client.put("/api/v2/settings",
                      json={"user_preferences_path": str(prefs)}).status_code == 200

    resp = client.get("/api/v2/settings")

    assert resp.status_code == 200
    assert resp.json()["user_preferences_content"] == GBK_TEXT
    assert prefs.read_bytes() == raw


def test_gbk_user_memory_file_returns_200_decoded_and_untouched(client, tmp_path):
    memory = tmp_path / "user_memory.md"
    raw = "- likes tea\n".encode() + "- 喜欢喝茶\n".encode("gbk")
    memory.write_bytes(raw)
    assert client.put("/api/v2/settings",
                      json={"user_memory_path": str(memory)}).status_code == 200

    resp = client.get("/api/v2/settings")

    assert resp.status_code == 200
    assert resp.json()["user_memory_content"] == "- likes tea\n- 喜欢喝茶\n"
    assert memory.read_bytes() == raw


def test_missing_preferences_file_still_reads_empty(client, tmp_path):
    assert client.put("/api/v2/settings",
                      json={"user_preferences_path": str(tmp_path / "nope.md")}).status_code == 200
    resp = client.get("/api/v2/settings")
    assert resp.status_code == 200
    assert resp.json()["user_preferences_content"] == ""
