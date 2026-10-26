# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 134 A5 (W8) — ``projects.json`` survives being killed mid-write.

``_save`` truncated the file and rewrote it in place, and ``_load`` on the
boot path was unguarded, so ending a frozen app during a flush made every
later boot fail. Now: tmp + fsync + ``os.replace``; a ``.bak`` after each
good load; a corrupt file is moved aside and restored from the backup; with
no usable backup the store refuses (the desktop app shows it in a window)
rather than ever booting with an empty project list.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest

from agent_os.daemon_v2.project_store import ProjectStore, ProjectStoreCorrupt


def _seed(tmp_path, projects=None):
    projects = projects or {"proj_a": {"project_id": "proj_a", "name": "A",
                                       "agent_name": "A", "workspace": "/w/a"}}
    (tmp_path / "projects.json").write_text(json.dumps(projects), encoding="utf-8")
    return projects


def test_a_good_load_writes_the_backup(tmp_path):
    projects = _seed(tmp_path)
    ProjectStore(data_dir=str(tmp_path))
    assert json.loads((tmp_path / "projects.json.bak").read_text(encoding="utf-8")) == projects


def test_a_crash_mid_save_leaves_the_old_file_intact(tmp_path):
    _seed(tmp_path)
    store = ProjectStore(data_dir=str(tmp_path))
    before = (tmp_path / "projects.json").read_bytes()

    def boom(*args, **kwargs):
        raise OSError("killed mid-write")

    with patch("agent_os.daemon_v2.project_store.json.dump", side_effect=boom), \
         pytest.raises(OSError):
        store.create_project({"name": "B", "agent_name": "B", "workspace": str(tmp_path / "b")})
    assert (tmp_path / "projects.json").read_bytes() == before
    assert not list(tmp_path.glob("projects.json.*.tmp"))


def test_a_truncated_file_is_restored_from_the_backup(tmp_path, caplog):
    projects = _seed(tmp_path)
    ProjectStore(data_dir=str(tmp_path))  # good load → .bak
    (tmp_path / "projects.json").write_text('{"proj_a": {"name": "A", "wor', encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger="agent_os.daemon_v2.project_store"):
        store = ProjectStore(data_dir=str(tmp_path))

    assert store.restored_from_backup is True
    assert [p["name"] for p in store.list_projects()] == ["A"]
    assert json.loads((tmp_path / "projects.json").read_text(encoding="utf-8")) == projects
    aside = list(tmp_path.glob("projects.json.corrupt-*"))
    assert len(aside) == 1 and aside[0].read_text(encoding="utf-8").startswith('{"proj_a"')
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_an_empty_file_counts_as_corrupt(tmp_path):
    _seed(tmp_path)
    ProjectStore(data_dir=str(tmp_path))
    (tmp_path / "projects.json").write_bytes(b"")
    store = ProjectStore(data_dir=str(tmp_path))
    assert store.restored_from_backup is True and len(store.list_projects()) == 1


def test_no_backup_refuses_and_never_boots_empty(tmp_path):
    (tmp_path / "projects.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ProjectStoreCorrupt) as exc:
        ProjectStore(data_dir=str(tmp_path))
    assert "projects.json" in str(exc.value)
    # Left exactly where it was, for support to look at; nothing replaced it.
    assert (tmp_path / "projects.json").read_text(encoding="utf-8") == "{not json"
    assert not list(tmp_path.glob("projects.json.corrupt-*"))


def test_a_corrupt_backup_also_refuses(tmp_path):
    (tmp_path / "projects.json").write_text("[1,", encoding="utf-8")
    (tmp_path / "projects.json.bak").write_text("also bad", encoding="utf-8")
    with pytest.raises(ProjectStoreCorrupt):
        ProjectStore(data_dir=str(tmp_path))


def test_a_fresh_install_still_starts_empty(tmp_path):
    store = ProjectStore(data_dir=str(tmp_path))
    assert store.list_projects() == [] and store.restored_from_backup is False


def test_create_app_refuses_and_gives_the_pid_file_back(tmp_path):
    from agent_os.api.app import create_app

    data = tmp_path / "data"
    data.mkdir()
    (data / "projects.json").write_text('{"proj_a": {', encoding="utf-8")
    with patch("agent_os.api.app.release_pid_file") as released, \
         pytest.raises(ProjectStoreCorrupt):
        create_app(data_dir=str(data))
    released.assert_called_once()
