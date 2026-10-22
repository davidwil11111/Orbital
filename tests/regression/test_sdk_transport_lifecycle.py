# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for SDKTransport reader lifecycle.

Originally for two coupled defects in
``agent_os/agent/transports/sdk_transport.py`` (see
``TASK-fix-sdk-transport-lifecycle.md``):

1. ``dispatch()`` must keep a strong reference to the background
   response-consumption task, otherwise the event loop can GC it
   mid-stream and emit ``"Task was destroyed but it is pending"`` warnings.
2. ``stop()`` must cancel (and await) the background task BEFORE
   disconnecting the SDK client, so the task does not observe a None
   client mid-iteration and surface spurious errors.

Spec 129 replaced the per-dispatch consumer with ONE persistent reader
(``_reader_task``) started at connect and cancelled at stop; the same two
guarantees now hold for it. A second dispatch while one is pending no longer
cancels anything — it closes the prior dispatch's boundary as ``stopped``
and the reader carries on.

All tests use asyncio mocks — no real subprocess, no network, no SDK
binary. Behavior is identical on macOS and Windows.
"""

from __future__ import annotations

import asyncio
import gc
import logging

import pytest

from agent_os.agent.transports.sdk_transport import SDKTransport


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #


class _SlowReceiveClient:
    """Mock ClaudeSDKClient whose ``receive_messages`` yields very slowly.

    Each iteration step sleeps ``delay`` seconds. The generator never
    terminates on its own — useful for exercising cancellation paths.
    ``disconnect`` is awaitable and records the call.
    """

    def __init__(self, delay: float = 0.05) -> None:
        self._delay = delay
        self.query_calls: list[str] = []
        self.disconnect_called = False
        self._receive_started = asyncio.Event()

    async def query(self, message: str) -> None:
        self.query_calls.append(message)

    def receive_messages(self):
        return self._receive_gen()

    async def _receive_gen(self):
        self._receive_started.set()
        # Yield forever; each iteration awaits so cancellation can take
        # effect between yields.
        while True:
            await asyncio.sleep(self._delay)
            # We intentionally yield nothing that maps to an event — the
            # transport's _message_to_events() returns [] for unknown
            # message types, so the queue stays empty.
            yield object()

    async def disconnect(self) -> None:
        self.disconnect_called = True


def _install_slow_client(transport: SDKTransport, delay: float = 0.05) -> _SlowReceiveClient:
    client = _SlowReceiveClient(delay=delay)
    transport._client = client  # type: ignore[assignment]
    transport._alive = True
    return client


def _drain(transport):
    events = []
    while not transport._event_queue.empty():
        events.append(transport._event_queue.get_nowait())
    return events


# --------------------------------------------------------------------- #
# 1. The reader is strongly referenced
# --------------------------------------------------------------------- #


async def test_reader_strongly_referenced_after_dispatch():
    """dispatch() must leave the persistent reader referenced on the
    transport and alive across gc cycles."""
    transport = SDKTransport()
    _install_slow_client(transport, delay=0.05)

    await transport.dispatch("hello")

    assert transport._reader_task is not None, "dispatch() must keep the reader on self"
    assert not transport._reader_task.done(), "reader should still be running"

    for _ in range(3):
        gc.collect()

    assert transport._reader_task is not None
    assert not transport._reader_task.done()
    assert transport._reader_task in asyncio.all_tasks()

    transport._reader_task.cancel()
    try:
        await asyncio.wait_for(
            asyncio.gather(transport._reader_task, return_exceptions=True),
            timeout=2.0,
        )
    except asyncio.TimeoutError:
        pass


# --------------------------------------------------------------------- #
# 2. stop() cancels the reader within its timeout
# --------------------------------------------------------------------- #


async def test_stop_cancels_reader_within_timeout():
    """stop() must cancel the reader and return within the 5s budget."""
    transport = SDKTransport()
    client = _install_slow_client(transport, delay=0.05)

    await transport.dispatch("hello")
    reader = transport._reader_task
    assert reader is not None

    # Wait until the receive_messages generator has actually started so
    # the cancellation path is exercised mid-iteration.
    await asyncio.wait_for(client._receive_started.wait(), timeout=2.0)

    loop = asyncio.get_running_loop()
    started = loop.time()
    await transport.stop()
    elapsed = loop.time() - started

    assert elapsed < 6.0, f"stop() took {elapsed:.2f}s, expected <6s"
    assert reader.done(), "reader should be done after stop()"
    assert reader.cancelled() or reader.exception() is None

    # Transport state is fully torn down.
    assert transport._reader_task is None
    assert transport._client is None
    assert transport._alive is False
    assert client.disconnect_called
    # The pending dispatch's boundary was written on the way out.
    tcs = [e for e in _drain(transport) if e.event_type == "turn_complete"]
    assert [(t.data["origin"], t.data["cause"]) for t in tcs] == [("dispatch", "stopped")]


# --------------------------------------------------------------------- #
# 3. stop() with no dispatch is a no-op
# --------------------------------------------------------------------- #


async def test_stop_without_dispatch_is_noop():
    """Calling stop() before any dispatch() must complete quickly and
    not raise — no reader exists to cancel."""
    transport = SDKTransport()

    loop = asyncio.get_running_loop()
    started = loop.time()
    await transport.stop()
    elapsed = loop.time() - started

    assert elapsed < 1.0
    assert transport._reader_task is None
    assert transport._alive is False


# --------------------------------------------------------------------- #
# 4. Reader exceptions are logged, not silently swallowed
# --------------------------------------------------------------------- #


async def test_reader_exception_logged_not_swallowed(caplog):
    """When the reader raises, the error and stack trace must surface via
    ``logger.error(..., exc_info=...)`` and no ``"Task was destroyed"``
    warning must appear."""
    transport = SDKTransport()
    synthetic = RuntimeError("synthetic")

    async def boom() -> None:
        raise synthetic

    transport._reader_loop = boom  # type: ignore[assignment]
    _install_slow_client(transport, delay=0.05)

    with caplog.at_level(logging.ERROR, logger="agent_os.agent.transports.sdk_transport"):
        await transport.dispatch("hello")
        assert transport._reader_task is not None
        try:
            await asyncio.wait_for(
                asyncio.gather(transport._reader_task, return_exceptions=True),
                timeout=2.0,
            )
        except asyncio.TimeoutError:
            pytest.fail("reader did not terminate")
        await asyncio.sleep(0)

    matching = [
        r for r in caplog.records
        if r.levelno >= logging.ERROR and "synthetic" in (r.getMessage() + repr(r.exc_info))
    ]
    assert matching, (
        f"expected ERROR log with 'synthetic'; got {[r.getMessage() for r in caplog.records]}"
    )
    assert any(r.exc_info is not None for r in matching)
    task_destroyed = [
        r for r in caplog.records
        if "Task was destroyed but it is pending" in r.getMessage()
    ]
    assert not task_destroyed, "reader handling must not leak a GC warning"


# --------------------------------------------------------------------- #
# 5. stop() is idempotent
# --------------------------------------------------------------------- #


async def test_stop_is_idempotent():
    transport = SDKTransport()
    _install_slow_client(transport, delay=0.05)

    await transport.dispatch("hello")
    await transport.stop()
    await transport.stop()

    assert transport._reader_task is None
    assert transport._client is None
    assert transport._alive is False


# --------------------------------------------------------------------- #
# 6. dispatch() while a dispatch is pending — the prior boundary closes
#    as stopped; the reader is NOT cancelled
# --------------------------------------------------------------------- #


async def test_dispatch_while_pending_closes_prior_boundary_and_keeps_the_reader(caplog):
    transport = SDKTransport()
    client = _install_slow_client(transport, delay=5.0)

    await transport.dispatch("first")
    reader = transport._reader_task
    assert reader is not None and not reader.done()
    await asyncio.wait_for(client._receive_started.wait(), timeout=2.0)

    with caplog.at_level(logging.WARNING, logger="agent_os.agent.transports.sdk_transport"):
        await transport.dispatch("second")
        gc.collect()
        await asyncio.sleep(0)

    assert transport._reader_task is reader and not reader.done()
    assert client.query_calls == ["first", "second"]
    tcs = [e for e in _drain(transport) if e.event_type == "turn_complete"]
    assert [(t.data["origin"], t.data["cause"]) for t in tcs] == [("dispatch", "stopped")]
    assert transport.dispatch_pending is True
    assert any("dispatch while a dispatch is pending" in r.message for r in caplog.records)
    destroyed_warnings = [
        r for r in caplog.records
        if "Task was destroyed but it is pending" in r.getMessage()
    ]
    assert not destroyed_warnings

    reader.cancel()
    await asyncio.gather(reader, return_exceptions=True)


# --------------------------------------------------------------------- #
# 7. dispatch() after the reader ended — it is started again
# --------------------------------------------------------------------- #


async def test_dispatch_restarts_a_finished_reader(caplog):
    transport = SDKTransport()
    client = _install_slow_client(transport, delay=0.01)

    await transport.dispatch("first")
    first_reader = transport._reader_task
    first_reader.cancel()
    await asyncio.gather(first_reader, return_exceptions=True)
    assert first_reader.done()
    _drain(transport)

    with caplog.at_level(logging.WARNING, logger="agent_os.agent.transports.sdk_transport"):
        await transport.dispatch("second")

    second_reader = transport._reader_task
    assert second_reader is not None
    assert second_reader is not first_reader
    assert not second_reader.done()
    assert client.query_calls == ["first", "second"]

    second_reader.cancel()
    await asyncio.gather(second_reader, return_exceptions=True)
