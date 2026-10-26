# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Is the binary we found actually the agent we can talk to?

A file on disk, even one that prints a version, says nothing about whether
Orbital's transport can drive it: the vendor may have changed the launch
contract (dsh 0.1 → 0.2 replaced ``dsh-acp-demo --config`` with
``dsh --profile acp``) or the wrapper may be broken. The answer that counts is
the protocol's own opening exchange, so that is what this runs — ACP
``initialize`` over stdio, nothing that creates a session or spends tokens.

Measured 2026-10-26 on the dev Mac: kimi 0.7 s, mcode 2.7 s, codebuddy 0.9 s,
cursor 0.5 s, dsh (desktop bundle) 0.7 s — none needs a login to answer.

Three answers, and only one of them blocks:

* ``ok``      — the agent answered ``initialize``.
* ``failed``  — it exited, wrote an error, or answered with an error. The
  agent is found but cannot be driven; the detail is its own words.
* ``unknown`` — no answer within the budget (a cold npm start, a slow disk).
  Never treated as broken: a timeout must not hide a working agent.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time

from agent_os.utils.subprocess_flags import win_no_window_flags

logger = logging.getLogger(__name__)

HANDSHAKE_TIMEOUT_S = 20.0
_DETAIL_CHARS = 400


def _tail(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= _DETAIL_CHARS:
        return text
    return "…" + text[-_DETAIL_CHARS:]


def acp_handshake(argv: list[str], *, env: dict[str, str] | None = None,
                  cwd: str | None = None,
                  timeout: float = HANDSHAKE_TIMEOUT_S,
                  session_cwd: str | None = None,
                  session_settle_s: float = 0.0) -> dict:
    """Spawn ``argv``, send ACP ``initialize``, report what happened.

    Returns ``{"state": "ok"|"failed"|"unknown", "detail": str|None,
    "agent_name": str|None, "agent_version": str|None}``. Never raises.

    ``session_cwd`` also opens a session there (``session/new``) and adds the
    agent's advertised ``config_options`` to the result — what it offers for
    model and reasoning, in its own words. Opening a session writes the
    agent's session store, so callers point it at a scratch directory.
    ``session_settle_s`` waits that long and opens a second session, whose
    options are the ones reported: dsh registers the user's provider routes
    asynchronously after boot, so a session opened at once may list only
    its default model.
    """
    result = {"state": "unknown", "detail": None,
              "agent_name": None, "agent_version": None}
    spawn_env = dict(os.environ)
    if env:
        spawn_env.update(env)
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=cwd or None, env=spawn_env,
            creationflags=win_no_window_flags(),
        )
    except (OSError, ValueError) as exc:
        result.update(state="failed", detail=f"Could not start {argv[0]}: {exc}")
        return result

    lines: "queue.Queue[bytes | None]" = queue.Queue()
    stderr_chunks: list[bytes] = []

    def _read_stdout() -> None:
        try:
            for line in iter(proc.stdout.readline, b""):
                lines.put(line)
        except (OSError, ValueError):
            pass
        lines.put(None)

    def _read_stderr() -> None:
        try:
            for chunk in iter(lambda: proc.stderr.read(4096), b""):
                stderr_chunks.append(chunk)
        except (OSError, ValueError):
            pass

    threading.Thread(target=_read_stdout, daemon=True).start()
    threading.Thread(target=_read_stderr, daemon=True).start()

    def _send(request: dict) -> None:
        try:
            proc.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
            proc.stdin.flush()
        except (OSError, ValueError):
            pass  # it already died; the stdout EOF below says so

    deadline = time.monotonic() + timeout

    def _await(request_id: int, what: str) -> dict | None:
        """The response to ``request_id``, or None with ``result`` updated."""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result["detail"] = f"No answer to ACP {what} within {timeout:.0f} s."
                return None
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                try:
                    code = proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    code = None
                stderr = b"".join(stderr_chunks).decode("utf-8", "replace")
                result.update(state="failed", detail=_tail(stderr) or (
                    f"{os.path.basename(argv[0])} exited"
                    + (f" with code {code}" if code is not None else "")
                    + " before answering."))
                return None
            try:
                message = json.loads(line)
            except ValueError:
                continue  # a banner line; ACP frames are one JSON object per line
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            if "error" in message:
                error = message.get("error") or {}
                result.update(state="failed", detail=_tail(
                    str(error.get("message") or error)))
                return None
            return message.get("result") or {}

    try:
        _send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}},
        }})
        initialized = _await(1, "initialize")
        if initialized is not None:
            info = initialized.get("agentInfo") or {}
            result.update(state="ok", agent_name=info.get("name"),
                          agent_version=info.get("version"))
            if session_cwd:
                _send({"jsonrpc": "2.0", "id": 2, "method": "session/new",
                       "params": {"cwd": session_cwd, "mcpServers": []}})
                session = _await(2, "session/new")
                if session is not None and session_settle_s > 0:
                    time.sleep(session_settle_s)
                    _send({"jsonrpc": "2.0", "id": 3, "method": "session/new",
                           "params": {"cwd": session_cwd, "mcpServers": []}})
                    session = _await(3, "session/new")
                if session is not None:
                    result["config_options"] = list(session.get("configOptions") or [])
    finally:
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                logger.warning("handshake process %s did not exit", argv[0])
    return result
