# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Where the user's agent history lives, and which folders are never a
project (spec 138 "Grouping" pass 1).

``ORBITAL_HISTORY_HOME`` (default ``~``) moves every history reader and the
importable-projects scanner onto a staged home, so tests and the daemon smoke
never read the real ``~/.claude`` / ``~/.codex``. ``ORBITAL_CODEX_HOME`` still
points only the Codex readers at a staging dir, as before.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field


def history_home() -> str:
    return os.environ.get("ORBITAL_HISTORY_HOME") or os.path.expanduser("~")


def claude_dir(home: str | None = None) -> str:
    """``$CLAUDE_CONFIG_DIR``, else ``~/.claude`` under ``home``. An explicit
    ``home`` wins over the variable; the ``ORBITAL_HISTORY_HOME`` override
    does not (the session list has always honoured ``CLAUDE_CONFIG_DIR``,
    and a user who set it keeps their history there)."""
    if home is None:
        env = os.environ.get("CLAUDE_CONFIG_DIR")
        if env:
            return env
    return os.path.join(home or history_home(), ".claude")


def codex_dir(home: str | None = None) -> str:
    """``$ORBITAL_CODEX_HOME`` > ``$CODEX_HOME`` > ``~/.codex`` under ``home``
    (an explicit ``home`` wins over ``CODEX_HOME``)."""
    env = os.environ.get("ORBITAL_CODEX_HOME")
    if env:
        return env
    if home is None:
        env = os.environ.get("CODEX_HOME")
        if env:
            return env
    return os.path.join(home or history_home(), ".codex")


def _norm(path: str) -> str:
    return os.path.normpath(path)


@dataclass(frozen=True)
class HistoryEnv:
    """The folders the grouping rules treat specially, resolved for one home.

    Tests build one with their own ``temp_roots`` because pytest's tmp_path
    is itself under a temp root."""

    home: str
    temp_roots: tuple[str, ...]
    containers: frozenset[str] = field(default_factory=frozenset)
    # Written files under these never link two sessions (pass 3).
    private_roots: tuple[str, ...] = ()

    @classmethod
    def for_home(cls, home: str | None = None, *,
                 temp_roots: tuple[str, ...] | None = None) -> "HistoryEnv":
        home = _norm(home or history_home())
        if temp_roots is None:
            roots = {"/tmp", "/private/tmp", "/private/var/folders", "/var/folders",
                     os.path.join(home, "Library")}
            try:
                roots.add(os.path.realpath(tempfile.gettempdir()))
            except OSError:
                pass
            # A home staged inside a temp dir (tests, smokes) was put there on
            # purpose: the roots holding it do not make everything temp.
            real_home = os.path.realpath(home)
            roots = {r for r in roots
                     if not (home + os.sep).startswith(r.rstrip(os.sep) + os.sep)
                     and not (real_home + os.sep).startswith(r.rstrip(os.sep) + os.sep)}
            temp_roots = tuple(sorted(roots))
        containers = frozenset(_norm(p) for p in (
            home, os.path.join(home, "Desktop"), os.path.join(home, "Documents"),
            os.path.join(home, "Downloads"), "/", "/tmp", "/private/tmp"))
        private = tuple(os.path.join(home, d) for d in (".claude", ".codex", ".codebuddy"))
        return cls(home=home, temp_roots=tuple(_norm(r) for r in temp_roots),
                   containers=containers, private_roots=private)

    def is_temp(self, path: str | None) -> bool:
        if not path:
            return False
        p = _norm(path)
        return any(p == r or p.startswith(r.rstrip(os.sep) + os.sep) for r in self.temp_roots)

    def is_container(self, path: str | None) -> bool:
        return bool(path) and _norm(path) in self.containers

    def is_private(self, path: str) -> bool:
        p = _norm(path)
        return any(p == r or p.startswith(r + os.sep) for r in self.private_roots)
