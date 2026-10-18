# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""C2: PermissionManager — Windows ACL management via icacls."""

import logging
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from agent_os.platform.types import (
    AccessInfo,
    FolderInfo,
    PermissionResult,
    WORKSPACE_AGENT_DIR,
    windows_protected_control_files,
    windows_toolchain_roots,
    windows_worker_home,
)
from agent_os.utils.subprocess_flags import win_no_window_flags

logger = logging.getLogger("agent_os.platform.windows.permissions")

#: Spec 114 D1 — the rights a W3 deny names, i.e. generic-write minus
#: ``SYNCHRONIZE``: write data / add file, append data / add subdirectory,
#: write extended attributes, write attributes. The writer and the classifier
#: both render from this tuple so they cannot drift.
W3_DENY_RIGHTS: tuple[str, ...] = ("WD", "AD", "WEA", "WA")

#: Classification of what an ``icacls`` listing already holds for the worker.
DenyState = Literal["none", "current", "stale"]

_ACE_GROUP_RE = re.compile(r"\(([^()]*)\)")


def w3_deny_rights_string() -> str:
    """``(WD,AD,WEA,WA)`` — the rights group icacls takes and prints."""
    return "(" + ",".join(W3_DENY_RIGHTS) + ")"


class PermissionManager:
    """Manages file-system permissions for the sandbox user via icacls."""

    def __init__(self, icacls_timeout: float | None = None) -> None:
        # Spec 109 D7: a per-call bound on icacls, in seconds. ``None`` (the
        # daemon and the Folder-access UI) means no bound — a user may
        # legitimately grant a huge folder behind a visible spinner. The
        # installer paths (``SetupOrchestrator.run_setup/run_teardown``) set
        # it for their duration so an unknown slow DACL (network profile,
        # antivirus hooking icacls) can never wedge Inno's progress bar.
        self.icacls_timeout: float | None = icacls_timeout

    # Standard user folders returned by get_available_folders()
    _STANDARD_FOLDERS = [
        "Desktop",
        "Documents",
        "Downloads",
        "Pictures",
        "Videos",
        "Music",
    ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def grant_access(
        self,
        username: str,
        path: str,
        mode: Literal["read_only", "read_write"],
    ) -> PermissionResult:
        """Grant *username* access to *path* with the specified *mode*."""
        resolved = self._resolve_path(path)
        if resolved is None:
            return PermissionResult(success=False, path=path, error="Path does not exist")

        if mode == "read_only":
            perm = f"{username}:(OI)(CI)R"
        else:
            perm = f"{username}:(OI)(CI)F"

        result = self._run_icacls([resolved, "/grant", perm, "/T", "/Q"])
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            logger.error("icacls grant failed for %s on %s: %s", username, resolved, err)
            return PermissionResult(success=False, path=resolved, error=err)

        logger.info("Granted %s access (%s) to %s", username, mode, resolved)
        return PermissionResult(success=True, path=resolved)

    def revoke_access(self, username: str, path: str) -> PermissionResult:
        """Revoke all access for *username* on *path*.

        Root-only, deliberately no ``/T`` (spec 109 D1): removing the
        inheritable ACE from the root removes the inherited copies through
        the same NTFS propagation that put them there. With ``/T`` this was
        a per-file walk over every toolchain root — the uninstall hang in
        issue #55. Explicit per-file entries written by pre-109 installs
        (which used ``/T`` on the grant) are not touched; once the account is
        deleted they are unresolvable-SID clutter that Windows ignores.
        """
        resolved = self._resolve_path(path)
        if resolved is None:
            return PermissionResult(success=False, path=path, error="Path does not exist")

        result = self._run_icacls([resolved, "/remove", username, "/Q"])
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            logger.error("icacls revoke failed for %s on %s: %s", username, resolved, err)
            return PermissionResult(success=False, path=resolved, error=err)

        logger.info("Revoked access for %s on %s", username, resolved)
        return PermissionResult(success=True, path=resolved)

    # ------------------------------------------------------------------
    # Spec 077 W1/W2/W3 — toolchain grants, worker home, deny ACEs
    # ------------------------------------------------------------------

    def deny_access(self, username: str, path: str) -> PermissionResult:
        """Add a deny-write ACE for *username* on *path* (spec 077 W3, 114 D1).

        Deny ACEs precede allows in Windows access evaluation, so the
        read-write grant on the enclosing workspace cannot override this. The
        inheritance flags ``(OI)(CI)`` are only valid on a directory, so a file
        target (``.git\\config``) gets the rights group alone.

        The rights are the four write rights **by name** —
        :data:`W3_DENY_RIGHTS`, rendered ``(WD,AD,WEA,WA)`` — never the ``W``
        shorthand. ``W`` expands to the generic-write mask, which carries
        ``SYNCHRONIZE`` next to the write bits, and Windows evaluates a deny
        bit by bit: every synchronous ``CreateFile`` (every normal open,
        read-only included) asks for ``SYNCHRONIZE``, so a ``W`` deny failed
        *reads* of ``.git\\config`` and with them every git command the worker
        ran (issue #58). .NET strips ``Synchronize`` from deny rules for the
        same reason.

        What stays blocked for the worker: writing or creating files under
        ``.git\\hooks`` and rewriting ``.git\\config`` — ``git remote add``,
        ``git config --local``, hook edits. What works: reads, and every git
        command that leaves those two alone (``status``, ``log``, ``diff``,
        ``add``, ``commit``, ``checkout``). Delete was never part of ``W`` and
        is not added.
        """
        resolved = self._resolve_path(path)
        if resolved is None:
            return PermissionResult(success=False, path=path, error="Path does not exist")

        rights = w3_deny_rights_string()
        flags = f"(OI)(CI){rights}" if os.path.isdir(resolved) else rights
        args = [resolved, "/deny", f"{username}:{flags}", "/Q"]
        if os.path.isdir(resolved):
            args.insert(-1, "/T")

        result = self._run_icacls(args)
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            logger.error("icacls deny failed for %s on %s: %s", username, resolved, err)
            return PermissionResult(success=False, path=resolved, error=err)

        logger.info("Denied write for %s on %s", username, resolved)
        return PermissionResult(success=True, path=resolved)

    def remove_deny(self, username: str, path: str) -> PermissionResult:
        """Drop the deny ACE added by :meth:`deny_access`.

        Teardown must call this: a deny ACE left on the user's own repository
        after uninstall outlives the account it names (spec 077 §8).
        """
        resolved = self._resolve_path(path)
        if resolved is None:
            return PermissionResult(success=False, path=path, error="Path does not exist")

        args = [resolved, "/remove:d", username, "/Q"]
        if os.path.isdir(resolved):
            args.insert(-1, "/T")

        result = self._run_icacls(args)
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip()
            logger.warning("icacls remove:d failed for %s on %s: %s", username, resolved, err)
            return PermissionResult(success=False, path=resolved, error=err)
        return PermissionResult(success=True, path=resolved)

    def deny_state(self, username: str, path: str) -> DenyState:
        """What *path* already carries for *username* (spec 114 D2).

        ``"current"`` — the D1 rights group; ``"stale"`` — any other deny for
        the account (in practice the v0.16.0 ``(W)`` shape, which must be
        rewritten); ``"none"`` — no deny, or the query failed.
        """
        resolved = self._resolve_path(path)
        if resolved is None:
            return "none"
        result = self._run_icacls([resolved])
        if result.returncode != 0:
            return "none"
        return _classify_deny_ace(result.stdout, username)

    def has_deny(self, username: str, path: str) -> bool:
        """True when *username* carries the **current** W3 deny on *path*.

        A stale deny (the pre-114 ``W`` shape) reads as "not protected yet":
        :meth:`protect_control_files` has to rewrite it, not skip it.
        """
        return self.deny_state(username, path) == "current"

    def protect_control_files(self, username: str, root: str) -> list[PermissionResult]:
        """Apply the W3 deny ACEs to every control file that exists under *root*.

        Idempotent and cheap: an ``icacls`` query per candidate, and a write
        only when the ACE is missing. Windows ACLs need a real path — there is
        no Seatbelt-style pattern for a not-yet-existing file — which is why
        this is re-run after commands that could have created a repository.
        """
        results: list[PermissionResult] = []
        for target in windows_protected_control_files(root):
            if not os.path.exists(target):
                continue
            state = self.deny_state(username, target)
            if state == "current":
                continue
            if state == "stale":
                # Spec 114 D2: an install that carries the v0.16.0 ``W`` deny
                # heals here, on the worker's first command in the workspace.
                # ``/remove:d`` clears every deny shape, so the old ACE and
                # the new one never coexist. The ACE itself is the state —
                # no version marker, no installer step.
                logger.info(
                    "Rewriting stale W3 deny ACE for %s on %s (spec 114: "
                    "W carried SYNCHRONIZE and blocked git reads)",
                    username, target,
                )
                self.remove_deny(username, target)
            results.append(self.deny_access(username, target))
        return results

    def unprotect_control_files(self, username: str, root: str) -> list[PermissionResult]:
        """Reverse :meth:`protect_control_files` (teardown)."""
        results: list[PermissionResult] = []
        for target in windows_protected_control_files(root):
            if os.path.exists(target):
                results.append(self.remove_deny(username, target))
        return results

    def find_repository_roots(self, root: str, max_depth: int = 3) -> list[str]:
        """Directories under *root* that contain a ``.git`` folder.

        Depth-bounded and skipping dependency trees, so the post-command
        re-check stays cheap on a large workspace.
        """
        skip = {"node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".git"}
        found: list[str] = []
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            return found
        for dirpath, dirnames, _ in os.walk(root):
            depth = dirpath[len(root):].count(os.sep)
            if os.path.isdir(os.path.join(dirpath, ".git")):
                found.append(dirpath)
            if depth >= max_depth:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d not in skip]
        return found

    def grant_toolchain_roots(self, username: str) -> list[PermissionResult]:
        """Grant read+execute on each per-user toolchain root that exists (W1).

        The user's profile as a whole stays closed: a Windows profile holds
        browsers, mail and vaults, and a blanket grant plus deny ACEs for
        secrets would be both invasive and hard to reason about. Non-elevated
        is enough here — the user owns these folders.

        The ACE is **inheritable** (``(OI)(CI)``) and ``/T`` is deliberately
        absent (spec 109 D1). Setting an inheritable ACE on the root makes
        Windows propagate it to the existing subtree as *inherited* entries;
        ``/T`` additionally wrote an *explicit* ACE on every descendant, one
        file at a time, which on a dev machine (``%LOCALAPPDATA%\\Programs``,
        ``.cargo``, ``.rustup``, ``npm``: 10^5-10^6 files) pinned a CPU for
        minutes behind the installer's frozen "Configuring agent sandbox"
        bar (issue #55). Descendants whose DACL has inheritance disabled
        (protected) do not receive the ACE — V1 found one, ``Microsoft VS
        Code``, whose protected DACL grants ``BUILTIN\\Users:(RX)`` anyway,
        so the worker reads it regardless. Acceptable for a best-effort
        grant; anything missed is one click in Settings > Folder access.

        The propagation is still a walk of the subtree, just a much cheaper
        one, and it happens on EVERY ``/grant`` — re-granting an ACE that is
        already there costs the same as the first grant. So a root that
        already carries the explicit inheritable RX entry is skipped after
        one single-object ``icacls`` query; files created later inherit at
        creation time without any walk.

        Measured (spec 109 V1, 2026-10-10, Windows 10 22H2 laptop,
        ``%LOCALAPPDATA%\\Programs`` with 104,018 entries, NTFS SSD): with
        ``/T``: 182.3 s grant / 182.0 s remove; without: 23.5 s grant
        (19.2-19.3 s warm, identical for a repeat grant) / 19.6 s remove. A
        deep file (``Python\\Python313\\Lib\\encodings\\utf_8.py``) showed
        ``AgentOS-Worker:(I)(RX)`` after the ``/T``-less grant and lost it
        after the root-only ``/remove``.
        """
        results: list[PermissionResult] = []
        for root in windows_toolchain_roots():
            if not os.path.isdir(root):
                continue
            resolved = self._resolve_path(root)
            if resolved is None:
                continue
            query = self._run_icacls([resolved])
            if query.returncode == 0 and _has_inheritable_rx_grant(query.stdout, username):
                results.append(PermissionResult(success=True, path=resolved))
                continue
            result = self._run_icacls(
                [resolved, "/grant", f"{username}:(OI)(CI)RX", "/Q"]
            )
            if result.returncode != 0:
                err = result.stderr.strip() or result.stdout.strip()
                logger.warning("toolchain grant failed on %s: %s", resolved, err)
                results.append(PermissionResult(success=False, path=resolved, error=err))
            else:
                results.append(PermissionResult(success=True, path=resolved))
        return results

    def revoke_toolchain_roots(self, username: str) -> list[PermissionResult]:
        """Reverse :meth:`grant_toolchain_roots` (teardown).

        A root-only ``/remove`` still walks the whole subtree to re-propagate
        inheritance — and it does so even when the account has no entry on
        the root (spec 109 V1 follow-up, measured on a dev machine: npm 34.8
        s, ``%LOCALAPPDATA%\\Programs`` 19.3 s, pnpm 4.9 s; 59 s in total
        with or without the ACE present). This runs inside one hidden
        uninstaller step whose progress bar does not move, so: a root with no
        explicit entry for *username* is skipped after a single-object
        query, and the remaining roots are revoked concurrently. The roots
        never nest, so the concurrent propagations touch disjoint trees.
        Measured on the same machine: 49.7 s concurrent vs 58.8 s sequential
        with the ACE on all five roots (disk-bound, so not the ideal max of
        the roots), 0.04 s vs 59 s with no ACE to remove.
        """
        roots = [r for r in windows_toolchain_roots() if os.path.isdir(r)]
        if not roots:
            return []
        with ThreadPoolExecutor(max_workers=len(roots)) as pool:
            return list(pool.map(lambda r: self._revoke_toolchain_root(username, r), roots))

    def _revoke_toolchain_root(self, username: str, root: str) -> PermissionResult:
        resolved = self._resolve_path(root)
        if resolved is None:
            return PermissionResult(success=False, path=root, error="Path does not exist")
        query = self._run_icacls([resolved])
        if query.returncode == 0 and not _has_explicit_entry(query.stdout, username):
            return PermissionResult(success=True, path=resolved)
        return self.revoke_access(username, resolved)

    def setup_worker_home(self, username: str) -> PermissionResult:
        """Create the worker's own home under ProgramData and hand it over (W2)."""
        home = windows_worker_home()
        try:
            for sub in ("", "Temp", os.path.join("AppData", "Roaming"),
                        os.path.join("AppData", "Local")):
                os.makedirs(os.path.join(home, sub) if sub else home, exist_ok=True)
        except OSError as exc:
            logger.error("Failed to create worker home at %s: %s", home, exc)
            return PermissionResult(
                success=False, path=home, error=f"Failed to create worker home: {exc}"
            )
        return self.grant_access(username, home, "read_write")

    def check_access(self, username: str, path: str) -> AccessInfo:
        """Check what access *username* has on *path*."""
        resolved = self._resolve_path(path)
        if resolved is None:
            return AccessInfo(has_access=False, mode="none", path=path)

        result = self._run_icacls([resolved])
        if result.returncode != 0:
            logger.warning("icacls check failed on %s: %s", resolved, result.stderr.strip())
            return AccessInfo(has_access=False, mode="none", path=resolved)

        return _parse_icacls_output(result.stdout, username, resolved)

    def setup_workspace(self, username: str, workspace_path: str) -> PermissionResult:
        """Create the workspace directory structure and grant full control."""
        try:
            os.makedirs(workspace_path, exist_ok=True)
            agent_dir = os.path.join(workspace_path, WORKSPACE_AGENT_DIR)
            os.makedirs(agent_dir, exist_ok=True)
        except OSError as exc:
            logger.error("Failed to create workspace dirs at %s: %s", workspace_path, exc)
            return PermissionResult(
                success=False,
                path=workspace_path,
                error=f"Failed to create directories: {exc}",
            )

        grant_result = self.grant_access(username, workspace_path, "read_write")
        if not grant_result.success:
            return grant_result

        logger.info("Workspace set up for %s at %s", username, workspace_path)
        return PermissionResult(success=True, path=workspace_path)

    def get_available_folders(self) -> list[FolderInfo]:
        """Return standard user folders with accessibility info."""
        home = os.path.expanduser("~")
        folders: list[FolderInfo] = []

        for name in self._STANDARD_FOLDERS:
            folder_path = os.path.join(home, name)
            exists = os.path.isdir(folder_path)
            folders.append(
                FolderInfo(
                    path=folder_path,
                    display_name=name,
                    accessible=exists,
                    access_note=None if exists else "Folder does not exist",
                )
            )

        return folders

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_path(path: str) -> str | None:
        """Resolve *path* to an absolute, symlink-free path.

        Returns ``None`` if the path does not exist.
        """
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            return None
        return os.path.realpath(abs_path)

    def _run_icacls(
        self, args: list[str], timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run icacls with the given arguments.

        *timeout* (seconds) overrides :attr:`icacls_timeout` for this call;
        ``None`` falls back to the attribute. On ``TimeoutExpired`` the child
        is already killed by ``subprocess.run``; a synthetic
        ``CompletedProcess`` with ``returncode=-1`` and a ``"timed out"``
        stderr comes back, so every caller's existing ``returncode != 0``
        branch reports a failed PermissionResult instead of raising (spec
        109 D7). A timed-out call can leave propagation partial; every call
        on the installer path is idempotent and re-runs at daemon start.
        """
        cmd = ["icacls"] + args
        bound = timeout if timeout is not None else self.icacls_timeout
        logger.debug("Running: %s", " ".join(cmd))
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                # icacls emits ANSI-localized text on non-English Windows, which
                # is invalid UTF-8 — strict decoding would kill the pipe reader
                # thread and hand back stdout=None (see test_subprocess_encoding).
                errors="replace",
                creationflags=win_no_window_flags(),
                timeout=bound,
            )
        except subprocess.TimeoutExpired:
            target = args[0] if args else "?"
            logger.warning(
                "icacls timed out after %ss on %s; child killed, continuing",
                bound, target,
            )
            return subprocess.CompletedProcess(
                args=cmd, returncode=-1, stdout="",
                stderr=f"timed out after {bound}s",
            )


def _classify_deny_ace(output: str, username: str) -> DenyState:
    """Classify the deny ACEs *username* has in ``icacls`` output (spec 114 D2).

    icacls renders a specific-rights deny as
    ``DESKTOP-ABC\\AgentOS-Worker:(DENY)(WD,AD,WEA,WA)`` for a file and
    ``...:(DENY)(OI)(CI)(WD,AD,WEA,WA)`` for a directory; inherited copies
    under ``.git\\hooks`` carry a leading ``(I)``; the pre-114 shape is
    ``(DENY)(W)`` / ``(DENY)(OI)(CI)(W)``. The rights group is always the last
    parenthesised group on the line and is compared as a *set*, so icacls'
    ordering of the rights is irrelevant. Any deny for the account whose
    rights are not exactly :data:`W3_DENY_RIGHTS` is ``"stale"``, and one
    stale line outranks any number of current ones — the rewrite must run.
    """
    username_lower = username.lower() + ":"
    expected = {r.lower() for r in W3_DENY_RIGHTS}
    state: DenyState = "none"
    for line in output.splitlines():
        line_lower = line.lower()
        at = line_lower.find(username_lower)
        if at < 0:
            continue
        # The account name must end right at the colon and start after a
        # separator — ``AgentOS-Worker2:`` contains ``AgentOS-Worker`` but
        # is not it, and ``DOMAIN\\AgentOS-Worker`` is.
        if at > 0 and line_lower[at - 1] not in " \\\t":
            continue
        entry = line_lower[at + len(username_lower):]
        groups = _ACE_GROUP_RE.findall(entry)
        if "deny" not in groups:
            continue
        rights = {r.strip() for r in groups[-1].split(",") if r.strip()}
        if rights == expected:
            if state == "none":
                state = "current"
        else:
            return "stale"
    return state


def _has_explicit_entry(output: str, username: str) -> bool:
    """True when *username* has any non-inherited entry (allow or deny) in
    ``icacls`` output — something a root-only ``/remove`` would take off."""
    username_lower = username.lower()
    for line in output.splitlines():
        if username_lower + ":" not in line.lower():
            continue
        flags = {f.upper() for f in re.findall(r"\(([^)]+)\)", line)}
        if "I" not in flags:
            return True
    return False


def _has_inheritable_rx_grant(output: str, username: str) -> bool:
    """True when *username* has the explicit ``(OI)(CI)`` read+execute (or
    wider) allow entry that :meth:`PermissionManager.grant_toolchain_roots`
    writes, e.g. ``DESKTOP-ABC\\AgentOS-Worker:(OI)(CI)(RX)``.

    An inherited ``(I)`` copy does not count: it would vanish if the parent
    lost its entry, and the root is where the grant belongs.
    """
    username_lower = username.lower()
    for line in output.splitlines():
        line_lower = line.lower()
        if username_lower + ":" not in line_lower:
            continue
        flags = {f.upper() for f in re.findall(r"\(([^)]+)\)", line)}
        if "DENY" in flags or "I" in flags:
            continue
        if {"OI", "CI"} <= flags and flags & {"RX", "M", "F"}:
            return True
    return False


def _parse_icacls_output(output: str, username: str, path: str) -> AccessInfo:
    """Parse icacls output and determine the access level for *username*.

    icacls output example::

        C:\\Users\\dev\\project src\\main.py AgentOS-Worker:(OI)(CI)(F)
                                             BUILTIN\\Users:(OI)(CI)(RX)

    The username may appear with or without a domain prefix (e.g.
    ``DESKTOP-ABC\\AgentOS-Worker`` or just ``AgentOS-Worker``).
    """
    username_lower = username.lower()

    for line in output.splitlines():
        line_lower = line.lower()
        # Match "username:" accounting for optional DOMAIN\ prefix
        if username_lower + ":" not in line_lower:
            continue
        # A deny ACE (spec 077 W3) is not an access grant — reading it as one
        # would report read_write on a path the worker cannot write.
        if "(deny)" in line_lower:
            continue

        # Extract all permission flags in parentheses, e.g. (OI)(CI)(F)
        flags = re.findall(r"\(([^)]+)\)", line)
        flag_set = {f.upper() for f in flags}

        if "F" in flag_set:
            return AccessInfo(has_access=True, mode="read_write", path=path)
        if "R" in flag_set or "RX" in flag_set:
            return AccessInfo(has_access=True, mode="read_only", path=path)

        # Has an entry but no recognized read/write flag — treat as some access
        return AccessInfo(has_access=True, mode="read_only", path=path)

    return AccessInfo(has_access=False, mode="none", path=path)
