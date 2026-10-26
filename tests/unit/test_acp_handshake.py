# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ACP handshake is the readiness verdict: real processes, fake agents."""

from __future__ import annotations

import sys
import textwrap

from agent_os.agents.handshake import acp_handshake


def _agent(tmp_path, body: str) -> list[str]:
    script = tmp_path / "agent.py"
    script.write_text(textwrap.dedent(body))
    return [sys.executable, str(script)]


def test_ok_reports_the_agent_info(tmp_path):
    argv = _agent(tmp_path, """
        import json, sys
        req = json.loads(sys.stdin.readline())
        print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": {
            "protocolVersion": 1,
            "agentInfo": {"name": "fake-acp", "version": "9.9.9"}}}), flush=True)
        sys.stdin.read()
    """)
    result = acp_handshake(argv, timeout=10)
    assert result["state"] == "ok"
    assert (result["agent_name"], result["agent_version"]) == ("fake-acp", "9.9.9")


def test_banner_lines_before_the_answer_are_skipped(tmp_path):
    argv = _agent(tmp_path, """
        import json, sys
        print("Welcome to fake agent", flush=True)
        print(json.dumps({"jsonrpc": "2.0", "method": "notice"}), flush=True)
        req = json.loads(sys.stdin.readline())
        print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": {}}), flush=True)
    """)
    assert acp_handshake(argv, timeout=10)["state"] == "ok"


def test_exit_before_answering_is_failed_with_its_own_words(tmp_path):
    # What dsh 0.2 does with the rc.6 launch contract.
    argv = _agent(tmp_path, """
        import sys
        sys.stderr.write("error: unknown option '--config'\\n")
        sys.exit(1)
    """)
    result = acp_handshake(argv, timeout=10)
    assert result["state"] == "failed"
    assert "unknown option '--config'" in result["detail"]


def test_error_response_is_failed(tmp_path):
    argv = _agent(tmp_path, """
        import json, sys
        req = json.loads(sys.stdin.readline())
        print(json.dumps({"jsonrpc": "2.0", "id": req["id"],
                          "error": {"code": -32600, "message": "unsupported protocol"}}),
              flush=True)
    """)
    result = acp_handshake(argv, timeout=10)
    assert result["state"] == "failed"
    assert "unsupported protocol" in result["detail"]


def test_silence_is_unknown_not_failed(tmp_path):
    argv = _agent(tmp_path, """
        import time
        time.sleep(30)
    """)
    result = acp_handshake(argv, timeout=1)
    assert result["state"] == "unknown"


def test_missing_binary_is_failed(tmp_path):
    result = acp_handshake([str(tmp_path / "nope")], timeout=5)
    assert result["state"] == "failed"
    assert "Could not start" in result["detail"]


def test_env_reaches_the_agent(tmp_path):
    argv = _agent(tmp_path, """
        import json, os, sys
        req = json.loads(sys.stdin.readline())
        print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": {
            "agentInfo": {"name": os.environ.get("PROBE_MARK", "")}}}), flush=True)
    """)
    result = acp_handshake(argv, env={"PROBE_MARK": "seen"}, timeout=10)
    assert result["agent_name"] == "seen"
