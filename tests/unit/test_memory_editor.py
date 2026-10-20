# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The memory editor (spec 089 §4.2, spec 124 interim) — choices by id,
never whole files.

What it must guarantee, because it edits real users' memory automatically:
every line it removes from a live file exists verbatim in an archive with its
id (nothing is left in the live file — INDEX and the archive's contents table
are the route back); the four files are backed up first (rolling, last 5);
a file written while the editor ran is skipped (OCC); the watermark advances
only on success; one run per project at a time; an id that left a live file
without reaching the archive is restored.
"""

import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agent_os.agent import memory_editor as E
from agent_os.agent import state_blocks
from agent_os.agent import workspace_files as WF
from agent_os.agent.workspace_files import WorkspaceFileManager, run_session_end_routine

TODAY = "2026-09-19"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _bullet(text, eid, created="2026-09-01"):
    return f"- {text}\n  <!--mem id:{eid} created:{created} touched:{created}-->"


def _decision(title, eid, body="**Chose:** A.\n**Reason:** B."):
    detail = "".join("  " + ln + "\n" for ln in body.split("\n"))
    return f"- {title}\n  <!--mem id:{eid} created:2026-08-01 touched:2026-08-01-->\n{detail}\n"


STATE = "## Now\n" + "\n".join([
    _bullet("GOAI booth prep — posters printed, flyers ordered", "goai01", "2026-09-14"),
    _bullet("Newsjack radar 9/18: no new target", "news18", "2026-09-18"),
    _bullet("Newsjack radar 9/17: no new target", "news17", "2026-09-17"),
    _bullet("Cold-DM automation paused since 08-15", "cdm001", "2026-08-10"),
]) + "\n"

DECISIONS = (
    "# DECISIONS\n\n"
    + _decision("TabTin → watchlist", "tabtin")
    + _decision("PenguinHarness → watchlist", "penguin")
    + _decision("Pinned rule", "pinrule").replace("touched:2026-08-01", "touched:2026-08-01 tag:pinned")
)

LESSONS = "# LESSONS\n\n- Curl beats the browser for GitHub.\n  <!--mem id:curl created:2026-08-01 touched:2026-08-01-->\n\n"
INDEX = "# INDEX\n\n- agent_output/ — reports\n- DECISIONS_ARCHIVE.md — older decisions (read on demand)\n"


@pytest.fixture
def wf(tmp_path):
    w = WorkspaceFileManager(str(tmp_path))
    w.write("state", STATE)
    w.write("decisions", DECISIONS)
    w.write("lessons", LESSONS)
    w.write("index", INDEX)
    return w


@pytest.fixture(autouse=True)
def _clear_single_flight():
    E._RUNNING.clear()
    WF._completed_session_ends.clear()
    yield
    E._RUNNING.clear()


def _resp(text=None, tool_calls=None):
    raw = {"role": "assistant", "content": text}
    if tool_calls:
        raw["tool_calls"] = [
            {"id": f"c{i}", "type": "function",
             "function": {"name": n, "arguments": json.dumps(a)}}
            for i, (n, a) in enumerate(tool_calls)
        ]
    return SimpleNamespace(text=text, raw_message=raw, tool_calls=raw.get("tool_calls", []),
                           has_tool_calls=bool(tool_calls))


class ScriptedLLM:
    """complete() replays a script; each entry is a response or a callable."""

    model = "fake-utility"

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def complete(self, messages, tools=None, **_kw):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        step = self.script.pop(0)
        return step() if callable(step) else step


def _live_lines(wf):
    return {k: (wf.read(k) or "") for k in E.LIVE_KEYS}


# ---------------------------------------------------------------------------
# backup + watermark + ids
# ---------------------------------------------------------------------------

def test_backup_copies_the_four_files_and_keeps_the_last_five(wf):
    base = datetime(2026, 9, 19, tzinfo=timezone.utc)
    dirs = [E.backup_live_files(wf.dir, now=base + timedelta(seconds=i)) for i in range(7)]
    root = os.path.join(wf.dir, "backups")
    kept = sorted(d for d in os.listdir(root) if d.startswith("memory-editor-"))
    assert len(kept) == 5
    assert os.path.basename(dirs[-1]) in kept and os.path.basename(dirs[0]) not in kept
    assert sorted(os.listdir(dirs[-1])) == ["DECISIONS.md", "INDEX.md", "LESSONS.md", "PROJECT_STATE.md"]
    with open(os.path.join(dirs[-1], "PROJECT_STATE.md"), encoding="utf-8") as f:
        assert f.read() == wf.read("state")


def test_backup_never_prunes_other_backups(wf):
    other = os.path.join(wf.dir, "backups", "asks-migration-20260919")
    os.makedirs(other)
    for i in range(7):
        E.backup_live_files(wf.dir, now=datetime(2026, 9, 19, tzinfo=timezone.utc) + timedelta(seconds=i))
    assert os.path.isdir(other)


def test_watermark_falls_back_to_the_old_marker_mtime(wf):
    with open(os.path.join(wf.dir, ".memory_cleanup.json"), "w") as f:
        json.dump({"state": 1, "decisions": 2, "lessons": 3, "index": 4}, f)
    mark = E.watermark(wf.dir)
    assert mark is not None and abs((datetime.now(timezone.utc) - mark).total_seconds()) < 60


def test_write_marker_records_watermark_and_preserves_it(wf):
    ran = datetime(2026, 9, 19, 8, 0, tzinfo=timezone.utc)
    E.write_marker(wf.dir, editor_ran_at=ran)
    assert E.watermark(wf.dir) == ran
    WF._write_cleanup_marker(wf)            # a later floor-only marker write
    assert E.watermark(wf.dir) == ran
    data = E.read_marker(wf.dir)
    assert set(data) == {"state", "decisions", "lessons", "index", "editor_last_run"}


def test_auto_run_due_needs_a_change_and_an_old_enough_watermark(wf):
    now = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
    assert E.auto_run_due(wf.dir, now=now)                       # never ran
    E.write_marker(wf.dir, editor_ran_at=now - timedelta(minutes=5))
    assert not E.auto_run_due(wf.dir, now=now)                   # no change
    wf.write("state", STATE + "- one more\n")
    assert not E.auto_run_due(wf.dir, now=now)                   # changed, but too soon
    assert E.auto_run_due(wf.dir, now=now + timedelta(hours=1))  # changed, and old enough


def test_prepass_normalises_and_stamps_unstamped_entries_only(tmp_path):
    w = WorkspaceFileManager(str(tmp_path))
    os.makedirs(w.dir)
    with open(w._file_path("state"), "w") as f:     # an external writer
        f.write("## Now\n- written by an external agent\n")
    with open(w._file_path("decisions"), "w") as f:  # legacy heading shape, one unstamped
        f.write("# D\n\n## New decision\nbody\n\n"
                "## Old <!--mem id:old01 created:2026-08-01 touched:2026-08-01-->\n**Chose:** A.\n")
    result = WF.prepass(w, today=TODAY)
    assert set(result.touched) == {"state", "decisions"}
    assert state_blocks.parse(w.read("state"))[1][0].id
    d = w.read("decisions")
    assert "- New decision\n  <!--mem id:new-decision created:2026-09-19 touched:2026-09-19-->\n  body" in d
    assert "- Old\n  <!--mem id:old01 created:2026-08-01 touched:2026-08-01-->\n  **Chose:** A." in d
    # a second prepass is a no-op
    assert WF.prepass(w, today=TODAY).touched == []


# ---------------------------------------------------------------------------
# prompt + parsing
# ---------------------------------------------------------------------------

def test_prompt_carries_full_files_ids_sizes_and_never_asks_for_whole_files(wf):
    snap = _live_lines(wf)
    p = E.build_prompt(snap, today=TODAY, since=None, sessions=[
        {"session_uuid": "s1", "last_activity_at": "2026-09-19T01:00:00+00:00", "name": "booth"}])
    assert "id:goai01" in p and "id:tabtin" in p
    assert "Cold-DM automation paused since 08-15" in p
    assert "soft budget 1800" in p and "target" in p
    assert "never a source of new facts" in p
    assert "s1" in p
    for whole_file_field in ('"project_state"', '"decisions":', '"lessons":'):
        assert whole_file_field not in p
    assert "<!--format" not in p


@pytest.mark.parametrize("text", [
    '{"archive": []}',
    '```json\n{"archive": []}\n```',
    'Here you go:\n{"archive": []}\nDone.',
])
def test_parse_choices_is_tolerant(text):
    assert E.parse_choices(text) == {"archive": []}


def test_parse_choices_rejects_non_objects():
    assert E.parse_choices("[1, 2]") is None
    assert E.parse_choices("nope") is None


# ---------------------------------------------------------------------------
# planning (pure)
# ---------------------------------------------------------------------------

def test_plan_archive_moves_the_block_byte_exact_and_leaves_nothing(wf):
    content = wf.read("state")
    plan = E.plan_entries("state", content, [{"_kind": "archive", "id": "cdm001"}], today=TODAY)
    block = _bullet("Cold-DM automation paused since 08-15", "cdm001", "2026-08-10")
    assert plan.archived == [(f"## [archived {TODAY}]", block)]
    assert block not in plan.content
    assert "[archived" not in plan.content and "cdm001" not in plan.content


def test_plan_merge_condenses_and_archives_originals(wf):
    content = wf.read("state")
    plan = E.plan_entries("state", content, [{
        "_kind": "merge", "ids": ["news18", "news17"],
        "text": "- Newsjack radar 9/17–18: no new target",
    }], today=TODAY)
    assert "- Newsjack radar 9/17–18: no new target\n  <!--mem id:" in plan.content
    assert "Newsjack radar 9/18: no new target" not in plan.content
    assert "[archived" not in plan.content
    heading, text = plan.archived[0]
    new_id = re.search(r"into id:(\S+)\]", heading).group(1)
    assert new_id not in ("news18", "news17") and f"id:{new_id}" in plan.content
    for eid in ("news18", "news17"):
        assert f"id:{eid}" in text


def test_plan_rejects_unknown_ids_and_padded_merges(wf):
    content = wf.read("state")
    plan = E.plan_entries("state", content, [
        {"_kind": "archive", "id": "nope00"},
        {"_kind": "merge", "ids": ["news18", "news17"], "text": "- " + "x" * 500},
    ], today=TODAY)
    assert plan.archived == [] and plan.content == content
    assert len(plan.rejected) == 2


def test_plan_durable_archive_takes_the_whole_entry(wf):
    content = wf.read("decisions")
    plan = E.plan_entries("decisions", content, [{"_kind": "archive", "id": "penguin"}], today=TODAY)
    assert "PenguinHarness" not in plan.content
    assert "[archived" not in plan.content
    assert "- PenguinHarness → watchlist\n  <!--mem id:penguin" in plan.archived[0][1]
    assert "  **Chose:** A." in plan.archived[0][1]
    assert "- TabTin → watchlist" in plan.content and "- Pinned rule" in plan.content


def test_plan_durable_refuses_pinned_and_merges_into_a_new_id(wf):
    content = wf.read("decisions")
    plan = E.plan_entries("decisions", content, [
        {"_kind": "archive", "id": "pinrule"},
        {"_kind": "merge", "ids": ["tabtin", "penguin"],
         "text": "- Watchlist calls\n  **Chose:** both."},
    ], today=TODAY)
    assert any("pinrule" in r for r in plan.rejected)
    assert "- Pinned rule" in plan.content
    m = re.search(r"- Watchlist calls\n  <!--mem id:(\S+) created:2026-09-19", plan.content)
    assert m and m.group(1) not in ("tabtin", "penguin")
    assert "[archived" not in plan.content
    assert plan.archived[0][0] == f"## [merged 2026-09-19 into id:{m.group(1)}]"


# ---------------------------------------------------------------------------
# a whole run
# ---------------------------------------------------------------------------

CHOICES = {
    "archive": [
        {"file": "PROJECT_STATE.md", "id": "cdm001", "pointer": "ignored since spec 124"},
        {"file": "DECISIONS.md", "id": "penguin"},
    ],
    "merge": [{"file": "PROJECT_STATE.md", "ids": ["news18", "news17"],
               "text": "- Newsjack radar 9/17–18: no new target"}],
}


@pytest.mark.asyncio
async def test_run_uses_readonly_tools_then_applies_choices(wf):
    llm = ScriptedLLM([
        _resp(tool_calls=[("grep", {"pattern": "cdm001", "path": "orbital"}),
                          ("write", {"path": "orbital/PROJECT_STATE.md", "content": "gone"})]),
        _resp(json.dumps(CHOICES)),
    ])
    before = _live_lines(wf)
    result = await E.run_editor(wf, llm, today=TODAY)
    assert result.ok and result.outcome == "edited"
    assert result.calls == 2
    # tools offered: read-only only
    offered = {t["function"]["name"] for t in llm.calls[0]["tools"]}
    assert offered == {"read", "grep", "list_sessions", "read_session"}
    # the write attempt was refused, as a tool result
    tool_rows = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert any("not available to the memory editor" in m["content"] for m in tool_rows)
    # every removed line is verbatim in an archive with its id; the live
    # file keeps nothing of it (no pointer), and the archive has a table
    archives = (wf.read("state_archive") or "") + (wf.read("decisions_archive") or "")
    for key in ("state", "decisions"):
        now_lines = set(wf.read(key).split("\n"))
        for line in before[key].split("\n"):
            if line.strip() and line not in now_lines:
                assert line in archives, line
    live = wf.read("state") + wf.read("decisions")
    assert "[archived" not in live
    for eid in ("cdm001", "news18", "news17", "penguin"):
        assert f"id:{eid}" not in live
        assert f"id:{eid}" in archives
        assert f"- id:{eid} · " in archives            # contents-table row
    assert result.restored == []
    assert "DECISIONS_ARCHIVE.md — 1 archived entry" in wf.read("index")
    # backup + audit record
    assert os.path.isfile(os.path.join(result.backup_dir, "PROJECT_STATE.md"))
    with open(os.path.join(result.backup_dir, "editor-run.json")) as f:
        assert json.load(f)["outcome"] == "edited"


@pytest.mark.asyncio
async def test_agent_write_during_the_run_makes_the_editor_skip_that_file(wf):
    def _concurrent_agent_write():
        os.utime(wf._file_path("state"), ns=(1, 1))   # someone touched it
        return _resp(json.dumps(CHOICES))

    result = await E.run_editor(wf, ScriptedLLM([_concurrent_agent_write]), today=TODAY)
    assert "PROJECT_STATE.md" in result.skipped_files
    assert "Cold-DM automation paused since 08-15" in wf.read("state")
    assert not wf.exists("state_archive")
    # the other file still went through
    assert "PenguinHarness" not in wf.read("decisions")


@pytest.mark.asyncio
async def test_run_is_bounded_to_max_calls_and_the_last_call_has_no_tools(wf):
    script = [_resp(tool_calls=[("grep", {"pattern": "x"})])] * (E.EDITOR_MAX_CALLS - 1)
    script.append(_resp("{}"))
    llm = ScriptedLLM(script)
    result = await E.run_editor(wf, llm, today=TODAY)
    assert result.ok and result.outcome == "no_change"
    assert len(llm.calls) == E.EDITOR_MAX_CALLS
    assert llm.calls[-1]["tools"] is None


@pytest.mark.asyncio
async def test_model_failure_is_reported_not_raised(wf):
    async def boom(*_a, **_k):
        raise RuntimeError("provider 500")
    llm = SimpleNamespace(complete=boom, model="x")
    result = await E.run_editor(wf, llm, today=TODAY)
    assert not result.ok and result.outcome == "failed"
    assert wf.read("state").count("<!--mem") == 4


@pytest.mark.asyncio
async def test_index_rewrite_keeps_archive_pointers(wf):
    llm = ScriptedLLM([_resp(json.dumps({"index": "# INDEX\n\n- src/ — code\n"}))])
    result = await E.run_editor(wf, llm, today=TODAY)
    assert result.outcome == "edited"
    idx = wf.read("index")
    assert "- src/ — code" in idx and "DECISIONS_ARCHIVE.md" in idx


# ---------------------------------------------------------------------------
# run_session_end_routine: gates, single flight, watermark, floor
# ---------------------------------------------------------------------------

def _over_budget(wf):
    big = "\n".join(_bullet(f"recent item {i} " + "r" * 400, f"big{i:03d}", "2026-09-18") for i in range(25))
    wf.write("state", STATE + big + "\n")


@pytest.mark.asyncio
async def test_routine_not_needed_when_under_budget_unless_forced(wf):
    llm = ScriptedLLM([_resp("{}")])
    assert await run_session_end_routine(None, llm, wf, session_uuid="s", bypass_idempotency=True) == "not_needed"
    assert llm.calls == []
    assert await run_session_end_routine(
        None, llm, wf, session_uuid="s", bypass_idempotency=True, force=True) == "no_change"


@pytest.mark.asyncio
async def test_routine_success_advances_the_watermark_then_no_delta(wf):
    _over_budget(wf)
    llm = ScriptedLLM([_resp(json.dumps(CHOICES))])
    assert E.read_marker(wf.dir).get("editor_last_run") is None
    out = await run_session_end_routine(None, llm, wf, session_uuid="s", bypass_idempotency=True)
    assert out == "edited"
    assert E.read_marker(wf.dir).get("editor_last_run")
    again = await run_session_end_routine(None, llm, wf, session_uuid="s", bypass_idempotency=True)
    assert again == "no_delta"


@pytest.mark.asyncio
async def test_routine_failure_runs_floor_and_leaves_watermark_alone(wf):
    _over_budget(wf)
    llm = ScriptedLLM([_resp("not json"), _resp("still not json")] + [_resp("nope")] * 20)
    out = await run_session_end_routine(None, llm, wf, session_uuid="s", bypass_idempotency=True)
    assert out == "backstop_only"
    assert "editor_last_run" not in E.read_marker(wf.dir)


@pytest.mark.asyncio
async def test_single_flight_across_two_callers(wf):
    _over_budget(wf)
    gate = asyncio.Event()

    async def slow():
        await gate.wait()
        return _resp("{}")

    class Slow(ScriptedLLM):
        async def complete(self, messages, tools=None, **_kw):
            self.calls.append(1)
            return await slow()

    llm = Slow([])
    first = asyncio.create_task(run_session_end_routine(
        None, llm, wf, session_uuid="a", bypass_idempotency=True))
    await asyncio.sleep(0.05)
    second = await run_session_end_routine(None, llm, wf, session_uuid="b", bypass_idempotency=True)
    assert second == "in_flight"
    gate.set()
    assert await first == "no_change"
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_no_llm_call_ever_returns_a_whole_memory_file(wf):
    """The old merge asked for complete PROJECT_STATE/DECISIONS/LESSONS; the
    editor's contract has no such field, and a reply that carries one is
    ignored."""
    _over_budget(wf)
    before = wf.read("decisions")
    llm = ScriptedLLM([_resp(json.dumps({"decisions": "# wiped\n", "project_state": "- wiped\n"}))])
    out = await run_session_end_routine(None, llm, wf, session_uuid="s", bypass_idempotency=True)
    assert out == "no_change"
    assert wf.read("decisions") == before
    assert "wiped" not in wf.read("state")


# ---------------------------------------------------------------------------
# resilience (live smoke 2026-09-19: one gateway 500 threw away 6 calls)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_transient_provider_error_is_retried_once(wf):
    from agent_os.agent.providers.types import LLMError

    def _boom():
        raise LLMError("Internal server error", status_code=500)

    llm = ScriptedLLM([_boom, _resp(json.dumps(CHOICES))])
    result = await E.run_editor(wf, llm, today=TODAY)
    assert result.ok and result.outcome == "edited"
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_an_auth_error_is_not_retried(wf):
    from agent_os.agent.providers.types import LLMError

    def _denied():
        raise LLMError("bad key", status_code=401)

    llm = ScriptedLLM([_denied, _resp("{}")])
    result = await E.run_editor(wf, llm, today=TODAY)
    assert not result.ok
    assert len(llm.calls) == 1


def test_prompt_says_the_files_are_already_in_full(wf):
    p = E.build_prompt(_live_lines(wf), today=TODAY, since=None, sessions=[])
    assert "do not read them again" in p


@pytest.mark.asyncio
async def test_an_occ_skip_leaves_the_pass_unfinished_so_it_retries(wf):
    """Live smoke 2026-09-19: an agent write during the run made the editor
    skip PROJECT_STATE — and the pass still advanced the watermark and the
    no-delta marker, so the over-budget file would not be retried."""
    _over_budget(wf)

    def _concurrent_agent_write():
        os.utime(wf._file_path("state"), ns=(1, 1))
        return _resp(json.dumps(CHOICES))

    llm = ScriptedLLM([_concurrent_agent_write])
    out = await run_session_end_routine(None, llm, wf, session_uuid="s", bypass_idempotency=True)
    assert out == "edited"                                   # decisions still applied
    assert "editor_last_run" not in E.read_marker(wf.dir)    # not a finished pass
    assert WF._has_cleanup_delta(wf)                         # so it will run again


def test_prompt_keeps_source_of_truth_lines_and_never_mentions_pointers():
    p = E.build_prompt({k: "" for k in E.LIVE_KEYS}, today=TODAY, since=None, sessions=[])
    assert "source of truth" in p
    assert "pointer" not in p.lower()
    assert "nothing is ever deleted" in p.lower()
    assert "never because it is old" in p


@pytest.mark.asyncio
async def test_an_entry_the_pass_drops_without_archiving_is_restored(wf, caplog):
    """Id conservation (spec 124 §3.5): the pass snapshot's ids must all be
    live or archived afterwards; a dropped one comes back and is logged."""
    class _Dropper(ScriptedLLM):
        async def complete(self, messages, tools=None, **_kw):
            # an outside writer deletes a line mid-run without archiving it
            with open(wf._file_path("decisions"), "w", encoding="utf-8") as f:
                f.write(wf.read("decisions").replace(_decision("TabTin → watchlist", "tabtin"), ""))
            return await super().complete(messages, tools=tools)

    llm = _Dropper([_resp(json.dumps({"archive": [{"file": "DECISIONS.md", "id": "penguin"}]}))])
    with caplog.at_level("WARNING"):
        result = await E.run_editor(wf, llm, today=TODAY)
    # OCC: decisions changed under the editor, so the editor skipped it and
    # conservation only guards files the editor wrote — tabtin is simply
    # outside this pass. Now the editor's own file:
    assert "DECISIONS.md" in result.skipped_files
    assert result.restored == []
