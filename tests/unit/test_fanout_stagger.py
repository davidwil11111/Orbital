# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 140 R3: fan-out stagger, plus the two worker-prompt fixes it needs to
pay off.

The provider's prefix cache is written only once a request has been
processed, so N workers fired in the same instant all miss (measured: three
concurrent identical 29k requests, all cached=0). The fanout now starts
worker 1, waits until its first LLM call returns a first token, then starts
workers 2..N, which then share worker 1's prefix. A hard timeout starts the
rest anyway; the stagger can never hang a fanout.

For the shared prefix to exist at all, the worker prompts must be identical
up to the brief: the per-task label no longer becomes the worker's identity
line (it made every worker's prompt differ at ~token 9), and a worker no
longer rebinds the parent's shared provider to its own session id (which
also moved the MANAGER onto the last worker's routing key after a fanout).
"""

import asyncio

import pytest

import agent_os.daemon_v2.sub_agent_manager as sam
from agent_os.agent.providers.types import StreamChunk, TokenUsage
from agent_os.daemon_v2.native_worker import NativeWorkerAdapter, WorkerDeps

from tests.unit.test_dispatch_fanout import (
    PID,
    SID,
    _StubToolRegistry,
    _make_manager,
    _make_registry_factory,
    _wait_until,
)


def _brief_of(messages) -> str:
    """Every user row's text: the brief may be followed by a separate
    runtime row, and a worker turn's later calls still carry it."""
    return "\n".join(str(m.get("content")) for m in messages if m.get("role") == "user")


class _GatedProvider:
    """One shared provider for the whole batch (as in production, where every
    worker reuses the parent's utility provider). Records when each brief's
    first stream call starts; the brief named ``lead`` blocks before its
    first chunk until ``release`` is set (or forever, if never set)."""

    def __init__(self, lead: str, *, lead_raises: bool = False):
        self.provider = "stub"
        self.model = "stub-model"
        self.sdk = "stub-sdk"
        self.lead = lead
        self.lead_raises = lead_raises
        self.release = asyncio.Event()
        self.started: dict[str, float] = {}
        self.first_token_at: float | None = None

    async def stream(self, messages, tools=None):
        brief = _brief_of(messages)
        loop = asyncio.get_event_loop()
        key = next((k for k in ("brief-a", "brief-b", "brief-c") if k in brief), brief)
        self.started.setdefault(key, loop.time())
        if key == self.lead:
            if self.lead_raises:
                raise RuntimeError("provider down")
            await self.release.wait()
            self.first_token_at = loop.time()
        yield StreamChunk(text=f"done {key}")
        yield StreamChunk(is_final=True, usage=TokenUsage(input_tokens=10, output_tokens=5))


def _manager(tmp_path, prov):
    """A fanout-wired SubAgentManager with the real LifecycleObserver, so a
    worker's terminal event reaches the join as in production."""
    from agent_os.daemon_v2.lifecycle_observer import LifecycleObserver

    mgr, rec = _make_manager(tmp_path, provider_factory=lambda: prov)
    observer = LifecycleObserver(rec, rec)
    observer.fanout_registry = mgr._fanout_registry
    mgr._lifecycle_observer = observer
    return mgr, rec


def _three_tasks():
    return [
        {"brief": "brief-a", "label": "task-a"},
        {"brief": "brief-b", "label": "task-b"},
        {"brief": "brief-c", "label": "task-c"},
    ]


@pytest.mark.asyncio
async def test_workers_2_to_n_start_after_worker_1_first_token(tmp_path):
    prov = _GatedProvider("brief-a")
    mgr, rec = _manager(tmp_path, prov)
    result = await mgr.dispatch_fanout(PID, _three_tasks(), session_id=SID)
    assert result.startswith("Fanout ")

    await _wait_until(lambda: "brief-a" in prov.started)
    await asyncio.sleep(0.2)
    # Worker 1 is in flight but has not produced a token: nobody else started.
    assert set(prov.started) == {"brief-a"}

    prov.release.set()
    await _wait_until(lambda: {"brief-b", "brief-c"} <= set(prov.started))
    assert prov.started["brief-b"] >= prov.first_token_at
    assert prov.started["brief-c"] >= prov.first_token_at

    await _wait_until(lambda: len(rec.injected) == 1)
    assert "3/3 succeeded" in rec.injected[0][1]


@pytest.mark.asyncio
async def test_timeout_starts_the_rest_anyway(tmp_path, monkeypatch):
    monkeypatch.setattr(sam, "FANOUT_STAGGER_TIMEOUT_S", 0.2)
    prov = _GatedProvider("brief-a")  # never released: worker 1 never yields
    mgr, rec = _manager(tmp_path, prov)
    await mgr.dispatch_fanout(PID, _three_tasks(), session_id=SID)

    await _wait_until(lambda: {"brief-b", "brief-c"} <= set(prov.started), timeout=2.0)
    assert prov.first_token_at is None
    t0 = prov.started["brief-a"]
    assert prov.started["brief-b"] - t0 >= 0.15

    prov.release.set()
    await _wait_until(lambda: len(rec.injected) == 1)


@pytest.mark.asyncio
async def test_worker_1_failing_releases_the_rest_without_waiting(tmp_path, monkeypatch):
    monkeypatch.setattr(sam, "FANOUT_STAGGER_TIMEOUT_S", 30)
    prov = _GatedProvider("brief-a", lead_raises=True)
    mgr, rec = _manager(tmp_path, prov)
    await mgr.dispatch_fanout(PID, _three_tasks(), session_id=SID)
    await _wait_until(lambda: {"brief-b", "brief-c"} <= set(prov.started), timeout=2.0)
    await _wait_until(lambda: len(rec.injected) == 1)
    assert "2/3 succeeded" in rec.injected[0][1]


@pytest.mark.asyncio
async def test_stop_during_stagger_never_starts_the_rest(tmp_path, monkeypatch):
    """A worker torn down before its delayed start must not run a turn after
    the stop, and still counts as interrupted so the join resolves."""
    monkeypatch.setattr(sam, "FANOUT_STAGGER_TIMEOUT_S", 30)
    prov = _GatedProvider("brief-a")
    mgr, rec = _manager(tmp_path, prov)

    await mgr.dispatch_fanout(PID, _three_tasks(), session_id=SID)
    await _wait_until(lambda: "brief-a" in prov.started)
    await mgr.stop_all(PID, session_id=SID)

    await _wait_until(lambda: len(rec.injected) == 1, timeout=3.0)
    assert set(prov.started) == {"brief-a"}
    content = rec.injected[0][1]
    assert content.count("[interrupted]") == 3


# ---------------------------------------------------------------------------
# Worker prompts share a prefix
# ---------------------------------------------------------------------------

class _RecordingProvider:
    def __init__(self):
        self.provider = "stub"
        self.model = "stub-model"
        self.sdk = "stub-sdk"
        self.systems: list[str] = []
        self.bound = None

    def bind_session_id(self, getter):
        self.bound = getter

    async def stream(self, messages, tools=None):
        self.systems.append(messages[0]["content"])
        yield StreamChunk(text="ok")
        yield StreamChunk(is_final=True, usage=TokenUsage(input_tokens=1, output_tokens=1))


def _deps(tmp_path, provider):
    return WorkerDeps(provider=provider, workspace=str(tmp_path), project_id=PID,
                      parent_session_id=SID, make_tool_registry=_make_registry_factory())


@pytest.mark.asyncio
async def test_worker_identity_does_not_carry_the_task_label(tmp_path):
    prov = _RecordingProvider()
    a = NativeWorkerAdapter(deps=_deps(tmp_path, prov), handle="worker:f-0",
                            display_name="Read INDEX.md", allowed_paths=None, forbidden_paths=None)
    b = NativeWorkerAdapter(deps=_deps(tmp_path, prov), handle="worker:f-1",
                            display_name="List instructions", allowed_paths=None, forbidden_paths=None)
    await a.send("brief one")
    n = len(prov.systems)
    await b.send("brief two")
    assert n >= 1 and len(prov.systems) >= n + 1
    assert prov.systems[0] == prov.systems[n]
    assert "Read INDEX.md" not in prov.systems[0]


def test_worker_does_not_rebind_the_parents_session_header(tmp_path):
    prov = _RecordingProvider()
    parent_getter = lambda: "parent-session"
    prov.bind_session_id(parent_getter)
    NativeWorkerAdapter(deps=_deps(tmp_path, prov), handle="worker:f-0",
                        display_name="t0", allowed_paths=None, forbidden_paths=None)
    NativeWorkerAdapter(deps=_deps(tmp_path, prov), handle="worker:f-1",
                        display_name="t1", allowed_paths=None, forbidden_paths=None)
    assert prov.bound is parent_getter


@pytest.mark.asyncio
async def test_first_token_event_set_by_first_chunk_and_by_turn_end(tmp_path):
    prov = _GatedProvider("brief-a")
    a = NativeWorkerAdapter(deps=_deps(tmp_path, prov), handle="worker:f-0",
                            display_name="t0", allowed_paths=None, forbidden_paths=None)
    assert not a.first_token.is_set()
    task = asyncio.create_task(a.send("brief-a"))
    await asyncio.sleep(0.1)
    assert not a.first_token.is_set()
    prov.release.set()
    await asyncio.wait_for(a.first_token.wait(), 2)
    await task

    failing = _GatedProvider("brief-a", lead_raises=True)
    b = NativeWorkerAdapter(deps=_deps(tmp_path, failing), handle="worker:f-1",
                            display_name="t1", allowed_paths=None, forbidden_paths=None)
    await b.send("brief-a")
    assert b.first_token.is_set()
