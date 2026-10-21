# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 086: text tool results are never rewritten in the prepared prompt.

``_prune_old_tool_results`` used to replace every text tool result over 500
chars with a ``[Truncated]`` stub once it was more than 5 assistant turns old.
The horizon moved one turn per call, so each call rewrote exactly the result
that had just crossed it, and everything after it missed the provider's prompt
cache (replay-measured ceiling 38-62% on a tool-heavy session). These tests pin
the invariant the cache depends on: between two calls, the prepared prompt only
changes at its tail.
"""

import json

from agent_os.agent.context import ContextManager
from agent_os.agent.prompt_builder import Autonomy, PromptContext
from agent_os.agent.session import Session


class _Builder:
    def build(self, context: PromptContext) -> tuple[str, str, str]:
        return ("cached-system-prefix", "semi-stable-suffix", "dynamic-runtime")


def _ctx(workspace: str) -> PromptContext:
    return PromptContext(
        workspace=workspace,
        model="test-model",
        autonomy=Autonomy.HANDS_OFF,
        enabled_agents=[],
        tool_names=["read"],
        os_type="linux",
        datetime_now="2026-01-01T00:00:00",
        context_usage_pct=0.0,
    )


def _append_read_turn(session: Session, n: int, content: str) -> None:
    tc_id = f"tc_read_{n}"
    session.append({
        "role": "assistant",
        "content": None,
        "source": "management",
        "tool_calls": [{"id": tc_id, "type": "function",
                        "function": {"name": "read",
                                     "arguments": json.dumps({"path": f"f{n}.md"})}}],
    })
    session.append_tool_result(tc_id, content)


def _first_difference(a: list[dict], b: list[dict]) -> int:
    for i, (x, y) in enumerate(zip(a, b)):
        if json.dumps(x, sort_keys=True) != json.dumps(y, sort_keys=True):
            return i
    return min(len(a), len(b))


def _tool_contents(messages: list[dict]) -> dict[str, object]:
    return {m["tool_call_id"]: m["content"] for m in messages if m.get("role") == "tool"}


def test_text_tool_results_never_rewritten_in_context(tmp_path):
    session = Session.new("cache_stability", str(tmp_path))
    session.append({"role": "user", "content": "Read the docs.", "source": "user"})
    old_result = "\n".join(f"line {i}: " + "A" * 60 for i in range(30))
    assert len(old_result) >= 2_000
    _append_read_turn(session, 0, old_result)
    for n in range(1, 13):
        _append_read_turn(session, n, f"file {n}\n" + "B" * 800)

    mgr = ContextManager(session, _Builder(), _ctx(str(tmp_path)),
                         model_context_limit=200_000)
    first = mgr.prepare()

    _append_read_turn(session, 13, "file 13\n" + "C" * 800)
    second = mgr.prepare()

    # (a) Every tool result, including the 13-turn-old one, reaches the model
    # exactly as the session holds it, on both calls.
    stored = _tool_contents(session.get_messages())
    for prepared in (first, second):
        sent = _tool_contents(prepared)
        assert sent["tc_read_0"] == old_result
        assert sent == {k: stored[k] for k in sent}

    # (b) The second prompt extends the first: nothing before the appended
    # tail differs (the first call's trailing runtime row is where the new
    # turn lands).
    diff_at = _first_difference(first, second)
    assert diff_at >= len(first) - 2, (
        f"prompt rewritten mid-history at message {diff_at} of {len(first)}: "
        f"{json.dumps(first[diff_at])[:200]}"
    )


def test_old_multimodal_text_result_is_not_summarised(tmp_path):
    """A list-shaped result with no image is no longer summarised as [Pruned]."""
    blocks = [{"type": "text", "text": "page one " * 80},
              {"type": "text", "text": "page two " * 80}]
    msgs = [{"role": "assistant", "content": None,
             "tool_calls": [{"id": "tc_doc", "function": {"name": "read"}}]},
            {"role": "tool", "tool_call_id": "tc_doc", "content": blocks}]
    for n in range(8):
        msgs.append({"role": "assistant", "content": f"reply {n}"})

    out = ContextManager._prune_old_tool_results(None, msgs)

    assert out[1]["content"] == blocks


# ---------------------------------------------------------------------------
# Spec 112 A5: media tool results are pruned only at run boundaries.
#
# ``_prune_old_tool_results`` replaced a snapshot two turns after it landed and
# an image-bearing result one turn after. The horizon moved every call, so a
# browser-heavy session rewrote one mid-history message on EVERY call and the
# prefix cache missed from there (527 pairs / 25M tokens in two months, §1.6).
# Results produced in the current run now stay intact until ``begin_run()``
# advances the horizon; the previous run's expired media is pruned once, on
# the next run's first call. Screenshots were always replaced — unchanged.
# ---------------------------------------------------------------------------


def _append_snapshot_turn(session: Session, n: int) -> None:
    tc_id = f"tc_snap_{n}"
    session.append({
        "role": "assistant", "content": None, "source": "management",
        "tool_calls": [{"id": tc_id, "type": "function",
                        "function": {"name": "browser",
                                     "arguments": json.dumps({"action": "snapshot"})}}],
    })
    session.append_tool_result(
        tc_id, f"SNAPSHOT-{n} " + "- [ref=e1] link\n" * 40,
        meta={"snapshot_stats": {"refs": 40}, "url": f"https://example.test/{n}"},
    )


def _append_screenshot_turn(session: Session, n: int) -> None:
    tc_id = f"tc_shot_{n}"
    session.append({
        "role": "assistant", "content": None, "source": "management",
        "tool_calls": [{"id": tc_id, "type": "function",
                        "function": {"name": "browser",
                                     "arguments": json.dumps({"action": "screenshot"})}}],
    })
    session.append_tool_result(
        tc_id, "base64-bytes-" * 100, meta={"screenshot_path": f"/shots/{n}.png"},
    )


def _snapshot_contents(messages: list[dict]) -> dict[str, str]:
    return {m["tool_call_id"]: m["content"] for m in messages
            if m.get("role") == "tool" and m["tool_call_id"].startswith("tc_snap_")}


def test_snapshots_from_this_run_survive_until_the_run_ends(tmp_path):
    session = Session.new("prune_run", str(tmp_path))
    session.append({"role": "user", "content": "Browse.", "source": "user"})
    mgr = ContextManager(session, _Builder(), _ctx(str(tmp_path)),
                         model_context_limit=200_000)
    mgr.begin_run()
    for n in range(5):
        _append_snapshot_turn(session, n)
    prepared = mgr.prepare()
    sent = _snapshot_contents(prepared)
    # The oldest one is four turns old — far past the old 2-turn TTL.
    for n in range(5):
        assert sent[f"tc_snap_{n}"].startswith(f"SNAPSHOT-{n} "), n


def test_previous_runs_expired_snapshots_are_pruned_once_on_the_next_run(tmp_path):
    session = Session.new("prune_next", str(tmp_path))
    session.append({"role": "user", "content": "Browse.", "source": "user"})
    mgr = ContextManager(session, _Builder(), _ctx(str(tmp_path)),
                         model_context_limit=200_000)
    mgr.begin_run()
    for n in range(5):
        _append_snapshot_turn(session, n)
    session.append({"role": "assistant", "content": "Done browsing.", "source": "management"})

    session.append({"role": "user", "content": "Next task.", "source": "user"})
    mgr.begin_run()
    first = mgr.prepare()
    sent = _snapshot_contents(first)
    # Older than two turns at run start → replaced by the one-liner.
    for n in range(3):
        assert sent[f"tc_snap_{n}"].startswith("[Snapshot of https://example.test/"), n
    # Within the TTL at run start → still intact.
    assert sent["tc_snap_4"].startswith("SNAPSHOT-4 ")

    # And the new run's own turns do not move the horizon for the old rows:
    # the prepared prompt only extends at the tail from here on.
    for n in range(5, 9):
        _append_snapshot_turn(session, n)
        second = mgr.prepare()
        assert _snapshot_contents(second)[f"tc_snap_{n}"].startswith(f"SNAPSHOT-{n} ")
        assert _first_difference(first, second) >= len(first) - 1
        first = second


def test_screenshots_are_always_replaced_regardless_of_run(tmp_path):
    session = Session.new("prune_shot", str(tmp_path))
    session.append({"role": "user", "content": "Shoot.", "source": "user"})
    mgr = ContextManager(session, _Builder(), _ctx(str(tmp_path)),
                         model_context_limit=200_000)
    mgr.begin_run()
    _append_screenshot_turn(session, 0)
    prepared = mgr.prepare()
    shot = next(m for m in prepared if m.get("role") == "tool" and m["tool_call_id"] == "tc_shot_0")
    assert shot["content"] == "[Screenshot saved to /shots/0.png]"


def test_image_results_from_this_run_survive_until_the_run_ends(tmp_path):
    session = Session.new("prune_img", str(tmp_path))
    session.append({"role": "user", "content": "Look.", "source": "user"})
    mgr = ContextManager(session, _Builder(), _ctx(str(tmp_path)),
                         model_context_limit=200_000)
    mgr.begin_run()
    blocks = [{"type": "text", "text": "the chart"},
              {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
    session.append({
        "role": "assistant", "content": None, "source": "management",
        "tool_calls": [{"id": "tc_img", "type": "function",
                        "function": {"name": "read", "arguments": json.dumps({"path": "c.png"})}}],
    })
    session.append_tool_result("tc_img", blocks, meta={"image_path": "c.png"})
    for n in range(3):
        _append_read_turn(session, n, f"file {n}")
    prepared = mgr.prepare()
    img = next(m for m in prepared if m.get("role") == "tool" and m["tool_call_id"] == "tc_img")
    assert img["content"] == blocks

    session.append({"role": "user", "content": "Again.", "source": "user"})
    mgr.begin_run()
    prepared = mgr.prepare()
    img = next(m for m in prepared if m.get("role") == "tool" and m["tool_call_id"] == "tc_img")
    assert img["content"] == "[Image: c.png] the chart"
