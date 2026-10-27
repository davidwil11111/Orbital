# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Every history reader ships in the packaged app (spec 138).

``agent_os.onboarding.sources`` loads its readers by name, which PyInstaller's
import analysis cannot see. The rc DMG (2026-10-27) bundled only the readers
some other module imports (pi, kimi_code, minimax_code): the scan read 20
sessions instead of 323 and offered no groups, while every source run was
fine. Text-level on the specs, like the default-skills spec check.
"""

import glob
import logging
import os

import pytest

from agent_os.onboarding import sources

SPEC_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "agent_os", "desktop")
SOURCES_DIR = os.path.dirname(sources.__file__)


def _spec_files() -> list[str]:
    return sorted(glob.glob(os.path.join(SPEC_DIR, "*.spec")))


def test_both_platform_specs_are_checked():
    names = {os.path.basename(p) for p in _spec_files()}
    assert {"agentos.spec", "agentos-macos.spec"} <= names


@pytest.mark.parametrize("spec_path", _spec_files())
def test_spec_collects_every_history_reader(spec_path):
    with open(spec_path, "r", encoding="utf-8") as f:
        text = f.read()
    assert "collect_submodules('agent_os.onboarding.sources')" in text, (
        f"{os.path.basename(spec_path)} must add "
        "collect_submodules('agent_os.onboarding.sources') to hiddenimports: "
        "the readers are imported by name and PyInstaller drops them otherwise")


def test_the_loader_names_every_reader_module():
    on_disk = {os.path.splitext(n)[0] for n in os.listdir(SOURCES_DIR)
               if n.endswith(".py") and not n.startswith("_")}
    assert on_disk == set(sources.SOURCE_MODULES)


def test_every_reader_loads():
    agents = [s.agent for s in sources.load_sources()]
    assert agents == list(sources.SOURCE_MODULES.values())


def test_a_missing_reader_is_logged(monkeypatch, caplog):
    monkeypatch.setitem(sources.SOURCE_MODULES, "no_such_reader", "nobody")
    with caplog.at_level(logging.WARNING, logger=sources.__name__):
        loaded = sources.load_sources()
    assert "nobody" not in [s.agent for s in loaded]
    assert any("no_such_reader" in r.getMessage() for r in caplog.records)
