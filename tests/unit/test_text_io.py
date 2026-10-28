"""Spec 141 §1 A — read_text_lenient: one lenient reader for prompt-bound text.

The failing shape from the first feedback report: an otherwise-UTF-8 file
with a GBK tail appended by a Chinese-locale Windows shell (byte 0xb4 at
position 1649 → strict UTF-8 raised on every LLM call).
"""

from __future__ import annotations

import logging

import pytest

from agent_os.utils import text_io
from agent_os.utils.text_io import decode_lenient, read_text_lenient

HEAD = ("# Project State\n" + "状态：进行中 — all good so far.\n" * 60)
GBK_TAIL = "大量中文\n"


@pytest.fixture(autouse=True)
def _fresh_warnings():
    text_io._reset_warned_for_tests()
    yield
    text_io._reset_warned_for_tests()


@pytest.fixture
def windows_cp936(monkeypatch):
    monkeypatch.setattr(text_io, "_is_windows", lambda: True)
    monkeypatch.setattr(text_io, "_preferred_encoding", lambda: "cp936")


def _mixed_bytes() -> bytes:
    head = HEAD.encode("utf-8")
    assert len(head) > 1600
    data = head + GBK_TAIL.encode("gbk")
    assert data[len(head)] == 0xB4  # the reported byte, past position 1600
    return data


def test_pure_utf8(tmp_path):
    p = tmp_path / "a.md"
    p.write_bytes("héllo 中文\n".encode("utf-8"))
    assert read_text_lenient(str(p)) == "héllo 中文\n"


def test_utf8_bom_is_stripped(tmp_path):
    p = tmp_path / "a.md"
    p.write_bytes(b"\xef\xbb\xbf" + "中文\n".encode("utf-8"))
    assert read_text_lenient(str(p)) == "中文\n"


def test_pure_gbk_on_windows(tmp_path, windows_cp936):
    p = tmp_path / "a.md"
    p.write_bytes("用户写的说明\n第二行\n".encode("gbk"))
    assert read_text_lenient(str(p)) == "用户写的说明\n第二行\n"


def test_mixed_utf8_head_gbk_tail_on_windows(tmp_path, windows_cp936):
    p = tmp_path / "PROJECT_STATE.md"
    p.write_bytes(_mixed_bytes())
    assert read_text_lenient(str(p)) == HEAD + GBK_TAIL


def test_mixed_file_off_windows_keeps_utf8_head_and_replaces_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(text_io, "_is_windows", lambda: False)
    p = tmp_path / "PROJECT_STATE.md"
    p.write_bytes(_mixed_bytes())
    text = read_text_lenient(str(p))
    assert text.startswith(HEAD)  # the good half is untouched
    assert "�" in text[len(HEAD):]


def test_undecodable_garbage_becomes_replacement_chars(tmp_path, windows_cp936):
    p = tmp_path / "a.md"
    # 0x80 alone is invalid UTF-8 and an invalid GBK lead byte.
    p.write_bytes(b"ok line\n\x80\x80\xff junk\n")
    text = read_text_lenient(str(p))
    assert text.startswith("ok line\n")
    assert "�" in text
    assert text.endswith(" junk\n")


def test_missing_file_returns_none(tmp_path):
    assert read_text_lenient(str(tmp_path / "nope.md")) is None


def test_limit_caps_characters(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("中文abcdef", encoding="utf-8")
    assert read_text_lenient(str(p), limit=3) == "中文a"


def test_one_warning_per_path_per_process(tmp_path, windows_cp936, caplog):
    p = tmp_path / "PROJECT_STATE.md"
    p.write_bytes(_mixed_bytes())
    with caplog.at_level(logging.WARNING, logger="agent_os.utils.text_io"):
        read_text_lenient(str(p))
        read_text_lenient(str(p))
        read_text_lenient(str(p))
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert str(p) in msg and "cp936" in msg


def test_fast_path_does_not_warn(tmp_path, caplog):
    p = tmp_path / "a.md"
    p.write_text("fine\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="agent_os.utils.text_io"):
        assert read_text_lenient(str(p)) == "fine\n"
    assert not caplog.records


def test_decode_lenient_reports_fallbacks(windows_cp936):
    assert decode_lenient(b"plain")[1] == ()
    _, used = decode_lenient(_mixed_bytes())
    assert used == ("cp936",)


def test_utf8_preferred_encoding_on_windows_adds_no_codepage(monkeypatch):
    monkeypatch.setattr(text_io, "_is_windows", lambda: True)
    monkeypatch.setattr(text_io, "_preferred_encoding", lambda: "UTF-8")
    _, used = decode_lenient(_mixed_bytes())
    assert used == ("utf-8 (replacement chars)",)


def test_crlf_translated_like_text_mode(tmp_path):
    p = tmp_path / "a.md"
    p.write_bytes(b"one\r\ntwo\rthree\n")
    with open(p, "r", encoding="utf-8") as f:
        expected = f.read()
    assert read_text_lenient(str(p)) == expected == "one\ntwo\nthree\n"
