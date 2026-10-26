# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Import writes nothing until first use (spec 138).

A project created by the batch import (``POST /onboarding/import``) is marked
``deferred_seed: true`` and gets neither the workspace-root ``AGENTS.md`` nor
the ``orbital/`` folder (default skills) at creation: the folder is the
user's real repo and nothing should appear in it until they work there.
:func:`ensure_project_seeded` does both on first use — the first session
opened (materialised) or the first message sent — exactly once.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

DEFERRED_SEED_FIELD = "deferred_seed"

_lock = threading.Lock()


def is_deferred(project: dict | None) -> bool:
    return isinstance(project, dict) and bool(project.get(DEFERRED_SEED_FIELD))


def ensure_project_seeded(project_store, project_id: str) -> bool:
    """Seed a deferred project now. Returns True when this call seeded it,
    False when there was nothing to do. Idempotent; never raises."""
    try:
        if not is_deferred(project_store.get_project(project_id)):
            return False
        with _lock:
            if not is_deferred(project_store.get_project(project_id)):
                return False
            from agent_os.daemon_v2.agent_md_seeder import seed_project_agent_md
            from agent_os.daemon_v2.default_skills_installer import install_default_skills
            try:
                install_default_skills(project_store, project_id)
            except Exception:
                logger.error("deferred seed: default skills failed for %s", project_id,
                             exc_info=True)
            try:
                seed_project_agent_md(project_store, project_id)
            except Exception:
                logger.error("deferred seed: AGENTS.md failed for %s", project_id,
                             exc_info=True)
            project_store.update_project(project_id, {DEFERRED_SEED_FIELD: False})
            logger.info("deferred seed: seeded %s on first use", project_id)
            return True
    except Exception:
        logger.warning("deferred seed: could not seed %s", project_id, exc_info=True)
        return False
