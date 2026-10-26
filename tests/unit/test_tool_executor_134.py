# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 134 A2 — sync tools run on their own bounded pool.

The agent loop used ``asyncio.to_thread`` for sync tools, i.e. the event
loop's default pool (``min(32, cpu + 4)`` threads: 8 on a 4-core laptop). A
stuck shell call held a thread for as long as its script ran (W1), Stop could
not free it (W2), and once they piled up every request handler that needs a
thread queued behind them (W3). The tools now get a dedicated 16-thread pool,
so tool waits can never starve request handlers.
"""

from __future__ import annotations

import ast
import asyncio
import contextvars
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent_os.agent import loop as loop_mod

LOOP_SOURCE = Path(loop_mod.__file__)


def test_sixteen_stuck_tool_calls_do_not_delay_to_thread_elsewhere():
    release = threading.Event()

    async def scenario():
        loop = asyncio.get_running_loop()
        # A small default pool, so the old wiring would starve it for sure.
        loop.set_default_executor(ThreadPoolExecutor(max_workers=2))
        stuck = [asyncio.ensure_future(loop_mod.run_sync_tool(release.wait, 30))
                 for _ in range(16)]
        await asyncio.sleep(0.2)
        started = time.monotonic()
        value = await asyncio.wait_for(asyncio.to_thread(lambda: 42), timeout=2)
        waited = time.monotonic() - started
        release.set()
        await asyncio.gather(*stuck)
        return value, waited

    value, waited = asyncio.run(scenario())
    assert value == 42 and waited < 1.0


def test_the_tool_pool_is_bounded_at_sixteen():
    pool = loop_mod.tool_executor()
    assert isinstance(pool, ThreadPoolExecutor)
    assert pool._max_workers == 16
    assert loop_mod.tool_executor() is pool


def test_run_sync_tool_keeps_context_vars():
    var: contextvars.ContextVar[str] = contextvars.ContextVar("v", default="unset")

    async def scenario():
        var.set("set-in-loop")
        return await loop_mod.run_sync_tool(var.get)

    assert asyncio.run(scenario()) == "set-in-loop"


def test_the_loop_no_longer_runs_tools_on_the_default_pool():
    """Every ``self._tool_registry.execute`` goes through ``run_sync_tool``."""
    tree = ast.parse(LOOP_SOURCE.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "to_thread":
            continue
        if node.args and ast.unparse(node.args[0]).endswith("_tool_registry.execute"):
            offenders.append(node.lineno)
    assert offenders == [], f"tool calls on the default pool at lines {offenders}"
