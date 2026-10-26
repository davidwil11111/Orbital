# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 136 — the detection sweep behind both Settings screens.

Measured 2026-10-26 on this base: a cold ``check_all()`` took 9.03 s, the sum
of every agent's ``--version`` and sign-in probe run one after another. These
tests pin the fix:

  - the sweep runs the agents in parallel (slowest agent, not the sum), keeps
    registry order, and stays single-flight;
  - one dependency check per sweep, version output cached by binary identity,
    "not found" remembered for the sweep TTL;
  - ``refresh_agent`` re-probes one agent into the cached list;
  - a listener hears about a sweep only when its result changed;
  - a sign-in probe that times out keeps the last verdict (Cursor's
    ``agent status`` hangs several times a day and flipped the card to Login).
"""

import os
import subprocess
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from agent_os.agents.manifest import (
    AgentManifest,
    ManifestCredential,
    ManifestDependency,
    ManifestPermissions,
    ManifestRuntime,
    ManifestSetup,
)
from agent_os.agents.registry import AgentRegistry
from agent_os.agents.setup_engine import SetupEngine


def _manifest(slug, *, command=None, dependencies=None, credentials=None,
              check_command=None, auto_detect=None) -> AgentManifest:
    command = command or slug
    return AgentManifest(
        manifest_version="1", name=slug.title(), slug=slug, description="",
        author="tester", version="1.0.0",
        runtime=ManifestRuntime(adapter="cli", command=command, args=[],
                                interactive=False, output_format="stream-json"),
        setup=ManifestSetup(
            dependencies=dependencies or [],
            install_command=None,
            check_command=check_command if check_command is not None else f"{command} --version",
            auto_detect=auto_detect or {},
            credentials=credentials or [],
        ),
        permissions=ManifestPermissions(network_domains=[]),
    )


def _registry(*manifests) -> AgentRegistry:
    registry = AgentRegistry()
    for m in manifests:
        registry.register(m)
    return registry


def _stub_engine(tmp_path, slugs, delay=0.0):
    """A real engine whose per-agent probe is a timed stub."""
    registry = MagicMock()
    registry.list_all.return_value = [SimpleNamespace(slug=s) for s in slugs]
    engine = SetupEngine(registry, data_dir=str(tmp_path))
    seen: list[tuple[str, float | None, str]] = []
    lock = threading.Lock()
    state = {"version": "1"}

    def check_agent(slug):
        with lock:
            seen.append((slug, getattr(engine._sweep_local, "handshake_wait", None),
                         threading.current_thread().name))
        version = state["version"]  # what the CLI said when it was asked
        time.sleep(delay(slug) if callable(delay) else delay)
        return SimpleNamespace(slug=slug, installed=True, name=slug,
                               version=version)

    engine.check_agent = check_agent
    return engine, seen, state


def _expire(engine):
    results, _ = engine._check_all_cache
    engine._check_all_cache = (results, time.monotonic() - 1)


# ---------------------------------------------------------------------------
# B.1 parallel sweep
# ---------------------------------------------------------------------------

def test_sweep_costs_the_slowest_agent_not_the_sum(tmp_path):
    slugs = [f"a{i}" for i in range(6)]
    engine, seen, _ = _stub_engine(tmp_path, slugs, delay=0.3)
    started = time.monotonic()
    results = engine.check_all()
    elapsed = time.monotonic() - started
    assert elapsed < 0.9, f"serial sweep: {elapsed:.2f}s for 6 x 0.3s"
    assert [r.slug for r in results] == slugs  # registry order, not finish order
    assert len(seen) == 6


def test_sweep_keeps_registry_order_when_agents_finish_out_of_order(tmp_path):
    slugs = ["slow", "medium", "fast"]
    delays = {"slow": 0.3, "medium": 0.15, "fast": 0.0}
    engine, _, _ = _stub_engine(tmp_path, slugs, delay=lambda s: delays[s])
    assert [r.slug for r in engine.check_all()] == slugs


def test_sweep_runs_on_its_own_pool_not_the_default_executor(tmp_path):
    engine, seen, _ = _stub_engine(tmp_path, ["a", "b", "c"], delay=0.05)
    engine.check_all()
    names = {name for _slug, _wait, name in seen}
    assert all(name.startswith("check-all") for name in names), names


def test_parallel_sweep_is_still_single_flight(tmp_path):
    engine, seen, _ = _stub_engine(tmp_path, ["a", "b"], delay=0.2)
    threads = [threading.Thread(target=engine.check_all) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(seen) == 2  # one sweep for five cold callers


def test_handshake_budget_reaches_the_parallel_workers(tmp_path):
    engine, seen, _ = _stub_engine(tmp_path, ["a", "b"])
    engine.check_all(handshake_wait=7.0)
    assert {wait for _slug, wait, _name in seen} == {7.0}
    engine.invalidate_cache()
    seen.clear()
    engine.check_all()
    assert {wait for _slug, wait, _name in seen} == {0.0}


def test_sweep_logs_one_timing_line(tmp_path, caplog):
    import logging
    engine, _, _ = _stub_engine(tmp_path, ["a", "b"], delay=lambda s: 0.1 if s == "b" else 0)
    with caplog.at_level(logging.INFO, logger="agent_os.agents.setup_engine"):
        engine.check_all()
    lines = [r.getMessage() for r in caplog.records if "check_all sweep" in r.getMessage()]
    assert len(lines) == 1
    assert "2 agents" in lines[0] and "slowest: b" in lines[0]


# ---------------------------------------------------------------------------
# B.2 one dependency check per sweep
# ---------------------------------------------------------------------------

def _node_dep():
    return ManifestDependency(name="Node.js", check_command="node --version",
                              min_version="18.0.0", install={})


def test_shared_dependency_is_checked_once_per_sweep(tmp_path):
    registry = _registry(*[_manifest(s, dependencies=[_node_dep()]) for s in ("a", "b", "c")])
    engine = SetupEngine(registry, data_dir=str(tmp_path))
    engine.resolve_binary = lambda m: None  # not installed: only the deps run
    calls = []
    lock = threading.Lock()

    def dep(cmd, minimum):
        with lock:
            calls.append(cmd)
        time.sleep(0.05)
        return True

    engine._check_single_dependency = dep
    statuses = engine.check_all()
    assert calls == ["node --version"]
    assert all(s.dependencies_met for s in statuses)
    engine.invalidate_cache()
    engine.check_all()
    assert len(calls) == 2  # per sweep, not for the daemon's lifetime


def test_single_agent_check_outside_a_sweep_runs_its_dependency(tmp_path):
    registry = _registry(_manifest("a", dependencies=[_node_dep()]))
    engine = SetupEngine(registry, data_dir=str(tmp_path))
    engine.resolve_binary = lambda m: None
    calls = []
    engine._check_single_dependency = lambda cmd, minimum: calls.append(cmd) or True
    engine.check_agent("a")
    engine.check_agent("a")
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# B.3 version output cached by binary identity
# ---------------------------------------------------------------------------

def _installed_engine(tmp_path, slug="a"):
    binary = tmp_path / "bin" / slug
    binary.parent.mkdir(exist_ok=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    registry = _registry(_manifest(slug))
    engine = SetupEngine(registry, data_dir=str(tmp_path / "data"))
    engine._pin(slug, str(binary), {"kind": "path"})
    versions = []
    engine._run_check_command = lambda cmd: versions.append(cmd) or "a 1.2.3"
    return engine, binary, versions


def test_version_check_runs_once_per_binary_identity(tmp_path):
    engine, _binary, versions = _installed_engine(tmp_path)
    assert engine.check_agent("a").version == "a 1.2.3"
    assert engine.check_agent("a").version == "a 1.2.3"
    assert len(versions) == 1


def test_a_changed_binary_is_asked_again(tmp_path):
    engine, binary, versions = _installed_engine(tmp_path)
    engine.check_agent("a")
    st = os.stat(binary)
    os.utime(binary, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    engine.check_agent("a")
    assert len(versions) == 2


def test_forget_versions_asks_again(tmp_path):
    engine, _binary, versions = _installed_engine(tmp_path)
    engine.check_agent("a")
    engine.forget_versions()
    engine.check_agent("a")
    assert len(versions) == 2


def test_a_failed_version_check_is_not_cached(tmp_path):
    engine, _binary, _ = _installed_engine(tmp_path)
    calls = []
    engine._run_check_command = lambda cmd: calls.append(cmd) and None
    engine.check_agent("a")
    engine.check_agent("a")
    assert len(calls) == 2


def test_version_cache_follows_a_repointed_symlink(tmp_path):
    """Homebrew and the claude installer repoint a symlink on update."""
    old = tmp_path / "v1"
    new = tmp_path / "v2"
    for target in (old, new):
        target.write_text("#!/bin/sh\n")
        target.chmod(0o755)
    link = tmp_path / "a"
    link.symlink_to(old)
    st = os.stat(old)
    os.utime(new, ns=(st.st_atime_ns, st.st_mtime_ns))  # same mtime and size
    registry = _registry(_manifest("a"))
    engine = SetupEngine(registry, data_dir=str(tmp_path / "data"))
    engine._pin("a", str(link), {"kind": "path"})
    calls = []
    engine._run_check_command = lambda cmd: calls.append(cmd) or "1.0.0"
    engine.check_agent("a")
    link.unlink()
    link.symlink_to(new)
    engine.check_agent("a")
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# B.4 "not found" remembered for the sweep TTL
# ---------------------------------------------------------------------------

def test_a_missing_agent_is_not_resolved_again_every_sweep(tmp_path, monkeypatch):
    registry = _registry(_manifest("ghost", command="orbital-test-no-such-cli"))
    engine = SetupEngine(registry, data_dir=str(tmp_path))
    calls = []
    engine._run_check_command = lambda cmd: calls.append(cmd) and None
    engine.check_all()
    engine.invalidate_cache()
    engine.check_all()
    assert len(calls) == 1  # strategy 5's shell spawn ran once, not per sweep


def test_invalidate_resolved_path_forgets_the_miss(tmp_path):
    registry = _registry(_manifest("ghost", command="orbital-test-no-such-cli"))
    engine = SetupEngine(registry, data_dir=str(tmp_path))
    calls = []
    engine._run_check_command = lambda cmd: calls.append(cmd) and None
    engine.check_all()
    engine.invalidate_resolved_path("ghost")
    engine.invalidate_cache()
    engine.check_all()
    assert len(calls) == 2


def test_a_single_agent_check_always_resolves_afresh(tmp_path):
    registry = _registry(_manifest("ghost", command="orbital-test-no-such-cli"))
    engine = SetupEngine(registry, data_dir=str(tmp_path))
    calls = []
    engine._run_check_command = lambda cmd: calls.append(cmd) and None
    engine.check_all()
    engine.check_agent("ghost")
    assert len(calls) == 2


def test_the_miss_expires_with_the_sweep_ttl(tmp_path, monkeypatch):
    from agent_os.agents import setup_engine as mod
    registry = _registry(_manifest("ghost", command="orbital-test-no-such-cli"))
    engine = SetupEngine(registry, data_dir=str(tmp_path))
    calls = []
    engine._run_check_command = lambda cmd: calls.append(cmd) and None
    monkeypatch.setattr(mod, "CHECK_ALL_CACHE_TTL_SECONDS", 0)
    engine.check_all()
    engine.check_all()
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# A.5 refresh_agent
# ---------------------------------------------------------------------------

def test_refresh_agent_patches_one_entry_and_keeps_the_expiry(tmp_path):
    engine, seen, state = _stub_engine(tmp_path, ["a", "b"])
    engine.check_all()
    _results, expires_at = engine._check_all_cache
    seen.clear()
    state["version"] = "2"
    status = engine.refresh_agent("b")
    assert status.version == "2"
    assert [s for s, _w, _n in seen] == ["b"]  # one agent, not the sweep
    results, expires_after = engine._check_all_cache
    assert expires_after == expires_at
    assert [(r.slug, r.version) for r in results] == [("a", "1"), ("b", "2")]


def test_refresh_agent_does_not_write_over_an_invalidation(tmp_path):
    engine, _, state = _stub_engine(tmp_path, ["a", "b"])
    engine.check_all()
    original = engine.check_agent

    def check_and_invalidate(slug):
        status = original(slug)
        engine.invalidate_cache()  # a Refresh lands while this agent is probed
        return status

    engine.check_agent = check_and_invalidate
    engine.refresh_agent("a")
    assert engine._check_all_cache is None


def test_refresh_agent_without_a_cached_sweep_writes_nothing(tmp_path):
    engine, _, _ = _stub_engine(tmp_path, ["a", "b"])
    assert engine.refresh_agent("a").slug == "a"
    assert engine._check_all_cache is None


def test_refresh_agent_waits_for_a_sweep_in_flight(tmp_path):
    """A sweep that probed the agent before the login finished must not
    overwrite the refreshed entry afterwards."""
    engine, _, state = _stub_engine(tmp_path, ["a"], delay=0.3)
    engine.check_all()
    _expire(engine)
    sweep = threading.Thread(target=lambda: engine._probe_all())
    sweep.start()
    time.sleep(0.05)
    state["version"] = "2"
    engine.refresh_agent("a")
    sweep.join()
    assert engine._check_all_cache[0][0].version == "2"


# ---------------------------------------------------------------------------
# A.2 the listener hears changes only
# ---------------------------------------------------------------------------

def test_listener_is_told_when_a_sweep_changes_the_result(tmp_path):
    engine, _, state = _stub_engine(tmp_path, ["a"])
    heard = []
    engine.set_status_listener(lambda results: heard.append([r.version for r in results]))
    engine.check_all()            # the first sweep is the baseline
    assert heard == []
    _expire(engine)
    engine._probe_all()           # same result: nothing to say
    assert heard == []
    state["version"] = "2"
    _expire(engine)
    engine._probe_all()
    assert heard == [["2"]]


def test_listener_hears_a_background_refresh(tmp_path):
    engine, _, state = _stub_engine(tmp_path, ["a"], delay=0.05)
    heard = threading.Event()
    engine.set_status_listener(lambda results: heard.set())
    engine.check_all()
    state["version"] = "2"
    _expire(engine)
    stale = engine.check_all(allow_stale=True)
    assert stale[0].version == "1"  # served at once
    assert heard.wait(2.0)
    assert engine.check_all()[0].version == "2"


def test_listener_hears_refresh_agent_changes(tmp_path):
    engine, _, state = _stub_engine(tmp_path, ["a", "b"])
    heard = []
    engine.set_status_listener(lambda results: heard.append(1))
    engine.check_all()
    engine.refresh_agent("a")
    assert heard == []
    state["version"] = "3"
    engine.refresh_agent("a")
    assert heard == [1]


def test_a_failing_listener_does_not_fail_the_sweep(tmp_path):
    engine, _, state = _stub_engine(tmp_path, ["a"])

    def boom(results):
        raise RuntimeError("ws gone")

    engine.set_status_listener(boom)
    engine.check_all()
    state["version"] = "2"
    _expire(engine)
    assert engine._probe_all()[0].version == "2"


def test_warm_starts_one_background_sweep(tmp_path):
    engine, seen, _ = _stub_engine(tmp_path, ["a", "b"], delay=0.05)
    engine.warm()
    engine.warm()
    deadline = time.monotonic() + 2
    while engine._check_all_cache is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert engine._check_all_cache is not None
    assert len(seen) == 2


# ---------------------------------------------------------------------------
# Decision 2: a timed-out sign-in probe never flips the card to Login
# ---------------------------------------------------------------------------

def _oauth_engine(tmp_path):
    cred = ManifestCredential(key="cursor_auth", label="Cursor", type="oauth_cli",
                              required=True, check_command="agent status --format json",
                              check_field="isAuthenticated", check_value="true",
                              setup_command="agent login")
    registry = _registry(_manifest("cursor", command="agent", credentials=[cred]))
    engine = SetupEngine(registry, data_dir=str(tmp_path))
    # Seeded directly: the caller's assertion, never re-checked on disk.
    engine._resolved_paths["cursor"] = "/usr/local/bin/agent"
    engine._run_check_command = lambda cmd: "2026.10.1"
    return engine


def _auth_run(outcome):
    def run(cmd, *args, **kwargs):
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
        return subprocess.CompletedProcess(
            cmd, 0, stdout='{"isAuthenticated": %s}' % ("true" if outcome == "in" else "false"),
            stderr="")
    return run


def test_timeout_after_signed_in_stays_signed_in(tmp_path, monkeypatch):
    from agent_os.agents import setup_engine as mod
    engine = _oauth_engine(tmp_path)
    monkeypatch.setattr(mod.subprocess, "run", _auth_run("in"))
    assert engine.check_agent("cursor").credentials_configured is True
    monkeypatch.setattr(mod.subprocess, "run", _auth_run("timeout"))
    status = engine.check_agent("cursor")
    assert status.credentials_configured is True
    assert status.missing_credentials == []


def test_timeout_after_signed_out_stays_signed_out(tmp_path, monkeypatch):
    from agent_os.agents import setup_engine as mod
    engine = _oauth_engine(tmp_path)
    monkeypatch.setattr(mod.subprocess, "run", _auth_run("out"))
    assert engine.check_agent("cursor").credentials_configured is False
    monkeypatch.setattr(mod.subprocess, "run", _auth_run("timeout"))
    assert engine.check_agent("cursor").credentials_configured is False


def test_timeout_with_no_earlier_verdict_reads_unknown_not_login(tmp_path, monkeypatch):
    from agent_os.agents import setup_engine as mod
    engine = _oauth_engine(tmp_path)
    monkeypatch.setattr(mod.subprocess, "run", _auth_run("timeout"))
    status = engine.check_agent("cursor")
    assert status.credentials_configured is True
    assert status.credential_state == "unknown"
    assert not any(a.action == "run_cli_auth" for a in status.setup_actions)


def test_a_real_answer_after_a_timeout_wins(tmp_path, monkeypatch):
    from agent_os.agents import setup_engine as mod
    engine = _oauth_engine(tmp_path)
    monkeypatch.setattr(mod.subprocess, "run", _auth_run("timeout"))
    engine.check_agent("cursor")
    monkeypatch.setattr(mod.subprocess, "run", _auth_run("out"))
    status = engine.check_agent("cursor")
    assert status.credentials_configured is False
    assert status.credential_state is None


# ---------------------------------------------------------------------------
# Expired sign-in record: patched into the sweep, not a full re-probe
# ---------------------------------------------------------------------------

def test_auth_failure_record_patches_the_cached_entry(tmp_path):
    engine, seen, _ = _stub_engine(tmp_path, ["a", "b"])
    engine.check_agent = (lambda orig: (lambda slug: SimpleNamespace(
        **{**vars(orig(slug)), "credentials_configured": True, "auth_expired": False})))(
            engine.check_agent)
    engine.check_all()
    seen.clear()
    assert engine.note_auth_failure("a", "Failed to authenticate. API Error: 401")
    entry = engine.cached_status("a")
    assert entry is not None and entry.auth_expired is True
    engine.clear_auth_failure("a")
    assert engine.cached_status("a").auth_expired is False
    assert seen == []  # no probe ran
