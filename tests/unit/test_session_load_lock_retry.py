# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Disk reads of a session wait out a transiently held file lock.

On Windows the sentinel lock is per descriptor, so a worker-thread read
(recap build, hydrate-on-inject) collides with the loop thread's own append
and ``Session.load`` raises ``FileLockError`` although the writer holds the
lock for milliseconds. Seen on the spec 127 V2 run (2026-10-22): "recap
build failed ... dispatching without".
"""

from unittest.mock import patch

import pytest

from agent_os.daemon_v2 import agent_manager as am
from agent_os.utils.file_lock import FileLockError


def test_retries_until_the_lock_frees(monkeypatch):
    calls = []
    sentinel = object()

    def flaky_load(target):
        calls.append(target)
        if len(calls) < 3:
            raise FileLockError("Session file is locked by another process: x.lock")
        return sentinel

    monkeypatch.setattr(am, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))
    with patch.object(am.Session, "load", side_effect=flaky_load):
        assert am._load_session_with_lock_retry("x.jsonl", attempts=5, backoff_s=0) is sentinel
    assert calls == ["x.jsonl"] * 3


def test_gives_up_after_the_attempts_with_the_last_error():
    def always_locked(target):
        raise FileLockError("Session file is locked by another process: x.lock")

    with patch.object(am.Session, "load", side_effect=always_locked):
        with pytest.raises(FileLockError, match="locked by another process"):
            am._load_session_with_lock_retry("x.jsonl", attempts=3, backoff_s=0)


def test_other_errors_are_not_retried():
    calls = []

    def boom(target):
        calls.append(1)
        raise OSError("disk")

    with patch.object(am.Session, "load", side_effect=boom):
        with pytest.raises(OSError):
            am._load_session_with_lock_retry("x.jsonl", attempts=5, backoff_s=0)
    assert len(calls) == 1


def test_load_session_from_disk_goes_through_the_retry():
    import inspect
    src = inspect.getsource(am.AgentManager._load_session_from_disk)
    assert "_load_session_with_lock_retry(target)" in src
    assert "Session.load(target)" not in src
