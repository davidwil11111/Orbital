# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Event-loop stall detector (spec 134 A3).

The daemon is one asyncio loop, and the desktop window is served by the same
process, so a blocked loop is a frozen app. The Windows freeze report came
with no evidence: by the time anyone looked, the process had been killed.
This turns the next one into stack traces.

A plain thread pings the loop (``call_soon_threadsafe``) every ``interval``
seconds. When the loop does not answer within ``threshold`` seconds, the
thread writes ``faulthandler.dump_traceback(all_threads=True)`` plus the
thread pools' sizes and queue depths to ``logs/hang.log``:

- once per stall (and at most once per ``min_gap`` seconds), with a line
  when the loop answers again;
- the file is capped at ``MAX_BYTES``: past it, it moves to ``hang.log.1``;
- a missed answer while the wall clock jumped well past the monotonic clock
  (laptop sleep, a clock change) is not counted.

Costs one wake-up per second. The feedback bundle (spec 113) includes the
file.
"""

from __future__ import annotations

import asyncio
import faulthandler
import logging
import os
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

INTERVAL_S = 1.0
THRESHOLD_S = 5.0
MAX_BYTES = 2 * 1024 * 1024
MIN_GAP_S = 30.0
#: How far the wall clock may run ahead of the monotonic one before a missed
#: answer is put down to sleep or a clock change rather than a stall.
_CLOCK_JUMP_S = 2.0

_executors: dict[str, object] = {}
_executors_lock = threading.Lock()
_active: "StallWatch | None" = None


def register_executor(name: str, executor) -> None:
    """Report *executor*'s threads and queue depth in each dump."""
    with _executors_lock:
        _executors[name] = executor


def unregister_executor(name: str) -> None:
    with _executors_lock:
        _executors.pop(name, None)


def _pool_line(name: str, pool) -> str:
    try:
        threads = len(getattr(pool, "_threads", ()) or ())
        queue = getattr(pool, "_work_queue", None)
        queued = queue.qsize() if queue is not None else "?"
        cap = getattr(pool, "_max_workers", "?")
        return f"{name}: {threads} thread(s), {queued} queued (max {cap})"
    except Exception as exc:  # never let the report itself fail
        return f"{name}: unavailable ({type(exc).__name__})"


class StallWatch(threading.Thread):
    """Watches one event loop from its own daemon thread."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        log_path: str,
        *,
        interval: float = INTERVAL_S,
        threshold: float = THRESHOLD_S,
        max_bytes: int = MAX_BYTES,
        min_gap: float = MIN_GAP_S,
        wall=time.time,
        mono=time.monotonic,
    ) -> None:
        super().__init__(name="orbital-stall-watch", daemon=True)
        self._log_path = log_path
        self._interval = interval
        self._threshold = threshold
        self._max_bytes = max_bytes
        self._min_gap = min_gap
        self._wall = wall
        self._mono = mono
        self.loop = loop
        self._stop_event = threading.Event()
        self._pending: threading.Event | None = None
        self._last_dump: float | None = None
        self.stalls = 0

    # -- lifecycle -----------------------------------------------------

    def stop(self) -> None:
        self._stop_event.set()
        pending = self._pending
        if pending is not None:
            pending.set()  # wake a wait in progress
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=self._threshold + self._interval + 1)

    def run(self) -> None:
        while not self._stop_event.is_set():
            answered = threading.Event()
            self._pending = answered
            mono0, wall0 = self._mono(), self._wall()
            try:
                self.loop.call_soon_threadsafe(answered.set)
            except RuntimeError:
                return  # the loop is closed
            if answered.wait(self._threshold):
                self._stop_event.wait(self._interval)
                continue
            mono1, wall1 = self._mono(), self._wall()
            if (wall1 - wall0) - (mono1 - mono0) > _CLOCK_JUMP_S:
                continue  # slept or the clock moved; not a stall
            if self._last_dump is not None and mono1 - self._last_dump < self._min_gap:
                self._wait_for_answer(answered)
                continue
            self._last_dump = mono1
            self.stalls += 1
            self._dump(mono1 - mono0)
            waited = self._wait_for_answer(answered)
            if waited is not None:
                self._append(f"loop answered again after "
                             f"{(mono1 - mono0) + waited:.1f} s\n\n")
                logger.warning("Event loop answered again after %.1f s",
                               (mono1 - mono0) + waited)

    def _wait_for_answer(self, answered: threading.Event) -> float | None:
        start = self._mono()
        while not answered.wait(self._interval):
            if self._stop_event.is_set():
                return None
        if self._stop_event.is_set():
            return None
        return self._mono() - start

    # -- the dump ------------------------------------------------------

    def _rotate_if_full(self) -> None:
        try:
            if os.path.getsize(self._log_path) >= self._max_bytes:
                os.replace(self._log_path, self._log_path + ".1")
        except OSError:
            pass

    def _append(self, text: str) -> None:
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass

    def _dump(self, stalled_for: float) -> None:
        logger.warning(
            "Event loop has not answered for %.1f s; writing all thread "
            "stacks to %s", stalled_for, self._log_path)
        try:
            os.makedirs(os.path.dirname(self._log_path) or ".", exist_ok=True)
            self._rotate_if_full()
            pools = [_pool_line("default executor",
                                getattr(self.loop, "_default_executor", None))]
            with _executors_lock:
                pools += [_pool_line(name, pool) for name, pool in _executors.items()]
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(
                    f"=== event loop stalled: no answer for {stalled_for:.1f} s "
                    f"at {now} (pid {os.getpid()}, "
                    f"{threading.active_count()} threads) ===\n"
                )
                f.write("\n".join(pools) + "\n\n")
                f.flush()
                faulthandler.dump_traceback(file=f, all_threads=True)
                f.write("\n")
        except Exception:
            logger.exception("Could not write %s", self._log_path)


def start(loop: asyncio.AbstractEventLoop, log_path: str, **kwargs) -> StallWatch:
    """Start the process-wide watch (idempotent while it runs)."""
    global _active
    if _active is not None and _active.is_alive():
        if _active.loop is loop:
            return _active
        _active.stop()  # a previous app's loop (tests build several)
    _active = StallWatch(loop, log_path, **kwargs)
    _active.start()
    return _active


def stop() -> None:
    global _active
    watch, _active = _active, None
    if watch is not None:
        watch.stop()
