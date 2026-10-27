# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 138 history readers for pi, Kimi Code and MiniMax Code.

Each reader turns one native session into a ``HistorySession`` (contract §2):
the user's cleaned words, the stop cause of the last turn, and the files the
session touched. Fixtures are SYNTHETIC rows in the real on-disk formats
(``tests/fixtures/history_138/`` via ``_history_138_builders``).
"""

from __future__ import annotations

import json
import os

import pytest

from agent_os.onboarding import history_types as ht
from agent_os.onboarding.sources import load_sources
from agent_os.onboarding.sources import kimi_code, minimax_code, pi
from tests.unit import _history_138_builders as b


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return str(h)


CWD = b.native("/Users/x/proj")


# --------------------------------------------------------------------------
# Shared: error classification additions found in pi / mcode error text
# --------------------------------------------------------------------------


class TestClassifyAdditions:
    def test_low_credit_balance_is_a_usage_limit(self):
        stop = ht.classify_error_text(
            '400 {"error":{"message":"Your credit balance is too low to access the API."}}')
        assert stop.cause == ht.CAUSE_USAGE_LIMIT

    @pytest.mark.parametrize("text", [
        "402: This request requires more credits, or fewer max_tokens.",  # OpenRouter via pi
        "429 You exceeded your current quota, please check your plan",
        "Insufficient balance",
    ])
    def test_billing_stops_are_usage_limits(self, text):
        assert ht.classify_error_text(text).cause == ht.CAUSE_USAGE_LIMIT

    def test_mcode_model_not_available_for_route(self):
        stop = ht.classify_error_text(
            'Model "minimax/MiniMax-M3.1" is not available for the "managed_token_plan" route')
        assert stop.cause == ht.CAUSE_MODEL_UNAVAILABLE


def test_load_sources_has_the_three_new_readers():
    agents = [s.agent for s in load_sources()]
    for slug in ("pi", "kimi-code", "minimax-code"):
        assert slug in agents


# --------------------------------------------------------------------------
# pi
# --------------------------------------------------------------------------


class TestPi:
    def test_session_files_lists_the_per_folder_jsonl(self, home):
        path = b.install_pi(home, CWD)
        assert pi.SOURCE.session_files(home) == [path]

    def test_reads_a_finished_session(self, home):
        path = b.install_pi(home, CWD)
        s = pi.SOURCE.read(path)
        assert (s.agent, s.native_id, s.path, s.cwd) == ("pi", b.PI_ID, path, CWD)
        assert s.resume_path == path
        assert s.title == "Rename helper"
        assert s.started_at == "2026-10-20T10:00:00+00:00"
        assert s.ended_at == "2026-10-20T10:00:04+00:00"
        assert s.first_user_text == s.last_user_text == f"Rename the helper in {CWD}/src/util.py"
        assert s.last_user_at == "2026-10-20T10:00:01+00:00"
        assert s.user_turns == 1 and not s.automation and not s.orbital_started
        assert s.stop is None
        util = b.norm(f"{CWD}/src/util.py")
        assert s.edits == {util: 1}
        assert s.reads[util] == 1  # relative path resolved against the cwd
        assert s.mentions == {util: 1}
        assert s.skills == ["tidy"]

    def test_provider_error_with_a_reset(self, home):
        path = b.install_pi(home, CWD, extra=[
            b.pi_message("user", [{"type": "text", "text": "continue"}],
                         ts="2026-10-20T11:00:00.000Z"),
            b.pi_message("assistant", [], ts="2026-10-20T11:00:01.000Z", stopReason="error",
                         errorMessage="You've hit your usage limit · resets 10pm (UTC)"),
        ])
        s = pi.SOURCE.read(path)
        assert s.stop.cause == ht.CAUSE_USAGE_LIMIT
        assert s.stop.resets_at == "2026-10-20T22:00:00+00:00"
        assert s.last_user_text == "continue"

    def test_unclassified_error_still_counts_as_unfinished(self, home):
        path = b.install_pi(home, CWD, extra=[
            b.pi_message("user", "go on"),
            b.pi_message("assistant", [], stopReason="error", errorMessage="Something odd"),
        ])
        stop = pi.SOURCE.read(path).stop
        assert stop.cause == ht.CAUSE_INTERRUPTED and stop.detail == "Something odd"

    def test_aborted_turn_is_interrupted(self, home):
        path = b.install_pi(home, CWD, extra=[
            b.pi_message("user", "long job"),
            b.pi_message("assistant", [{"type": "text", "text": "Start"}], stopReason="aborted"),
        ])
        assert pi.SOURCE.read(path).stop.cause == ht.CAUSE_INTERRUPTED

    def test_unanswered_last_message_is_no_reply(self, home):
        path = b.install_pi(home, CWD, extra=[b.pi_message("user", "and the tests?")])
        s = pi.SOURCE.read(path)
        assert s.stop.cause == ht.CAUSE_NO_REPLY
        assert s.last_user_text == "and the tests?" and s.user_turns == 2

    def test_answer_that_ends_with_a_question_is_finished(self, home):
        path = b.install_pi(home, CWD, extra=[
            b.pi_message("user", "deploy?"),
            b.pi_message("assistant", [{"type": "text", "text": "Which env?"}], stopReason="stop"),
        ])
        assert pi.SOURCE.read(path).stop is None

    def test_orbital_injected_and_automation_turns(self, home):
        path = b.install_pi(home, CWD, rows=[
            b.pi_message("user", "[Triggered by schedule 'nightly' (every day at 9)]\n\nRun the suite"),
            b.pi_message("assistant", [{"type": "text", "text": "Done"}], stopReason="stop"),
            b.pi_message("user", "[Orbital memory] Your last turn is ending; record it."),
            b.pi_message("assistant", [{"type": "text", "text": "Noted"}], stopReason="stop"),
        ])
        s = pi.SOURCE.read(path)
        assert s.automation is True and s.user_turns == 0
        assert s.last_user_text is None and s.stop is None

    def test_headerless_or_empty_file_is_none(self, home, tmp_path):
        p = tmp_path / "x.jsonl"
        p.write_text('{"type":"message"}\n')
        assert pi.SOURCE.read(str(p)) is None
        assert pi.SOURCE.read(str(tmp_path / "missing.jsonl")) is None

    def test_header_only_session_is_none(self, home):
        path = b.install_pi(home, CWD, rows=[])
        assert pi.SOURCE.read(path) is None


# --------------------------------------------------------------------------
# Kimi Code
# --------------------------------------------------------------------------


class TestKimi:
    def test_session_files_are_the_main_agent_wires(self, home):
        path = b.install_kimi(home, CWD)
        assert kimi_code.SOURCE.session_files(home) == [path]

    def test_reads_a_finished_session(self, home):
        path = b.install_kimi(home, CWD)
        s = kimi_code.SOURCE.read(path)
        assert (s.agent, s.native_id, s.path, s.cwd) == ("kimi-code", b.KIMI_ID, path, CWD)
        assert s.title == "Add a test for app.py"
        assert s.first_user_text == f"Add a test for {CWD}/app.py"
        assert s.user_turns == 1  # the injected date reminder is not a turn
        assert s.stop is None and s.resume_path is None
        assert s.creates == {b.norm(f"{CWD}/tests/test_app.py"): 1}
        assert s.reads == {b.norm(f"{CWD}/app.py"): 1}
        assert s.started_at == "2026-10-20T10:00:00+00:00"  # state.json createdAt
        assert s.ended_at == "2026-10-20T10:00:03+00:00"

    def test_failed_turn_classifies_the_error(self, home):
        path = b.install_kimi(home, CWD, extra=[
            b.kimi_message("user", "next", origin="user"),
            b.kimi_turn_ended("failed", error={
                "code": "provider.auth_error",
                "message": "403 Your current subscription does not have access"}),
        ])
        s = kimi_code.SOURCE.read(path)
        assert s.stop.cause == ht.CAUSE_NETWORK_AUTH
        assert s.last_user_text == "next"

    def test_cancelled_turn_is_interrupted(self, home):
        path = b.install_kimi(home, CWD, extra=[
            b.kimi_message("user", "next", origin="user"),
            b.kimi_message("assistant", "Working"),
            b.kimi_turn_ended("cancelled"),
        ])
        assert kimi_code.SOURCE.read(path).stop.cause == ht.CAUSE_INTERRUPTED

    def test_user_message_never_answered(self, home):
        path = b.install_kimi(home, CWD, extra=[b.kimi_message("user", "hello?", origin="user")])
        assert kimi_code.SOURCE.read(path).stop.cause == ht.CAUSE_NO_REPLY

    def test_legacy_tool_call_shape_and_missing_origin(self, home):
        path = b.install_kimi(home, CWD, wire=[
            b.kimi_message("user", "fix it"),
            b.kimi_message("assistant", "Fixed", tool_calls=[
                {"id": "t", "function": {"name": "Edit", "arguments": json.dumps(
                    {"path": f"{CWD}/a.py", "old_string": "a", "new_string": "b"})}}]),
            b.kimi_turn_ended("completed"),
        ])
        s = kimi_code.SOURCE.read(path)
        assert s.user_turns == 1 and s.edits == {b.norm(f"{CWD}/a.py"): 1} and s.stop is None

    def test_child_and_archived_sessions_are_skipped(self, home):
        child = b.install_kimi(home, CWD, state={
            "custom": {"parent_session_id": "session_x", "child_session_kind": "child"}})
        assert kimi_code.SOURCE.read(child) is None
        archived = b.install_kimi(home, CWD + "2", sid="session_aaaa", state={"archived": True})
        assert kimi_code.SOURCE.read(archived) is None

    def test_missing_state_falls_back_to_the_dir_id(self, home):
        path = b.install_kimi(home, CWD)
        os.remove(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(path))),
                               "state.json"))
        s = kimi_code.SOURCE.read(path)
        assert s.native_id == b.KIMI_ID and s.cwd is None


# --------------------------------------------------------------------------
# MiniMax Code
# --------------------------------------------------------------------------


class TestMiniMax:
    def test_session_files_are_the_dated_messages_files(self, home):
        path = b.install_minimax(home, CWD)
        assert minimax_code.SOURCE.session_files(home) == [path]

    def test_reads_a_finished_session(self, home):
        path = b.install_minimax(home, CWD)
        s = minimax_code.SOURCE.read(path)
        assert (s.agent, s.native_id, s.path) == ("minimax-code", b.MINIMAX_ID, path)
        assert s.cwd == CWD  # only the runtime-state sqlite records it
        assert s.title == "Summarise README"
        # The <system-reminder>/<agent-context> wrapper is not the user's.
        assert s.first_user_text == f"Summarise {CWD}/README.md"
        assert s.user_turns == 1 and s.stop is None
        assert s.reads == {b.norm(f"{CWD}/README.md"): 1}
        assert s.creates == {b.norm(f"{CWD}/SUMMARY.md"): 1}
        assert s.skills == ["summarize"]

    def test_canonical_text_range_wins_over_unknown_wrappers(self, home):
        text = "<mcode-new-wrapper>\nnot yours\n</mcode-new-wrapper>\n\nship it"
        row = b.minimax_user(text)
        path = b.install_minimax(home, CWD, messages=[row])
        # Re-stamp the range by hand: the builder only knows the reminder marker.
        rows = [json.loads(line) for line in open(path, encoding="utf-8")]
        rows[0]["message"]["canonicalTextRange"] = {
            "startOffset": text.index("ship it"), "endOffset": len(text)}
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows))
        assert minimax_code.SOURCE.read(path).last_user_text == "ship it"

    def test_orbital_recap_in_a_turn_keeps_only_the_new_words(self, home):
        recap = ("Conversation so far (earlier messages in this chat):\n\nuser: hi\n\n"
                 "--- end of conversation so far ---\n\nnow the docs")
        path = b.install_minimax(home, CWD, extra=[
            b.minimax_user("<system-reminder>\nx\n</system-reminder>\n\n" + recap),
            b.minimax_assistant([{"type": "text", "text": "ok"}], "stop")])
        assert minimax_code.SOURCE.read(path).last_user_text == "now the docs"

    def test_error_and_abort_stops(self, home):
        path = b.install_minimax(home, CWD, extra=[
            b.minimax_user("again"),
            b.minimax_assistant([], "error", error="ECONNRESET while streaming")])
        assert minimax_code.SOURCE.read(path).stop.cause == ht.CAUSE_NETWORK_AUTH
        path = b.install_minimax(home, CWD + "b", sid="mvs_bbbb", extra=[
            b.minimax_user("again"),
            b.minimax_assistant([{"type": "text", "text": "par"}], "aborted")])
        assert minimax_code.SOURCE.read(path).stop.cause == ht.CAUSE_INTERRUPTED

    def test_failed_before_any_message_reads_the_runtime_state(self, home):
        # A session whose first turn failed has an EMPTY messages.jsonl; the
        # user's words and the error live only in the runtime-state sqlite.
        path = b.install_minimax(
            home, CWD, messages=[], status="error",
            error_message='Model "minimax/X" is not available for the "managed_token_plan" route',
            display_rows=[{"role": "user", "msg_content": "write the manifest",
                           "timestamp": 1792490401000}])
        s = minimax_code.SOURCE.read(path)
        assert s.user_turns == 1 and s.last_user_text == "write the manifest"
        assert s.stop.cause == ht.CAUSE_MODEL_UNAVAILABLE

    def test_sub_sessions_and_archived_are_skipped(self, home):
        sub = b.install_minimax(home, CWD, parent="mvs_parent")
        assert minimax_code.SOURCE.read(sub) is None
        arch = b.install_minimax(home, CWD, sid="mvs_cccc", archived=1)
        assert minimax_code.SOURCE.read(arch) is None
        task = b.install_minimax(home, CWD, sid="mvs_dddd", kind="task")
        assert minimax_code.SOURCE.read(task) is None

    def test_without_the_sqlite_the_session_still_reads(self, home):
        path = b.install_minimax(home, CWD)
        os.remove(os.path.join(home, ".minimax", "v2", "sqlite", "runtime-state.sqlite"))
        s = minimax_code.SOURCE.read(path)
        assert s.native_id == b.MINIMAX_ID and s.cwd is None and s.user_turns == 1


class TestWindowsPathsInText:
    """Mentions and skill folders in Windows histories (batch 1026 rc): the
    patterns knew only POSIX roots and forward slashes, so on Windows no
    mention or skill-by-path was ever found. Pattern-level, so it runs on
    every host."""

    def test_drive_paths_are_mentions(self):
        from agent_os.onboarding.sources._common import ABS_PATH_RE
        text = r'cat C:\Users\x\notes\plan.md and D:/work/app/main.py; also /Users/y/a.md'
        found = [posix or drive for posix, drive in ABS_PATH_RE.findall(text)]
        assert found == [r"C:\Users\x\notes\plan.md", "D:/work/app/main.py", "/Users/y/a.md"]

    def test_a_json_escaped_posix_path_still_stops_at_the_escape(self):
        from agent_os.onboarding.sources._common import ABS_PATH_RE
        found = [posix or drive for posix, drive in ABS_PATH_RE.findall('"/Users/x/a.md\\n"')]
        assert found == ["/Users/x/a.md"]

    def test_skill_folders_with_either_separator(self):
        from agent_os.onboarding.sources._common import SKILL_RE
        assert SKILL_RE.search(r"C:\Users\x\.claude\skills\pptx\SKILL.md").group(1) == "pptx"
        assert SKILL_RE.search(r"C:\\Users\\x\\.agents\\skills\\docx\\SKILL.md").group(1) == "docx"
        assert SKILL_RE.search("/Users/x/.codex/skills/tidy/SKILL.md").group(1) == "tidy"
        assert SKILL_RE.search(r"C:\Users\x\.claude\skills\.hidden\x") is None
