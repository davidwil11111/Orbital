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


# Indirection so a test can stand in for Windows' case folding on a Mac.
_normcase = os.path.normcase


def _key(path: str) -> str:
    """Comparison form of a folder: normalised, and case-folded where the
    file system ignores case (Windows: ``C:\\Users\\x`` == ``c:\\users\\x``)."""
    return _normcase(os.path.normpath(path))


_AGENT_MD_NAMES = ("AGENTS.md", "CLAUDE.md")


def has_project_instructions(folder: str) -> bool:
    """A CLAUDE.md, or an AGENTS.md the user wrote, in ``folder`` (spec 138
    pass 1). Orbital seeds AGENTS.md into every project it creates, so with
    the seed counted every folder Orbital was ever pointed at qualified (rc
    DMG: 35 smoke-test workspaces offered as projects)."""
    from agent_os.daemon_v2.agent_md_seeder import SEED_MARKER

    for name in _AGENT_MD_NAMES:
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        if name != "AGENTS.md":
            return True
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                head = fh.read(4096)
        except OSError:
            return True
        if SEED_MARKER not in head:
            return True
    return False


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
    # Where the Codex app puts a chat started without a project:
    # <root>/<YYYY-MM-DD>/<slug>, one folder per chat. The root and its day
    # folders are containers; a chat folder is a project only as a git repo.
    chat_roots: tuple[str, ...] = ()

    @classmethod
    def for_home(cls, home: str | None = None, *,
                 temp_roots: tuple[str, ...] | None = None) -> "HistoryEnv":
        home = _norm(home or history_home())
        if temp_roots is None:
            roots = {"/tmp", "/private/tmp", "/private/var/folders", "/var/folders",
                     os.path.join(home, "Library"),
                     # Windows: app data and caches, the ~/Library of Windows.
                     os.path.join(home, "AppData")}
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
        drive = os.path.splitdrive(home)[0]
        containers = frozenset(_key(p) for p in (
            home, os.path.join(home, "Desktop"), os.path.join(home, "Documents"),
            os.path.join(home, "Downloads"), "/", "/tmp", "/private/tmp",
            *((drive + os.sep,) if drive else ())))
        private = tuple(_key(os.path.join(home, d)) for d in (
            ".claude", ".codex", ".codebuddy", ".pi", ".kimi-code", ".minimax", ".agents"))
        chats = (_key(os.path.join(home, "Documents", "Codex")),)
        return cls(home=home, temp_roots=tuple(_key(r) for r in temp_roots),
                   containers=containers, private_roots=private, chat_roots=chats)

    def is_temp(self, path: str | None) -> bool:
        if not path:
            return False
        p = _key(path)
        return any(p == r or p.startswith(r.rstrip(os.sep) + os.sep) for r in self.temp_roots)

    def is_container(self, path: str | None) -> bool:
        if not path:
            return False
        p = _key(path)
        return p in self.containers or self._chat_depth(p) in (0, 1)

    def is_chat_folder(self, path: str | None) -> bool:
        """Inside a Codex app chat folder (below a chat root's day folders)."""
        return bool(path) and (self._chat_depth(_key(path)) or 0) >= 2

    def _chat_depth(self, p: str) -> int | None:
        """Levels below a chat root (0 = the root itself), or None."""
        for root in self.chat_roots:
            if p == root:
                return 0
            prefix = root.rstrip(os.sep) + os.sep
            if p.startswith(prefix):
                return len([part for part in p[len(prefix):].split(os.sep) if part])
        return None

    def is_private(self, path: str) -> bool:
        p = _key(path)
        return any(p == r or p.startswith(r + os.sep) for r in self.private_roots)
