"""Daily rollup + sender (spec 046 §5, §11)."""

import json
from pathlib import Path

from agent_os.telemetry.identity import InstallIdentity
from agent_os.telemetry.rollup import build_ping
from agent_os.telemetry.sender import TelemetrySender
from agent_os.telemetry.spool import Spool

DAY = "2026-08-08"


def seed_spool(tmp_path: Path, rows: list[dict]) -> Spool:
    path = tmp_path / "telemetry" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return Spool(tmp_path)


def ev(event: str, **fields) -> dict:
    return {"event": event, "ts": f"{DAY}T10:00:00+00:00", **fields}


class TestRollup:
    def test_aggregates_counters_and_disjoint_tokens(self, tmp_path):
        spool = seed_spool(tmp_path, [
            ev("app_start"),
            ev("app_start"),
            ev("project_created"),
            ev("session_created"),
            ev("turn_completed", provider="deepseek",
               uncached_input=100, cache_read=900, cache_write=50, output=80),
            ev("turn_completed", provider="deepseek",
               uncached_input=10, cache_read=0, cache_write=0, output=20),
            ev("turn_completed", provider="minimax",
               uncached_input=5, cache_read=5, cache_write=0, output=5),
            ev("llm_error", error_code="provider_unreachable"),
            ev("llm_error", error_code="provider_unreachable"),
            ev("llm_error", error_code="invalid_api_key"),
            # Different day must be excluded:
            {"event": "app_start", "ts": "2026-08-07T10:00:00+00:00"},
        ])
        identity = InstallIdentity(tmp_path)
        identity.latch_milestone("key_set")
        identity.latch_milestone("first_turn")

        ping = build_ping(identity, spool, DAY, version="0.8.4", os_name="darwin")

        assert ping["schema"] == 1
        assert ping["install_id"] == identity.install_id
        assert ping["account_id"] is None
        assert ping["version"] == "0.8.4"
        assert ping["os"] == "darwin"
        assert ping["date"] == DAY
        assert ping["milestones"] == {
            "key_set": True, "first_project": False,
            "first_session": False, "first_turn": True,
            # Spec 138 onboarding milestones.
            "key_skipped": False, "first_import": False,
            "first_leftoff_open": False, "first_imported_send": False,
        }
        c = ping["counters"]
        assert c["app_starts"] == 2
        assert c["projects_created"] == 1
        assert c["sessions"] == 1
        assert c["turns"] == 3
        # in = uncached_input + cache_read + cache_write (disjoint ledger fields)
        assert c["tokens_by_provider"]["deepseek"] == {"in": 1060, "out": 100}
        assert c["tokens_by_provider"]["minimax"] == {"in": 10, "out": 5}
        assert c["errors"] == {"provider_unreachable": 2, "invalid_api_key": 1}

    def test_empty_day_yields_zero_ping(self, tmp_path):
        ping = build_ping(
            InstallIdentity(tmp_path), Spool(tmp_path), DAY,
            version="0.8.4", os_name="darwin",
        )
        assert ping["counters"]["turns"] == 0
        assert ping["counters"]["tokens_by_provider"] == {}
        assert ping["counters"]["errors_by_provider"] == {}
        assert ping["counters"]["login_attempted"] == 0
        assert ping["counters"]["login_failed"] == 0

    def test_errors_split_by_provider_alongside_by_code(self, tmp_path):
        """Spec 063 §4 + decision 2: the new map is ADDITIVE — `errors` is the
        only error series with history and keeps its exact shape."""
        spool = seed_spool(tmp_path, [
            ev("llm_error", error_code="invalid_api_key", provider="deepseek"),
            ev("llm_error", error_code="provider_unreachable", provider="deepseek"),
            ev("llm_error", error_code="invalid_api_key", provider="minimax"),
        ])
        c = build_ping(
            InstallIdentity(tmp_path), spool, DAY, version="0.9.1", os_name="windows",
        )["counters"]
        assert c["errors"] == {"invalid_api_key": 2, "provider_unreachable": 1}
        assert c["errors_by_provider"] == {"deepseek": 2, "minimax": 1}

    def test_unresolved_provider_falls_back_to_unknown(self, tmp_path):
        """Construction failures fail before the provider is resolved; the
        same `unknown` bucket tokens_by_provider already uses (§8)."""
        spool = seed_spool(tmp_path, [
            ev("llm_error", error_code="missing_api_key"),          # field absent
            ev("llm_error", error_code="missing_api_key", provider=None),
            ev("llm_error", error_code="invalid_api_key", provider="deepseek"),
        ])
        c = build_ping(
            InstallIdentity(tmp_path), spool, DAY, version="0.9.1", os_name="darwin",
        )["counters"]
        assert c["errors_by_provider"] == {"unknown": 2, "deepseek": 1}
        assert sum(c["errors_by_provider"].values()) == sum(c["errors"].values())

    def test_login_funnel_counters_aggregate(self, tmp_path):
        """Spec 063 §12 decision 5: sub-agent setup — the product's
        differentiator — was invisible between key_set and first_turn. The
        agent slug stays in the local spool; only totals are transmitted."""
        spool = seed_spool(tmp_path, [
            ev("login_attempted", agent="claude-code"),
            ev("login_attempted", agent="codex"),
            ev("login_attempted", agent="codex"),
            ev("login_failed", agent="codex"),
            # Different day must be excluded:
            {"event": "login_attempted", "ts": "2026-08-07T10:00:00+00:00"},
        ])
        c = build_ping(
            InstallIdentity(tmp_path), spool, DAY, version="0.9.1", os_name="windows",
        )["counters"]
        assert c["login_attempted"] == 3
        assert c["login_failed"] == 1
        assert isinstance(c["login_attempted"], int)

    def test_subagent_dispatch_counters_aggregate_per_agent(self, tmp_path):
        """Whether anyone delegates at all was invisible: nothing counted a
        dispatch. Totals plus a per-agent split; a failure need not follow a
        dispatch (a spawn that never started still counts as failed)."""
        spool = seed_spool(tmp_path, [
            ev("subagent_dispatched", agent="claude-code"),
            ev("subagent_dispatched", agent="claude-code"),
            ev("subagent_dispatched", agent="worker:fan1-0"),
            ev("subagent_failed", agent="claude-code"),
            ev("subagent_failed", agent="codex"),
            {"event": "subagent_dispatched", "ts": "2026-08-07T10:00:00+00:00",
             "agent": "claude-code"},
        ])
        c = build_ping(
            InstallIdentity(tmp_path), spool, DAY, version="0.13.1", os_name="darwin",
        )["counters"]
        assert c["subagent_dispatches"] == 3
        assert c["subagent_failed"] == 2
        # Spec 113 A2 widened the per-agent entry with ``failed_by_reason``;
        # rows without a reason (this fixture, and every pre-113 build) land
        # in ``unknown``.
        assert c["subagent_by_agent"] == {
            "claude-code": {"dispatched": 2, "failed": 1,
                            "failed_by_reason": {"unknown": 1}},
            "native": {"dispatched": 1, "failed": 0, "failed_by_reason": {}},
            "codex": {"dispatched": 0, "failed": 1,
                      "failed_by_reason": {"unknown": 1}},
        }

    def test_subagent_agent_outside_builtin_set_is_bucketed(self, tmp_path):
        """A handle is model-chosen text (a hallucinated agent name reaches the
        spawn and fails). Only built-in slugs may leave the machine."""
        spool = seed_spool(tmp_path, [
            ev("subagent_failed", agent="my-secret-project-helper"),
            ev("subagent_dispatched"),
        ])
        c = build_ping(
            InstallIdentity(tmp_path), spool, DAY, version="0.13.1", os_name="darwin",
        )["counters"]
        assert c["subagent_by_agent"] == {
            "other": {"dispatched": 1, "failed": 1, "failed_by_reason": {"unknown": 1}},
        }

    def test_empty_day_has_zero_subagent_counters(self, tmp_path):
        c = build_ping(
            InstallIdentity(tmp_path), seed_spool(tmp_path, []), DAY,
            version="0.13.1", os_name="darwin",
        )["counters"]
        assert c["subagent_dispatches"] == 0
        assert c["subagent_failed"] == 0
        assert c["subagent_by_agent"] == {}

    def test_every_subagent_manifest_slug_is_transmittable(self):
        """Parity guard: a new sub-agent manifest must be added to the
        allowlist, or its usage silently collapses into ``other``."""
        from pathlib import Path

        import yaml

        from agent_os.telemetry.rollup import SUBAGENT_SLUGS

        manifests = Path(__file__).resolve().parents[2] / "agent_os" / "agents" / "manifests"
        slugs = set()
        for path in manifests.glob("*.yaml"):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if data["runtime"]["adapter"] != "built_in":
                slugs.add(data["slug"])
        assert slugs, "no manifests found"
        assert slugs <= set(SUBAGENT_SLUGS), sorted(slugs - set(SUBAGENT_SLUGS))


class FakePost:
    def __init__(self, status=200, exc: Exception | None = None):
        self.status = status
        self.exc = exc
        self.calls: list[tuple[str, dict, dict]] = []

    async def __call__(self, url, payload, headers):
        self.calls.append((url, payload, headers))
        if self.exc:
            raise self.exc
        return self.status


def make_sender(tmp_path, post, enabled=True) -> TelemetrySender:
    return TelemetrySender(
        tmp_path,
        InstallIdentity(tmp_path),
        Spool(tmp_path),
        is_enabled=lambda: enabled,
        endpoint="https://example.invalid/ingest",
        post=post,
    )


class TestSender:
    async def test_cycle_snapshots_today_and_sends(self, tmp_path):
        post = FakePost()
        sender = make_sender(tmp_path, post)
        await sender.run_cycle()

        assert len(post.calls) == 1
        url, payload, headers = post.calls[0]
        assert payload["schema"] == 1
        assert headers["X-Orbital-Telemetry-Token"]
        # Today's snapshot survives for the next cycle (upsert semantics).
        pending = list((tmp_path / "telemetry" / "pending").glob("*.json"))
        assert len(pending) == 1
        # Viewer surfaces: last-sent recorded verbatim.
        assert sender.last_sent_payload() == payload

    async def test_past_day_pending_deleted_on_success_today_kept(self, tmp_path):
        post = FakePost()
        sender = make_sender(tmp_path, post)
        pending = tmp_path / "telemetry" / "pending"
        pending.mkdir(parents=True)
        stale = {"schema": 1, "date": "2020-01-01", "install_id": "inst_x"}
        (pending / "2020-01-01.json").write_text(json.dumps(stale))

        await sender.run_cycle()

        names = sorted(p.name for p in pending.glob("*.json"))
        assert "2020-01-01.json" not in names  # flushed + deleted
        assert len(names) == 1  # today's snapshot remains
        assert len(post.calls) == 2

    async def test_offline_keeps_all_pending(self, tmp_path):
        post = FakePost(exc=OSError("no network"))
        sender = make_sender(tmp_path, post)
        await sender.run_cycle()
        pending = list((tmp_path / "telemetry" / "pending").glob("*.json"))
        assert len(pending) == 1  # today's snapshot queued for next cycle
        assert sender.last_sent_payload() is None

    async def test_server_error_keeps_pending(self, tmp_path):
        post = FakePost(status=500)
        sender = make_sender(tmp_path, post)
        await sender.run_cycle()
        assert len(list((tmp_path / "telemetry" / "pending").glob("*.json"))) == 1
        assert sender.last_sent_payload() is None

    async def test_toggle_off_no_send_no_snapshot(self, tmp_path):
        post = FakePost()
        sender = make_sender(tmp_path, post, enabled=False)
        await sender.run_cycle()
        assert post.calls == []
        assert not (tmp_path / "telemetry" / "pending").exists()

    async def test_next_pending_payload_matches_schema(self, tmp_path):
        sender = make_sender(tmp_path, FakePost())
        payload = sender.next_pending_payload()
        assert set(payload) == {
            "schema", "install_id", "account_id", "version", "os",
            "date", "first_seen", "milestones", "counters",
        }


# ---------------------------------------------------------------------------
# Spec 111 — counters were a stale snapshot for every past day.
#
# The cycle only ever built TODAY's ping. A past day's pending file froze at
# the last cycle that ran on that day, and events spooled after it (a 20-minute
# session closed before the next 6h tick; everything after 23:00 UTC) were
# never aggregated again. The fix rebuilds every unacknowledged day the spool
# still covers before flushing; acknowledgements persist in ``acks.json``.
# ---------------------------------------------------------------------------

DAY1 = "2026-10-01"
DAY2 = "2026-10-02"


def _rows(day: str, event: str, n: int, **fields) -> list[dict]:
    return [{"event": event, "ts": f"{day}T12:00:00+00:00", **fields} for _ in range(n)]


def _pending_dir(tmp_path: Path) -> Path:
    p = tmp_path / "telemetry" / "pending"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _sent_for(post: FakePost, day: str) -> list[dict]:
    return [payload for _, payload, _ in post.calls if payload.get("date") == day]


class TestPastDayRebuild:
    async def test_stale_past_snapshot_is_rebuilt_from_spool(self, tmp_path):
        """day1 has 10 turns in the spool but its pending file says turns=0
        (the snapshot taken at 09:00 before any work happened). The next
        cycle must send turns=10, not the frozen file."""
        post = FakePost()
        spool = seed_spool(tmp_path, _rows(DAY1, "turn_completed", 10, provider="deepseek"))
        sender = TelemetrySender(
            tmp_path, InstallIdentity(tmp_path), spool,
            is_enabled=lambda: True, endpoint="https://example.invalid/ingest", post=post,
        )
        stale = build_ping(InstallIdentity(tmp_path), Spool(tmp_path / "empty"), DAY1,
                           version="0.16.0", os_name="darwin")
        assert stale["counters"]["turns"] == 0
        (_pending_dir(tmp_path) / f"{DAY1}.json").write_text(json.dumps(stale))

        await sender.run_cycle()

        sent = _sent_for(post, DAY1)
        assert len(sent) == 1
        assert sent[0]["counters"]["turns"] == 10

    async def test_day_with_events_but_no_pending_file_is_generated(self, tmp_path):
        """Start at 23:00 UTC (cycle writes day1), work until 01:00, quit:
        day2 never got a file. The next start must produce and send it."""
        post = FakePost()
        spool = seed_spool(tmp_path, _rows(DAY2, "session_created", 3))
        sender = make_sender(tmp_path, post)
        sender._spool = spool

        await sender.run_cycle()

        sent = _sent_for(post, DAY2)
        assert len(sent) == 1
        assert sent[0]["counters"]["sessions"] == 3

    async def test_acked_day_is_not_resent(self, tmp_path):
        post = FakePost()
        spool = seed_spool(tmp_path, _rows(DAY1, "turn_completed", 2))
        sender = make_sender(tmp_path, post)
        sender._spool = spool

        await sender.run_cycle()
        assert len(_sent_for(post, DAY1)) == 1
        acks = json.loads((tmp_path / "telemetry" / "acks.json").read_text())
        assert DAY1 in acks

        await sender.run_cycle()
        assert len(_sent_for(post, DAY1)) == 1  # still one — not resent
        # And the past day's file is gone; only today's snapshot remains.
        names = sorted(p.name for p in (tmp_path / "telemetry" / "pending").glob("*.json"))
        assert f"{DAY1}.json" not in names and len(names) == 1

    async def test_corrupt_ack_file_degrades_to_resend_all(self, tmp_path):
        """Resends are idempotent (server upserts latest-wins), so a broken
        ack record must mean "send everything again", never "send nothing"."""
        post = FakePost()
        spool = seed_spool(tmp_path, _rows(DAY1, "turn_completed", 2))
        sender = make_sender(tmp_path, post)
        sender._spool = spool
        (tmp_path / "telemetry" / "acks.json").write_text("{not json")

        await sender.run_cycle()

        assert len(_sent_for(post, DAY1)) == 1
        acks = json.loads((tmp_path / "telemetry" / "acks.json").read_text())
        assert DAY1 in acks

    async def test_failed_send_does_not_ack(self, tmp_path):
        post = FakePost(status=500)
        spool = seed_spool(tmp_path, _rows(DAY1, "turn_completed", 2))
        sender = make_sender(tmp_path, post)
        sender._spool = spool

        await sender.run_cycle()

        assert not (tmp_path / "telemetry" / "acks.json").exists()
        assert (tmp_path / "telemetry" / "pending" / f"{DAY1}.json").exists()

    async def test_day_outside_spool_window_is_sent_as_is(self, tmp_path):
        """Spool rotation (5 MB + one predecessor) can drop a day's events
        while its pending file survives. That file is the best record left:
        send it verbatim, then ack it. No error, no rebuild to zeros."""
        post = FakePost()
        sender = make_sender(tmp_path, post)  # empty spool
        old = {"schema": 1, "date": "2026-09-01", "install_id": "inst_x",
               "counters": {"turns": 7}}
        (_pending_dir(tmp_path) / "2026-09-01.json").write_text(json.dumps(old))

        await sender.run_cycle()

        sent = _sent_for(post, "2026-09-01")
        assert sent == [old]
        acks = json.loads((tmp_path / "telemetry" / "acks.json").read_text())
        assert "2026-09-01" in acks
        assert not (tmp_path / "telemetry" / "pending" / "2026-09-01.json").exists()

    async def test_today_is_never_acked(self, tmp_path):
        """Today keeps being rewritten and resent until the day is over."""
        post = FakePost()
        sender = make_sender(tmp_path, post)
        await sender.run_cycle()
        today = post.calls[0][1]["date"]
        acks_path = tmp_path / "telemetry" / "acks.json"
        acks = json.loads(acks_path.read_text()) if acks_path.exists() else {}
        assert today not in acks

    async def test_rebuild_covers_every_unacked_spool_day(self, tmp_path):
        """Three days of spool, nothing acked: all three go out in one cycle,
        each aggregated from its own day's rows."""
        post = FakePost()
        rows = (_rows(DAY1, "turn_completed", 1) + _rows(DAY2, "turn_completed", 2)
                + _rows("2026-10-03", "turn_completed", 3))
        spool = seed_spool(tmp_path, rows)
        sender = make_sender(tmp_path, post)
        sender._spool = spool

        await sender.run_cycle()

        got = {d: _sent_for(post, d)[0]["counters"]["turns"]
               for d in (DAY1, DAY2, "2026-10-03")}
        assert got == {DAY1: 1, DAY2: 2, "2026-10-03": 3}


class TestFlushNow:
    """A2: the exit path needs a bounded, never-raising "send what you have"."""

    async def test_flush_now_runs_a_cycle_when_allowed(self, tmp_path, monkeypatch):
        post = FakePost()
        sender = make_sender(tmp_path, post)
        monkeypatch.setattr(sender, "_suppression_reason", lambda: None)
        assert await sender.flush_now(timeout=3.0) == "sent"
        assert len(post.calls) == 1

    async def test_flush_now_is_suppressed_like_start(self, tmp_path, monkeypatch):
        """The dev daemon / CI / a temp data dir must not transmit on exit
        either — the same guard ``start()`` applies."""
        post = FakePost()
        sender = make_sender(tmp_path, post)  # tmp_path → structural guard
        assert await sender.flush_now(timeout=3.0) == "skipped"
        assert post.calls == []

    async def test_flush_now_times_out_quietly(self, tmp_path, monkeypatch):
        import asyncio

        async def hang(url, payload, headers):
            await asyncio.sleep(10)
            return 200

        sender = make_sender(tmp_path, hang)
        monkeypatch.setattr(sender, "_suppression_reason", lambda: None)
        assert await sender.flush_now(timeout=0.05) == "timeout"

    async def test_flush_now_toggle_off_is_skipped(self, tmp_path, monkeypatch):
        post = FakePost()
        sender = make_sender(tmp_path, post, enabled=False)
        monkeypatch.setattr(sender, "_suppression_reason", lambda: None)
        assert await sender.flush_now(timeout=3.0) == "skipped"
        assert post.calls == []


# ---------------------------------------------------------------------------
# Spec 113 — login / dispatch failures carry a reason and structured
# diagnostics; the ping lists the last 20 failure events.
# ---------------------------------------------------------------------------

class TestFailureReasons:

    def _counters(self, tmp_path, rows):
        return build_ping(
            InstallIdentity(tmp_path), seed_spool(tmp_path, rows), DAY,
            version="0.17.0", os_name="windows",
        )["counters"]

    def test_login_funnel_splits_by_agent_and_reason(self, tmp_path):
        """§3.1: unverified is its own counter, NOT a login failure."""
        c = self._counters(tmp_path, [
            ev("login_attempted", agent="codex"),
            ev("login_attempted", agent="codex"),
            ev("login_attempted", agent="codex"),
            ev("login_failed", agent="codex", reason="nonzero_exit"),
            ev("login_failed", agent="codex", reason="nonzero_exit"),
            ev("login_unverified", agent="codex"),
        ])
        assert c["login_attempted"] == 3
        assert c["login_failed"] == 2
        assert c["login_unverified"] == 1
        assert c["login_by_agent"] == {"codex": {"attempted": 3, "failed": 2, "unverified": 1}}
        assert c["login_failed_by_reason"] == {"nonzero_exit": 2}

    def test_non_builtin_slug_and_missing_reason_degrade_safely(self, tmp_path):
        c = self._counters(tmp_path, [
            ev("login_attempted", agent="my-private-cli"),
            ev("login_failed", agent="my-private-cli"),        # old format: no reason
            ev("login_failed", agent="codex", reason="stack trace text"),
        ])
        assert c["login_by_agent"] == {
            "other": {"attempted": 1, "failed": 1, "unverified": 0},
            "codex": {"attempted": 0, "failed": 1, "unverified": 0},
        }
        assert c["login_failed_by_reason"] == {"unknown": 2}

    def test_subagent_failure_reason_counts_top_level_and_per_agent(self, tmp_path):
        c = self._counters(tmp_path, [
            ev("subagent_failed", agent="claude-code", reason="spawn"),
            ev("subagent_failed", agent="claude-code", reason="update_needed"),
            ev("subagent_failed", agent="codex", reason="not_authenticated"),
            ev("subagent_failed", agent="codex"),                 # old format
            ev("subagent_dispatched", agent="codex"),
        ])
        assert c["subagent_failed"] == 4
        assert c["subagent_failed_by_reason"] == {
            "spawn": 1, "update_needed": 1, "not_authenticated": 1, "unknown": 1}
        assert c["subagent_by_agent"]["claude-code"] == {
            "dispatched": 0, "failed": 2,
            "failed_by_reason": {"spawn": 1, "update_needed": 1}}
        assert c["subagent_by_agent"]["codex"] == {
            "dispatched": 1, "failed": 2,
            "failed_by_reason": {"not_authenticated": 1, "unknown": 1}}

    def test_update_job_counters(self, tmp_path):
        c = self._counters(tmp_path, [
            ev("update_attempted", agent="claude-code"),
            ev("update_failed", agent="claude-code", reason="unverified",
               exit_code=0, cli_version="2.1.100", install_kind="npm"),
        ])
        assert c["update_attempted"] == 1
        assert c["update_failed"] == 1
        assert c["recent_failures"][0]["event"] == "update_failed"
        assert c["recent_failures"][0]["reason"] == "unverified"

    def test_recent_failures_carry_structured_diagnostics_only(self, tmp_path):
        """The 2026-10-18 Windows case (spec 116 §0) must be readable from
        the fields alone: CLI 2.1.100, npm layout, refused for the floor."""
        c = self._counters(tmp_path, [
            ev("subagent_failed", agent="claude-code", reason="update_needed",
               exit_code=None, cli_version="2.1.100 (Claude Code)",
               install_kind="npm", duration_ms=12, error_class=None,
               stderr_category="other",
               # Anything free-text a careless emitter adds must NOT leak:
               error="Refusing to execute batch script C:\\\\Users\\\\x\\\\claude.CMD",
               binary_path="C:\\\\Users\\\\x\\\\AppData\\\\Roaming\\\\npm\\\\claude.CMD"),
            ev("login_failed", agent="claude-code", reason="nonzero_exit",
               exit_code=1, cli_version="2.1.100", install_kind="npm",
               duration_ms=840, error_class=None, stderr_category="unknown_command"),
            ev("subagent_failed", agent="weird-agent", reason="made-up",
               exit_code="not-an-int", cli_version="latest", install_kind="brew",
               error_class="SomethingError", stderr_category="raw text"),
        ])
        recent = c["recent_failures"]
        assert len(recent) == 3
        windows = recent[0]
        assert windows == {
            "ts": f"{DAY}T10:00:00+00:00", "event": "subagent_failed",
            "agent": "claude-code", "reason": "update_needed", "exit_code": None,
            "cli_version": "2.1.100", "install_kind": "npm", "duration_ms": 12,
            "error_class": None, "stderr_category": "other",
        }
        assert recent[1]["exit_code"] == 1
        assert recent[1]["stderr_category"] == "unknown_command"
        degraded = recent[2]
        assert degraded["agent"] == "other"
        assert degraded["reason"] == "unknown"
        assert degraded["exit_code"] is None
        assert degraded["cli_version"] is None
        assert degraded["install_kind"] == "unknown"
        assert degraded["error_class"] == "other"
        assert degraded["stderr_category"] == "other"
        # No row carries anything but the fixed key set.
        for row in recent:
            assert set(row) == {"ts", "event", "agent", "reason", "exit_code",
                                "cli_version", "install_kind", "duration_ms",
                                "error_class", "stderr_category"}

    def test_recent_failures_is_bounded_to_the_last_twenty(self, tmp_path):
        rows = [ev("subagent_failed", agent="codex", reason="spawn", duration_ms=i)
                for i in range(25)]
        c = self._counters(tmp_path, rows)
        assert len(c["recent_failures"]) == 20
        assert [r["duration_ms"] for r in c["recent_failures"]] == list(range(5, 25))
        assert c["subagent_failed"] == 25

    def test_empty_day_has_empty_failure_shapes(self, tmp_path):
        c = self._counters(tmp_path, [])
        assert c["login_unverified"] == 0
        assert c["login_by_agent"] == {}
        assert c["login_failed_by_reason"] == {}
        assert c["update_attempted"] == 0
        assert c["update_failed"] == 0
        assert c["subagent_failed_by_reason"] == {}
        assert c["recent_failures"] == []

    def test_reason_keys_are_subsets_of_the_declared_enums(self, tmp_path):
        """Enum parity (§3.1): nothing outside the constant tuples can appear
        as a ``*_by_reason`` key, however the spool rows were written."""
        from agent_os.telemetry.diagnostics import (
            LOGIN_FAILURE_REASONS, SUBAGENT_FAILURE_REASONS)

        c = self._counters(tmp_path, [
            ev("login_failed", agent="codex", reason="Traceback (most recent call last)"),
            ev("login_failed", agent="codex", reason="timeout"),
            ev("subagent_failed", agent="codex", reason="https://evil.example/?code=1"),
            ev("subagent_failed", agent="codex", reason="runtime_error"),
        ])
        assert set(c["login_failed_by_reason"]) <= set(LOGIN_FAILURE_REASONS)
        assert set(c["subagent_failed_by_reason"]) <= set(SUBAGENT_FAILURE_REASONS)
        for entry in c["subagent_by_agent"].values():
            assert set(entry["failed_by_reason"]) <= set(SUBAGENT_FAILURE_REASONS)


class TestSchemaDocParity:
    """§3.4: the published example in docs/TELEMETRY.md and ``build_ping``
    agree on the ``counters`` key set, so the doc can never drift from the
    payload (it is what users are told leaves their machine)."""

    def test_doc_example_counters_match_build_ping(self, tmp_path):
        import re
        from pathlib import Path

        doc = (Path(__file__).resolve().parents[2] / "docs" / "TELEMETRY.md").read_text(
            encoding="utf-8")
        block = re.search(r"```json\n(\{.*?\n\})\n```", doc, re.S)
        assert block, "docs/TELEMETRY.md has no JSON example"
        example = json.loads(block.group(1))
        ping = build_ping(InstallIdentity(tmp_path), seed_spool(tmp_path, []), DAY,
                          version="0.17.0", os_name="darwin")
        assert set(example["counters"]) == set(ping["counters"])
        assert set(example) == set(ping)
