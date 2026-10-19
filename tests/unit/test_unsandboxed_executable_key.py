# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The card and the parked runs carry the backend's allowlist key (issue 7).

The UI used to label "Always allow" with the command's first word, which on
Windows was usually ``cmd``. The daemon now sends ``executable`` — the program
"always" would record, or ``""`` when the command cannot be allowlisted — and
the UI hides the button for ``""``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from agent_os.api.routes.agents_v2 import _redact_project
from agent_os.agent.prompt_builder import Autonomy
from agent_os.daemon_v2.autonomy import AutonomyInterceptor


def _card(command):
    ws = MagicMock()
    interceptor = AutonomyInterceptor(Autonomy.CHECK_IN, ws, "proj_x", session_id="s1")
    tc = {"id": "call_1", "name": "shell", "arguments": {"command": command}}
    pending_id = interceptor.on_sandbox_denial(tc, {"command": command, "reason": "r"}, [])
    return interceptor.get_pending(pending_id), ws.broadcast.call_args[0][1]


def test_card_names_the_program_inside_a_cmd_wrapper():
    pending, payload = _card("cmd /c type private\\note.txt")
    assert pending["executable"] == payload["executable"] == "type"


def test_card_offers_no_key_for_a_chained_command():
    pending, payload = _card("git status; Remove-Item x")
    assert pending["executable"] == payload["executable"] == ""


def test_parked_runs_carry_the_key_even_when_stored_without_it():
    project = {
        "project_id": "proj_x", "name": "x", "workspace": "C:\\ws",
        "pending_unsandboxed_runs": [
            {"id": "ur_1", "command": "cmd /c curl.exe -sS https://x"},
            {"id": "ur_2", "command": "git log | Out-File x"},
        ],
    }
    wire = _redact_project(project)
    keys = [r["executable"] for r in wire["pending_unsandboxed_runs"]]
    assert keys == ["curl.exe", ""]
