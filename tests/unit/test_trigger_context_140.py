# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 140 R2 — the trigger context lives in the first user message, not
the system prompt.

A triggered run's system prompt must be byte-identical to a chat session's of
the same project, so the provider's prefix cache is shared across session
kinds. What the old "## Trigger Context" system section said travels in the
manager's first user message instead, after the unchanged
"[Triggered by …]" first line and the task.
"""

import tempfile

import pytest
from unittest.mock import AsyncMock, MagicMock

from agent_os.agent.prompt_builder import Autonomy, PromptBuilder, PromptContext
from agent_os.daemon_v2.project_store import ProjectStore
from agent_os.daemon_v2.trigger_manager import TRIGGER_RUN_NOTE, TriggerManager
from agent_os.onboarding.text_clean import KIND_AUTOMATION, clean_user_text


# ---------------------------------------------------------------------------
# System prompt: byte-identical with and without a trigger
# ---------------------------------------------------------------------------

def _ctx(workspace, **extra):
    return PromptContext(
        workspace=workspace,
        model="deepseek-v4.1-flash",
        autonomy=Autonomy.HANDS_OFF,
        enabled_agents=[{"handle": "codex", "display_name": "Codex", "type": "cli"}],
        tool_names=["read", "write", "shell", "create_trigger", "list_triggers",
                    "update_trigger", "delete_trigger"],
        os_type="macos",
        datetime_now="2026-10-28T10:00:00",
        project_name="Marketing",
        project_instructions="Be brief.",
        **extra,
    )


@pytest.mark.parametrize("source,name", [
    ("schedule", "Morning PDF summary"),
    ("file_watch", "Inbox watcher"),
])
def test_system_prompt_is_byte_identical_with_and_without_a_trigger(source, name):
    ws = tempfile.mkdtemp()
    builder = PromptBuilder(ws)
    chat = builder.build(_ctx(ws))
    triggered = builder.build(_ctx(ws, trigger_source=source, trigger_name=name))
    assert triggered[0] == chat[0]   # cached prefix
    assert triggered[1] == chat[1]   # semi_stable
    assert triggered[2] == chat[2]   # truly_dynamic
    assert name not in "".join(triggered)


def test_no_trigger_context_section_anywhere():
    ws = tempfile.mkdtemp()
    parts = PromptBuilder(ws).build(
        _ctx(ws, trigger_source="schedule", trigger_name="Nightly"))
    assert "Trigger Context" not in "".join(parts)


# ---------------------------------------------------------------------------
# First user message: the header line, the task, then the note
# ---------------------------------------------------------------------------

def _project(trigger_type="schedule", agent=None):
    tmpdir = tempfile.mkdtemp()
    store = ProjectStore(data_dir=tmpdir)
    pid = store.create_project({
        "name": "Test Project", "workspace": tmpdir,
        "model": "gpt-4", "api_key": "sk-test",
    })
    trigger = {
        "id": "trg_aaa", "name": "Nightly", "type": trigger_type, "enabled": True,
        "task": "Do the thing", "trigger_count": 0, "last_triggered": None,
    }
    if trigger_type == "schedule":
        trigger["schedule"] = {"cron": "0 7 * * *", "human": "Daily at 7am"}
    else:
        trigger["watch_path"] = "inbox"
    if agent is not None:
        trigger["agent"] = agent
    store.update_project(pid, {"triggers": [trigger]})
    return store, pid


def _manager():
    sub_mgr = MagicMock()
    sub_mgr.send = AsyncMock(return_value="Message sent to codex.")
    mgr = MagicMock()
    mgr.is_running = MagicMock(return_value=False)
    mgr.start_agent = AsyncMock()
    mgr.new_session = AsyncMock(return_value={"session_id": "proj_sess1"})
    mgr.get_sub_agent_manager = MagicMock(return_value=sub_mgr)
    return mgr, sub_mgr


def test_note_says_what_the_old_system_section_said():
    assert "unattended" in TRIGGER_RUN_NOTE
    assert "project goals and autonomy settings" in TRIGGER_RUN_NOTE
    for tool in ("create_trigger", "list_triggers", "update_trigger", "delete_trigger"):
        assert tool in TRIGGER_RUN_NOTE


@pytest.mark.asyncio
async def test_schedule_message_keeps_its_first_line_and_carries_the_note():
    store, pid = _project("schedule")
    mgr, _ = _manager()
    await TriggerManager(store, mgr)._fire_trigger(pid, "trg_aaa")

    msg = mgr.start_agent.await_args.kwargs["initial_message"]
    assert msg == (
        "[Triggered by schedule 'Nightly' (Daily at 7am)]\n\n"
        "Do the thing\n\n" + TRIGGER_RUN_NOTE
    )


@pytest.mark.asyncio
async def test_file_watch_message_keeps_its_header_and_carries_the_note():
    store, pid = _project("file_watch")
    mgr, _ = _manager()
    await TriggerManager(store, mgr)._fire_trigger(
        pid, "trg_aaa", changed_files=["inbox/a.md", "inbox/b.md"])

    msg = mgr.start_agent.await_args.kwargs["initial_message"]
    assert msg == (
        "[Triggered by file_watch 'Nightly']\n\n"
        "Changed files: inbox/a.md, inbox/b.md\n\n"
        "Do the thing\n\n" + TRIGGER_RUN_NOTE
    )


@pytest.mark.asyncio
async def test_history_cleaner_still_reads_the_task_first():
    """The history readers strip the header (and Changed files) and keep what
    follows; the task must still come first so a label starts with it."""
    store, pid = _project("file_watch")
    mgr, _ = _manager()
    await TriggerManager(store, mgr)._fire_trigger(pid, "trg_aaa", changed_files=["a.md"])

    cleaned = clean_user_text(mgr.start_agent.await_args.kwargs["initial_message"])
    assert cleaned.kind == KIND_AUTOMATION
    assert cleaned.text.startswith("Do the thing")


@pytest.mark.asyncio
async def test_worker_message_is_unchanged():
    """A worker has no trigger tools; its message stays the bare header + task."""
    store, pid = _project("schedule", agent="codex")
    mgr, sub_mgr = _manager()
    await TriggerManager(store, mgr)._fire_trigger(pid, "trg_aaa")

    dispatched = sub_mgr.send.await_args.args[2]
    assert TRIGGER_RUN_NOTE not in dispatched
    assert dispatched.endswith(
        "[Triggered by schedule 'Nightly' (Daily at 7am)]\n\nDo the thing")
