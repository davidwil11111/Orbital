# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The window waits on the daemon's first answer, so startup must not hold
the event loop (batch 1026 DMG, 2026-10-27: daemon start → window went from
2.9 s to 5.7–6.8 s and the Dock icon dropped out).

Two causes, both measured against copies of real sessions:

- The schedule tick's first ``pytz.timezone()`` builds pytz's zone list on
  the loop (a file check per zone). While the spec 136 startup sweep's
  threads hold the GIL, each check waits its turn: 0.2 s became 1.2–1.8 s
  from source and 5.9 s in the packaged first launch (hang.log).
- That sweep started the moment the daemon did, competing with the boot.
"""

import asyncio
import threading
import time
from unittest.mock import MagicMock

from agent_os.api import app as app_module
from agent_os.api.routes import settings as routes
from agent_os.daemon_v2 import trigger_manager as tm_module
from agent_os.daemon_v2.trigger_manager import TriggerManager

# conftest's autouse fixture replaces the hook for every test; keep the real
# one, taken at import (collection), before that fixture runs.
_REAL_START_SUB_AGENT_STATUS = app_module.start_sub_agent_status


# ---------------------------------------------------------------------------
# The tick builds pytz's zone list off the loop, before its first evaluation
# ---------------------------------------------------------------------------

def test_the_zone_list_is_built_off_the_loop_before_the_first_tick(monkeypatch):
    order = []

    def warm():
        order.append(("warm", threading.current_thread() is threading.main_thread()))

    monkeypatch.setattr(tm_module, "_warm_timezones", warm)
    tm = TriggerManager(MagicMock(), MagicMock())

    async def evaluate():
        order.append(("evaluate", None))
        tm._running = False

    tm._evaluate_due_triggers = evaluate
    tm.tick_interval_seconds = 0

    async def scenario():
        tm._running = True
        await tm._tick_loop()

    asyncio.run(scenario())
    assert order == [("warm", False), ("evaluate", None)]


def test_a_failed_warm_up_still_evaluates(monkeypatch):
    def warm():
        raise OSError("zoneinfo unreadable")

    monkeypatch.setattr(tm_module, "_warm_timezones", warm)
    tm = TriggerManager(MagicMock(), MagicMock())
    seen = []

    async def evaluate():
        seen.append(True)
        tm._running = False

    tm._evaluate_due_triggers = evaluate
    tm.tick_interval_seconds = 0

    async def scenario():
        tm._running = True
        await tm._tick_loop()

    asyncio.run(scenario())
    assert seen == [True]


def test_warm_timezones_fills_pytz_zone_set():
    import pytz

    tm_module._warm_timezones()
    assert "Asia/Shanghai" in pytz.all_timezones_set


# ---------------------------------------------------------------------------
# The startup sweep waits until the boot is done
# ---------------------------------------------------------------------------

class _Engine:
    def __init__(self):
        self._check_all_cache = None
        self.warmed = []

    def set_status_listener(self, _fn):
        pass

    def warm(self):
        self.warmed.append(time.monotonic())


def test_the_daemon_start_hook_delays_the_first_sweep(monkeypatch):
    seen = {}

    def record(engine, ws, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(routes, "start_sub_agent_status_updates", record)
    _REAL_START_SUB_AGENT_STATUS(object(), object())
    assert seen.get("warm_after", 0) >= 5


def test_a_delayed_sweep_starts_after_the_delay():
    engine = _Engine()

    async def scenario():
        started = time.monotonic()
        routes.start_sub_agent_status_updates(engine, MagicMock(), warm_after=0.3)
        await asyncio.sleep(0.1)
        assert engine.warmed == []
        await asyncio.sleep(0.4)
        assert len(engine.warmed) == 1
        assert engine.warmed[0] - started >= 0.3

    asyncio.run(scenario())


def test_a_delayed_sweep_is_skipped_when_a_read_already_swept():
    engine = _Engine()

    async def scenario():
        routes.start_sub_agent_status_updates(engine, MagicMock(), warm_after=0.2)
        engine._check_all_cache = ([], time.monotonic() + 60)   # Settings opened first
        await asyncio.sleep(0.4)
        assert engine.warmed == []

    asyncio.run(scenario())


def test_without_a_delay_the_sweep_starts_at_once():
    engine = _Engine()

    async def scenario():
        routes.start_sub_agent_status_updates(engine, MagicMock())
        assert len(engine.warmed) == 1

    asyncio.run(scenario())
