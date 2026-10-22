# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ACP SDK transport spawns its agent with the Windows no-window flag.

``acp.spawn_agent_process`` has no ``creationflags`` hook, so the transport
carries its own spawn. On Windows a ``.cmd`` launcher (dsh) runs through
``cmd.exe``; without ``CREATE_NO_WINDOW`` that cmd.exe shows a console for
the whole life of the agent (spec 055; seen on the spec 127 V2 run,
2026-10-22).
"""

import asyncio
from unittest.mock import patch

import pytest

from agent_os.agent.transports import acp_sdk_transport as mod
from agent_os.utils.subprocess_flags import win_no_window_flags


class _FakeProc:
    def __init__(self):
        self.stdin = _FakeStdin()
        self.stdout = object()
        self.stderr = object()
        self.returncode = None
        self.pid = 4242
        self.killed = False

    async def wait(self):
        self.returncode = 0
        return 0

    def kill(self):
        self.killed = True

    def terminate(self):
        self.killed = True


class _FakeStdin:
    def write_eof(self):
        pass

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass


@pytest.mark.asyncio
async def test_spawn_passes_no_window_creation_flags_and_wires_the_connection():
    captured = {}
    proc = _FakeProc()

    async def fake_exec(*argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return proc

    class FakeConn:
        def __init__(self, to_client, writer, reader, **kw):
            captured["conn"] = (to_client, writer, reader, kw)
            self.closed = False

        async def close(self):
            self.closed = True

    with patch.object(asyncio, "create_subprocess_exec", fake_exec), \
         patch.object(mod, "ClientSideConnection", FakeConn), \
         patch.object(mod, "default_environment", lambda: {"BASE": "1"}):
        async with mod._spawn_agent_process_no_window(
            "client", r"C:\x\dsh-acp-demo.cmd", "--config", "c.yml",
            cwd=r"D:\ws", env={"DEEPSEEK_API_KEY": "k"},
            transport_kwargs={"shutdown_timeout": 0.01},
            use_unstable_protocol=True,
        ) as (conn, process):
            assert process is proc
            assert isinstance(conn, FakeConn)

    assert captured["argv"] == (r"C:\x\dsh-acp-demo.cmd", "--config", "c.yml")
    kw = captured["kwargs"]
    assert kw["creationflags"] == win_no_window_flags()
    assert kw["cwd"] == r"D:\ws"
    assert kw["env"] == {"BASE": "1", "DEEPSEEK_API_KEY": "k"}
    assert kw["stdin"] == asyncio.subprocess.PIPE
    assert kw["stdout"] == asyncio.subprocess.PIPE
    to_client, writer, reader, conn_kw = captured["conn"]
    assert (to_client, writer, reader) == ("client", proc.stdin, proc.stdout)
    assert conn_kw == {"use_unstable_protocol": True}
    assert conn.closed is True


def test_transport_start_uses_the_no_window_spawn():
    """``start()`` must not fall back to the SDK's bare spawn."""
    import inspect
    src = inspect.getsource(mod.ACPSDKTransport.start)
    assert "_spawn_agent_process_no_window(" in src
    assert "spawn_agent_process(\n" not in src
