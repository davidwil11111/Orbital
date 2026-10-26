# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 134 A4 — the PID file knows which process wrote it.

Every desktop exit ends in ``os._exit`` (no ``atexit``), so ``daemon.pid`` was
always left behind, and any live process that later got that PID — Windows
reuses them fast — made every launch refuse to start (W7). The sidecar
``daemon.pid.meta`` records the writer's create time; a PID counts as live
only when the process holding it now was created then.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import psutil
import pytest

import agent_os.utils.pid_file as pf
from agent_os.utils.pid_file import (
    DaemonAlreadyRunning,
    acquire_pid_file,
    meta_path_for,
    read_pid_meta,
    release_pid_file,
)


@pytest.fixture(autouse=True)
def _fresh_owner():
    pf._active_pid_path = None
    yield
    pf._active_pid_path = None


def _write(pid_path, pid, create_time):
    pid_path.write_text(str(pid))
    meta_path_for(pid_path).write_text(json.dumps(
        {"pid": pid, "create_time": create_time, "exe": "x"}))


def test_acquire_writes_a_bare_pid_and_the_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_OS_PORT", "8123")
    pid_path = tmp_path / "daemon.pid"
    acquire_pid_file(pid_path)
    try:
        assert pid_path.read_text() == str(os.getpid())  # older builds parse it
        meta = read_pid_meta(pid_path)
        assert meta["pid"] == os.getpid()
        assert abs(meta["create_time"] - psutil.Process().create_time()) < 1
        assert meta["port"] == "8123"
    finally:
        release_pid_file(pid_path)
    assert not pid_path.exists()
    assert not meta_path_for(pid_path).exists()


def test_a_stale_file_naming_an_unrelated_live_process_boots(tmp_path):
    # §7 test 6: the PID is alive, but it is not the daemon that wrote it.
    other = os.getppid()
    pid_path = tmp_path / "daemon.pid"
    _write(pid_path, other, psutil.Process(other).create_time() - 3600)
    acquire_pid_file(pid_path)
    try:
        assert pid_path.read_text() == str(os.getpid())
    finally:
        release_pid_file(pid_path)


def test_the_live_daemon_that_wrote_the_file_is_still_refused(tmp_path):
    other = os.getppid()
    pid_path = tmp_path / "daemon.pid"
    _write(pid_path, other, psutil.Process(other).create_time())
    with pytest.raises(DaemonAlreadyRunning) as exc:
        acquire_pid_file(pid_path)
    assert exc.value.existing_pid == other


@pytest.fixture
def python_child():
    """A live process named like a daemon could be (a Python interpreter)."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    yield proc
    proc.kill()
    proc.wait()


def test_a_file_from_an_older_build_still_refuses_a_daemon_like_process(tmp_path, python_child):
    # No sidecar: a live PID that could be a daemon still refuses (downgrade-safe).
    pid_path = tmp_path / "daemon.pid"
    pid_path.write_text(str(python_child.pid))
    with pytest.raises(DaemonAlreadyRunning):
        acquire_pid_file(pid_path)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX sleep")
def test_a_file_from_an_older_build_naming_an_unrelated_process_boots(tmp_path):
    # The first launch after an upgrade: the old build left daemon.pid behind
    # and its PID now belongs to something that is not Orbital at all.
    other = subprocess.Popen(["sleep", "30"])
    try:
        pid_path = tmp_path / "daemon.pid"
        pid_path.write_text(str(other.pid))
        acquire_pid_file(pid_path)
        try:
            assert pid_path.read_text() == str(os.getpid())
            assert other.poll() is None  # left alone
        finally:
            release_pid_file(pid_path)
    finally:
        other.kill()
        other.wait()


def test_a_sidecar_for_another_pid_is_ignored(tmp_path, python_child):
    pid_path = tmp_path / "daemon.pid"
    pid_path.write_text(str(python_child.pid))
    meta_path_for(pid_path).write_text(json.dumps({"pid": 1, "create_time": 0.0}))
    with pytest.raises(DaemonAlreadyRunning):
        acquire_pid_file(pid_path)


def test_release_leaves_another_owners_sidecar(tmp_path):
    pid_path = tmp_path / "daemon.pid"
    _write(pid_path, os.getppid(), 1.0)
    release_pid_file(pid_path)
    assert pid_path.exists() and meta_path_for(pid_path).exists()
