# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Codex compresses older rollouts to ``rollout-*.jsonl.zst`` (spec 138).

On a dev Mac 418 of 487 rollouts were compressed and the history reader
opened only ``.jsonl``: most Codex history was invisible to the onboarding
scan, a git repo with 8 Codex sessions dropped out of the folder rows, and
an imported project's Codex sessions went missing from its session list
(the threads index keeps the uncompressed name).
"""

import json
import os
import sqlite3

import zstandard

from agent_os.onboarding import session_import
from agent_os.onboarding.sources import codex

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _rows(cwd, text="write the quarterly report"):
    return [
        {"type": "session_meta", "timestamp": "2026-07-23T10:00:00Z",
         "payload": {"id": "019f-zst", "cwd": cwd, "originator": "Codex Desktop",
                     "timestamp": "2026-07-23T10:00:00Z"}},
        {"type": "response_item", "timestamp": "2026-07-23T10:00:01Z",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": text}]}},
        {"type": "response_item", "timestamp": "2026-07-23T10:00:09Z",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "Here it is."}]}},
        {"type": "event_msg", "timestamp": "2026-07-23T10:00:10Z",
         "payload": {"type": "task_complete"}},
    ]


def _write(path, rows, *, compress):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = "".join(json.dumps(r) + "\n" for r in rows).encode("utf-8")
    if compress:
        path = str(path) + ".zst"
        data = zstandard.ZstdCompressor().compress(data)
    with open(path, "wb") as fh:
        fh.write(data)
    return str(path)


def test_a_compressed_rollout_reads_like_a_plain_one(tmp_path):
    cwd = str(tmp_path / "proj")
    plain = codex.SOURCE.read(_write(tmp_path / "a" / "rollout-1.jsonl", _rows(cwd), compress=False))
    packed = codex.SOURCE.read(_write(tmp_path / "b" / "rollout-1.jsonl", _rows(cwd), compress=True))
    assert packed is not None
    assert (packed.native_id, packed.cwd, packed.first_user_text, packed.user_turns,
            packed.ended_at) == (plain.native_id, plain.cwd, plain.first_user_text,
                                 plain.user_turns, plain.ended_at)


def test_session_files_lists_compressed_rollouts_once(tmp_path, monkeypatch):
    monkeypatch.delenv("ORBITAL_CODEX_HOME", raising=False)  # conftest stages an empty one
    home = tmp_path / "home"
    day = home / ".codex" / "sessions" / "2026" / "07" / "23"
    _write(day / "rollout-old.jsonl", _rows("/p"), compress=True)
    _write(day / "rollout-mid.jsonl", _rows("/p"), compress=False)
    _write(day / "rollout-mid.jsonl", _rows("/p"), compress=True)   # mid-compression
    _write(day / "rollout-new.jsonl", _rows("/p"), compress=False)
    names = sorted(os.path.basename(p) for p in codex.SOURCE.session_files(str(home)))
    assert names == ["rollout-mid.jsonl", "rollout-new.jsonl", "rollout-old.jsonl.zst"]


def test_a_compressed_rollout_converts_for_import(tmp_path):
    path = _write(tmp_path / "rollout-1.jsonl", _rows(str(tmp_path)), compress=True)
    converted = session_import.convert_transcript("codex", path)
    texts = [r.get("content") for r in converted.rows]
    assert any("write the quarterly report" in str(t) for t in texts)
    assert any("Here it is." in str(t) for t in texts)


def test_the_threads_index_row_finds_the_compressed_file(tmp_path):
    rollout = str(tmp_path / "rollout-1.jsonl")
    _write(rollout, _rows(str(tmp_path)), compress=True)       # only .zst on disk
    row = {"id": "019f-zst", "rollout_path": rollout, "cwd": str(tmp_path),
           "title": "Quarterly report", "first_user_message": "write the quarterly report",
           "updated_at_ms": 1785000000000, "source": "vscode"}
    native = session_import._codex_native_from_row(row)
    assert native is not None and native.path == rollout + ".zst"


def test_the_dependency_is_declared_and_bundled():
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        assert '"zstandard>=' in f.read()
    for spec in ("agentos.spec", "agentos-macos.spec"):
        with open(os.path.join(ROOT, "agent_os", "desktop", spec), encoding="utf-8") as f:
            assert "'zstandard.backend_c'" in f.read(), spec
