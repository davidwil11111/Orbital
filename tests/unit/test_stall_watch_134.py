# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 134 A3 — the loop-stall detector.

A plain thread pings the event loop every second; when the loop does not
answer within the threshold it writes every thread's stack (plus the pools'
queue depths) to ``logs/hang.log`` — once per stall, capped in size — so the
next freeze report arrives as stack traces.
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_os.utils import stall_watch
from agent_os.utils.stall_watch import StallWatch


def _run_with_watch(tmp_path, body, **kwargs):
    log = tmp_path / "logs" / "hang.log"

    async def scenario():
        loop = asyncio.get_running_loop()
        watch = StallWatch(loop, str(log), **kwargs)
        watch.start()
        try:
            await asyncio.sleep(0.3)
            await body()
            await asyncio.sleep(0.5)
        finally:
            watch.stop()
        return watch

    watch = asyncio.run(scenario())
    return log, watch


def test_a_blocked_loop_writes_one_hang_log_with_all_stacks(tmp_path):
    async def block():
        time.sleep(2.0)  # the bug: something sync on the loop

    log, watch = _run_with_watch(tmp_path, block, interval=0.1, threshold=0.5)
    text = log.read_text(encoding="utf-8")
    assert text.count("=== event loop stalled") == 1, text[:2000]
    # faulthandler's all-threads dump: the loop thread is caught in the sleep.
    assert "Thread 0x" in text or "Current thread 0x" in text
    assert "time.sleep" in text or "block" in text
    assert "default executor" in text
    assert "loop answered again after" in text
    assert watch.stalls == 1


def test_a_healthy_loop_writes_nothing(tmp_path):
    async def idle():
        for _ in range(10):
            await asyncio.sleep(0.1)

    log, watch = _run_with_watch(tmp_path, idle, interval=0.05, threshold=0.5)
    assert not log.exists()
    assert watch.stalls == 0


def test_pool_queue_depth_is_reported(tmp_path):
    pool = ThreadPoolExecutor(max_workers=1)
    release = threading.Event()
    pool.submit(release.wait, 10)
    pool.submit(release.wait, 10)
    stall_watch.register_executor("test-pool", pool)
    try:
        async def block():
            time.sleep(1.5)

        log, _ = _run_with_watch(tmp_path, block, interval=0.1, threshold=0.5)
        text = log.read_text(encoding="utf-8")
        assert "test-pool: 1 thread(s), 1 queued" in text
    finally:
        release.set()
        stall_watch.unregister_executor("test-pool")
        pool.shutdown(wait=True)


def test_the_log_is_capped(tmp_path):
    log = tmp_path / "logs" / "hang.log"
    log.parent.mkdir(parents=True)
    log.write_bytes(b"x" * (stall_watch.MAX_BYTES + 10))

    async def block():
        time.sleep(1.5)

    _run_with_watch(tmp_path, block, interval=0.1, threshold=0.5)
    assert log.stat().st_size < stall_watch.MAX_BYTES
    assert (tmp_path / "logs" / "hang.log.1").exists()
    assert "=== event loop stalled" in log.read_text(encoding="utf-8")


def test_a_wall_clock_jump_is_not_a_stall(tmp_path):
    # Laptop sleep / clock change: the wall clock moves far more than the
    # monotonic one, so the missed answer is not counted.
    offset = {"v": 0.0}

    def wall():
        offset["v"] += 10.0
        return time.time() + offset["v"]

    async def block():
        time.sleep(1.5)

    log, watch = _run_with_watch(tmp_path, block, interval=0.1, threshold=0.5, wall=wall)
    assert not log.exists()
    assert watch.stalls == 0


def test_start_and_stop_module_helpers(tmp_path):
    async def scenario():
        loop = asyncio.get_running_loop()
        watch = stall_watch.start(loop, str(tmp_path / "hang.log"))
        assert stall_watch.start(loop, str(tmp_path / "hang.log")) is watch
        stall_watch.stop()
        assert not watch.is_alive()

    asyncio.run(scenario())


def test_create_app_starts_and_stops_the_watch(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from agent_os.api.app import create_app

    monkeypatch.setenv("AGENT_OS_TELEMETRY_DISABLED", "1")
    app = create_app(data_dir=str(tmp_path / "data"))
    with TestClient(app):
        watch = stall_watch._active
        assert watch is not None and watch.is_alive()
        assert watch._log_path == str(tmp_path / "data" / "logs" / "hang.log")
    assert not watch.is_alive()
