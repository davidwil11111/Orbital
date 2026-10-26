# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Per-agent history readers (spec 138). Each module exposes ``SOURCE``, a
:class:`agent_os.onboarding.history_types.HistorySource`.

Modules are loaded by name and a missing one is skipped, so a reader can land
on its own without touching the scan.
"""

from __future__ import annotations

import importlib
import logging

logger = logging.getLogger(__name__)

# Module name -> sub-agent slug, in display order.
SOURCE_MODULES = {
    "claude_code": "claude-code",
    "codex": "codex",
    "pi": "pi",
    "kimi_code": "kimi-code",
    "minimax_code": "minimax-code",
}


def load_sources() -> list:
    """Every reader that imports cleanly, in ``SOURCE_MODULES`` order."""
    sources = []
    for module in SOURCE_MODULES:
        try:
            mod = importlib.import_module(f"{__name__}.{module}")
        except ModuleNotFoundError as exc:
            if exc.name == f"{__name__}.{module}":
                continue  # not built yet
            logger.exception("history source %s failed to import", module)
            continue
        except Exception:
            logger.exception("history source %s failed to import", module)
            continue
        source = getattr(mod, "SOURCE", None)
        if source is not None:
            sources.append(source)
    return sources
