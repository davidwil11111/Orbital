# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""``sync_imports`` (contract §6): one entry point that brings a project up to
date with its folder's native agent history.

Sessions need no persisting step — the session list lists the folder's native
sessions on every read and materializes one on first open — so a sync only
counts them. Codex automations are imported as triggers here, once each.

Called at the end of project creation, lazily on the first session list of a
project per daemon lifetime, and by ``POST /projects/{pid}/imports/sync``.
Never raises into its caller: any source error is logged and reads as zero.
"""

from __future__ import annotations

import logging
import os
import threading

from agent_os.onboarding import codex_automations

logger = logging.getLogger(__name__)


def _zero() -> dict:
    return {"imported": 0, "skipped": 0}


class ImportCoordinator:
    def __init__(self, project_store, agent_manager, *, trigger_manager=None):
        """``trigger_manager`` is a zero-arg callable returning the live
        TriggerManager (or None) — it is created after the agent manager."""
        self._project_store = project_store
        self._agent_manager = agent_manager
        self._trigger_manager = trigger_manager or (lambda: None)
        # One read-modify-write of a project's trigger list at a time, so the
        # lazy sync and an explicit sync racing cannot import an automation
        # twice.
        self._lock = threading.Lock()

    def sync_imports(self, project_id: str) -> dict:
        automations = self.import_automations(project_id)
        return {"sessions": self._count_virtual_sessions(project_id),
                "automations": automations}

    def _count_virtual_sessions(self, project_id: str) -> int:
        try:
            entries = self._agent_manager.list_sessions(project_id)
        except Exception:
            logger.warning("session import: could not list sessions of %s",
                           project_id, exc_info=True)
            return 0
        return sum(
            1 for e in entries
            if e.get("origin") == "imported"
            and not (e.get("imported") or {}).get("materialized", False)
        )

    def import_automations(self, project_id: str) -> dict:
        try:
            return self._import_automations(project_id)
        except Exception:
            logger.warning("session import: automation import failed for %s",
                           project_id, exc_info=True)
            return _zero()

    def _import_automations(self, project_id: str) -> dict:
        project = self._project_store.get_project(project_id)
        if not isinstance(project, dict) or project.get("is_scratch"):
            return _zero()
        workspace = project.get("workspace") or ""
        if not isinstance(workspace, str) or not os.path.isdir(workspace):
            return _zero()
        with self._lock:
            project = self._project_store.get_project(project_id) or project
            triggers = list(project.get("triggers") or [])
            new, skipped = codex_automations.plan_automation_imports(
                workspace, existing_triggers=triggers)
            if not new:
                return {"imported": 0, "skipped": skipped}
            self._project_store.update_project(project_id, {"triggers": triggers + new})
        tm = self._trigger_manager()
        for trigger in new:
            logger.info("session import: Codex automation %s → trigger %s (%s, enabled=%s)",
                        trigger["imported_from"]["codex_automation_id"], trigger["id"],
                        trigger["schedule"]["cron"], trigger["enabled"])
            if tm is not None:
                try:
                    tm.register_trigger(project_id, trigger)
                except Exception:
                    logger.warning("session import: could not register trigger %s",
                                   trigger["id"], exc_info=True)
        return {"imported": len(new), "skipped": skipped}
