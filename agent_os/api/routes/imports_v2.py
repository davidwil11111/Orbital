# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""``POST /api/v2/projects/{pid}/imports/sync`` (session import, contract §6).

Brings a project up to date with its folder's Claude Code / Codex history:
counts the native sessions not yet opened in Orbital and imports the folder's
Codex automations as triggers (once each). The same sync runs automatically at
project creation and on a project's first session list per daemon lifetime;
this route is for smokes and a future "re-scan" button.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v2")

_project_store = None
_agent_manager = None


def configure(project_store, agent_manager) -> None:
    global _project_store, _agent_manager
    _project_store = project_store
    _agent_manager = agent_manager


@router.post("/projects/{project_id}/imports/sync")
async def sync_imports(project_id: str) -> dict:
    if _project_store is None or _project_store.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    # Blocking reads (sqlite index, transcript heads, the session list).
    return await asyncio.to_thread(_agent_manager.sync_imports, project_id)
