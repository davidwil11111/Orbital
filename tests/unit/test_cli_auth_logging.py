# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 116 D7: the auth probe logs every False, with enough to act on.

``_check_cli_auth`` returned False on a non-zero exit, a timeout, an OSError
or unparseable JSON without a single log line, so an old CLI that does not
know ``auth status`` (exit 1) read as "not logged in" and the daemon log
could not say why (the 2026-10-18 Windows report). One WARNING per False,
naming the slug and the exit code / exception class; a passing probe logs
nothing at WARNING. The structured outcome (spec 113) is kept on the engine
for the dispatch gate's telemetry.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from unittest.mock import patch

import pytest

from agent_os.agents.manifest import (
    AgentManifest,
    ManifestCredential,
    ManifestRuntime,
    ManifestSetup,
)
from agent_os.agents.registry import AgentRegistry
from agent_os.agents.setup_engine import SetupEngine

LOGGER = "agent_os.agents.setup_engine"


def _engine(check_command: str = "probe-agent auth status --json"):
    manifest = AgentManifest(
        manifest_version="1", name="Probe", slug="probe-agent", description="",
        author="t", version="1",
        runtime=ManifestRuntime(adapter="cli", command="probe-agent"),
        setup=ManifestSetup(credentials=[ManifestCredential(
            key="probe_auth", label="Probe", type="oauth_cli", required=True,
            check_command=check_command, check_field="loggedIn", check_value="true",
        )]),
    )
    registry = AgentRegistry()
    registry.register(manifest)
    return SetupEngine(registry=registry), manifest


def _py(tmp_path, body: str) -> str:
    """A shell command running ``body`` as a script file — no shell quoting
    of JSON braces/quotes to get wrong on either platform."""
    script = tmp_path / "probe.py"
    script.write_text(body, encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


def _warnings(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING]


def test_nonzero_exit_logs_one_warning_with_the_exit_code(caplog, tmp_path):
    engine, manifest = _engine(_py(
        tmp_path, "import sys\nsys.stderr.write('unknown command auth')\nsys.exit(1)\n"))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        ok, missing = engine.check_credentials(manifest)
    assert ok is False and missing == ["probe_auth"]
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "probe-agent" in message
    assert "exit code 1" in message
    assert "unknown command auth" in message
    probe = engine.last_auth_probe("probe-agent")
    assert probe["exit_code"] == 1
    assert probe["error_class"] is None
    assert probe["stderr_category"] == "unknown_command"
    assert isinstance(probe["duration_ms"], int)


def test_invalid_json_logs_the_exception_class(caplog, tmp_path):
    engine, manifest = _engine(_py(tmp_path, "print('not json at all')\n"))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        ok, _ = engine.check_credentials(manifest)
    assert ok is False
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "JSONDecodeError" in warnings[0].getMessage()
    assert engine.last_auth_probe("probe-agent")["error_class"] == "JSONDecodeError"
    assert engine.last_auth_probe("probe-agent")["exit_code"] == 0


def test_timeout_logs_the_exception_class(caplog):
    engine, manifest = _engine("probe-agent auth status --json")
    with patch("agent_os.agents.setup_engine.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="x", timeout=10)), \
            caplog.at_level(logging.WARNING, logger=LOGGER):
        ok, _ = engine.check_credentials(manifest)
    # Spec 136 decision 2: a timeout is "no answer", not "signed out" — with
    # no earlier verdict to keep it is unknown, which is not missing. It is
    # still logged and recorded like every other failed probe.
    assert ok is True
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "TimeoutExpired" in warnings[0].getMessage()
    probe = engine.last_auth_probe("probe-agent")
    assert probe["error_class"] == "TimeoutExpired"
    assert probe["stderr_category"] == "timeout"
    assert probe["exit_code"] is None


def test_logged_out_json_logs_the_field_value(caplog, tmp_path):
    engine, manifest = _engine(_py(tmp_path, "print('{\"loggedIn\": false}')\n"))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        ok, _ = engine.check_credentials(manifest)
    assert ok is False
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "loggedIn" in warnings[0].getMessage()
    assert engine.last_auth_probe("probe-agent")["stderr_category"] == "not_logged_in"


def test_passing_probe_logs_nothing_at_warning(caplog, tmp_path):
    engine, manifest = _engine(_py(tmp_path, "print('{\"loggedIn\": true}')\n"))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        ok, missing = engine.check_credentials(manifest)
    assert ok is True and missing == []
    assert _warnings(caplog) == []
    assert engine.last_auth_probe("probe-agent")["exit_code"] == 0


def test_no_probe_yet_reads_as_none():
    engine, _ = _engine()
    assert engine.last_auth_probe("probe-agent") is None
