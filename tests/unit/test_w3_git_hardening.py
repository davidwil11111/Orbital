# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""W3 on Windows protects a repository's control files from the worker while
letting the worker use git normally (bench 2026-10-19, issues 1 and 2).

Windows decides delete, rename-over and re-permission from more than the
file's own write bits: a file can be replaced by anyone holding delete on it
or delete-child on its folder, and a DACL can be rewritten by anyone holding
change-permissions (or by the owner). So W3 denies, per target:

* ``.git\\config`` and ``.git\\hooks`` — the write rights, plus delete,
  change-permissions and take-ownership (``hooks`` also delete-child);
* the ``.git`` folder and the repository root, this folder only — delete,
  delete-child, change-permissions, take-ownership.

Git's normal writes (index, refs, objects, lock files) stay allowed because
those files carry their own delete right from the workspace grant.

The classifier reads the ACE masks themselves: ``icacls`` prints the 0x116
and 0x100116 masks identically as ``(W)``, so text could never tell a
current deny from a stale one.
"""

from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from agent_os.platform.windows.acl import (
    ACCESS_DENIED_ACE_TYPE,
    ACCESS_ALLOWED_ACE_TYPE,
    INHERITED_ACE,
    OBJECT_INHERIT_ACE,
    CONTAINER_INHERIT_ACE,
    Ace,
    rights_mask,
)
from agent_os.platform.windows.permissions import (
    W3_TARGET_RIGHTS,
    PermissionManager,
    classify_deny_aces,
    w3_targets,
)

USER = "AgentOS-Worker"
WORKER_SID = "S-1-5-21-1-2-3-1001"
OTHER_SID = "S-1-5-21-1-2-3-1002"


def _ok(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["icacls"], returncode=0, stdout=stdout, stderr="")


@pytest.fixture
def pm():
    return PermissionManager()


@pytest.fixture
def repo(tmp_path):
    git = tmp_path / ".git"
    (git / "hooks").mkdir(parents=True)
    (git / "config").write_text("[core]\n")
    return tmp_path


# --- the rights each target denies ------------------------------------------


def test_control_files_deny_delete_and_repermission_not_just_write():
    for kind in ("config", "hooks"):
        rights = set(W3_TARGET_RIGHTS[kind])
        assert {"WD", "AD", "WEA", "WA"} <= rights
        # Delete (replace-by-rename), change permissions, take ownership.
        assert {"DE", "WDAC", "WO"} <= rights, kind
    assert "DC" in W3_TARGET_RIGHTS["hooks"]


def test_git_dir_and_repo_root_deny_delete_child_and_repermission():
    assert {"DE", "DC", "WDAC", "WO"} <= set(W3_TARGET_RIGHTS["git_dir"])
    assert {"DC", "WDAC", "WO"} <= set(W3_TARGET_RIGHTS["repo_root"])


def test_no_target_denies_synchronize_or_reads():
    # A denied SYNCHRONIZE fails every synchronous open (issue #58), and the
    # worker must keep reading the repository.
    for kind, rights in W3_TARGET_RIGHTS.items():
        assert not ({"S", "W", "M", "F", "R", "RX", "RD", "GR", "GW"} & set(rights)), kind


def test_rights_mask_matches_the_windows_bits():
    assert rights_mask(("WD", "AD", "WEA", "WA")) == 0x116
    assert rights_mask(("DE",)) == 0x10000
    assert rights_mask(("DC",)) == 0x40
    assert rights_mask(("WDAC", "WO")) == 0xC0000


# --- which paths are targets --------------------------------------------------


def test_targets_cover_root_git_dir_hooks_and_config(repo):
    kinds = {kind: path for path, kind in w3_targets(str(repo))}
    assert set(kinds) == {"repo_root", "git_dir", "hooks", "config"}
    assert os.path.normcase(kinds["repo_root"]) == os.path.normcase(str(repo))
    assert os.path.basename(kinds["git_dir"]) == ".git"


def test_a_folder_without_a_repository_has_no_targets(tmp_path):
    assert w3_targets(str(tmp_path)) == []


def test_a_git_file_worktree_pointer_is_not_a_git_dir(tmp_path):
    (tmp_path / ".git").write_text("gitdir: elsewhere\n")
    assert w3_targets(str(tmp_path)) == []


# --- classification from real masks -------------------------------------------


def _deny(kind, flags=None, sid=WORKER_SID, mask=None):
    expected_flags = (OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE) if kind == "hooks" else 0
    return Ace(
        type=ACCESS_DENIED_ACE_TYPE,
        flags=expected_flags if flags is None else flags,
        mask=rights_mask(W3_TARGET_RIGHTS[kind]) if mask is None else mask,
        sid=sid,
    )


@pytest.mark.parametrize("kind", ["config", "hooks", "git_dir", "repo_root"])
def test_the_exact_deny_is_current(kind):
    assert classify_deny_aces([_deny(kind)], WORKER_SID, kind) == "current"


@pytest.mark.parametrize("old_mask", [0x116, 0x100116])
def test_the_v016_and_v017_masks_are_stale(old_mask):
    # 0x100116 is v0.16.0's ``W`` (with SYNCHRONIZE); 0x116 is spec 114's
    # write-only deny. Both print as ``(W)`` in icacls on Windows 10.
    assert classify_deny_aces([_deny("config", mask=old_mask)], WORKER_SID, "config") == "stale"


def test_no_deny_is_none():
    allow = Ace(type=ACCESS_ALLOWED_ACE_TYPE, flags=0, mask=0x1F01FF, sid=WORKER_SID)
    assert classify_deny_aces([allow], WORKER_SID, "config") == "none"


def test_another_accounts_deny_is_ignored():
    assert classify_deny_aces([_deny("config", sid=OTHER_SID)], WORKER_SID, "config") == "none"


def test_an_inherited_deny_does_not_count_as_this_targets_deny():
    inherited = _deny("hooks", flags=INHERITED_ACE | OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE)
    assert classify_deny_aces([inherited], WORKER_SID, "hooks") == "none"


def test_wrong_inheritance_on_hooks_is_stale():
    assert classify_deny_aces([_deny("hooks", flags=0)], WORKER_SID, "hooks") == "stale"


def test_a_stale_deny_next_to_a_current_one_still_needs_the_rewrite():
    aces = [_deny("config"), _deny("config", mask=0x116)]
    assert classify_deny_aces(aces, WORKER_SID, "config") == "stale"


# --- command shapes ------------------------------------------------------------


def test_deny_on_config_names_every_right_without_inheritance(pm, repo):
    with patch.object(PermissionManager, "_run_icacls", return_value=_ok()) as run:
        assert pm.deny_access(USER, str(repo / ".git" / "config"), kind="config").success
    args = run.call_args[0][0]
    grant = next(a for a in args if a.startswith(USER + ":"))
    assert "(OI)" not in grant and "/T" not in args
    assert set(grant.split(":", 1)[1].strip("()").split(",")) == set(W3_TARGET_RIGHTS["config"])


def test_deny_on_hooks_is_inheritable_and_recursive(pm, repo):
    with patch.object(PermissionManager, "_run_icacls", return_value=_ok()) as run:
        assert pm.deny_access(USER, str(repo / ".git" / "hooks"), kind="hooks").success
    args = run.call_args[0][0]
    grant = next(a for a in args if a.startswith(USER + ":"))
    assert grant.startswith(f"{USER}:(OI)(CI)(") and "/T" in args


@pytest.mark.parametrize("kind, sub", [("git_dir", ".git"), ("repo_root", "")])
def test_folder_denies_are_this_folder_only(pm, repo, kind, sub):
    target = repo / sub if sub else repo
    with patch.object(PermissionManager, "_run_icacls", return_value=_ok()) as run:
        assert pm.deny_access(USER, str(target), kind=kind).success
    args = run.call_args[0][0]
    grant = next(a for a in args if a.startswith(USER + ":"))
    # No inheritance flags (would spread delete-denies to every file git
    # rewrites) and no /T (would stamp them on every descendant).
    assert "(OI)" not in grant and "(CI)" not in grant
    assert "/T" not in args


@pytest.mark.parametrize("kind, sub", [("git_dir", ".git"), ("repo_root", "")])
def test_removing_a_folder_deny_does_not_recurse(pm, repo, kind, sub):
    # A recursive /remove:d on the repository root would strip every deny in
    # the workspace, nested repositories included.
    target = repo / sub if sub else repo
    with patch.object(PermissionManager, "_run_icacls", return_value=_ok()) as run:
        pm.remove_deny(USER, str(target), kind=kind)
    assert "/T" not in run.call_args[0][0]


def test_protect_applies_every_missing_target(pm, repo):
    with patch.object(PermissionManager, "deny_state", return_value="none"), \
            patch.object(PermissionManager, "remove_deny") as remove, \
            patch.object(PermissionManager, "deny_access", return_value=_ok()) as deny:
        pm.protect_control_files(USER, str(repo))
    kinds = sorted(c.kwargs["kind"] for c in deny.call_args_list)
    assert kinds == ["config", "git_dir", "hooks", "repo_root"]
    remove.assert_not_called()


def test_protect_is_a_no_op_when_everything_is_current(pm, repo):
    with patch.object(PermissionManager, "deny_state", return_value="current"), \
            patch.object(PermissionManager, "deny_access") as deny:
        assert pm.protect_control_files(USER, str(repo)) == []
    deny.assert_not_called()


def test_protect_rewrites_only_the_stale_target(pm, repo):
    states = {"config": "stale", "hooks": "current", "git_dir": "current", "repo_root": "current"}
    calls = []
    with patch.object(PermissionManager, "deny_state",
                      side_effect=lambda u, p, kind: states[kind]), \
            patch.object(PermissionManager, "remove_deny",
                         side_effect=lambda u, p, kind: calls.append(("remove", kind)) or _ok()), \
            patch.object(PermissionManager, "deny_access",
                         side_effect=lambda u, p, kind: calls.append(("deny", kind)) or _ok()):
        pm.protect_control_files(USER, str(repo))
    assert calls == [("remove", "config"), ("deny", "config")]


def test_unprotect_removes_every_target(pm, repo):
    with patch.object(PermissionManager, "remove_deny", return_value=_ok()) as remove:
        pm.unprotect_control_files(USER, str(repo))
    assert sorted(c.kwargs["kind"] for c in remove.call_args_list) == [
        "config", "git_dir", "hooks", "repo_root"]


# --- the writable root lets the daemon manage what the worker creates ---------


def test_read_write_grant_adds_the_user_and_owner_rights_entries(pm, tmp_path):
    with patch.object(PermissionManager, "_run_icacls", return_value=_ok()) as run, \
            patch.object(PermissionManager, "_root_hardening_present", return_value=False):
        assert pm.grant_access(USER, str(tmp_path), "read_write").success
    args = [a for c in run.call_args_list for a in c[0][0]]
    assert "*S-1-3-4:(OI)(CI)M" in args, args
    # The current user (not the worker) gets an inheritable full-control entry.
    assert any(a.endswith(":(OI)(CI)F") and not a.startswith(USER) for a in args), args
    # ...and it is applied before the worker grant, so it is inherited by
    # everything the worker creates.
    calls = [" ".join(c[0][0]) for c in run.call_args_list]
    assert "S-1-3-4" in calls[0] and f"{USER}:(OI)(CI)F" in calls[-1]


def test_read_only_grant_does_not_touch_root_hardening(pm, tmp_path):
    with patch.object(PermissionManager, "_run_icacls", return_value=_ok()) as run:
        assert pm.grant_access(USER, str(tmp_path), "read_only").success
    assert not any("S-1-3-4" in " ".join(c[0][0]) for c in run.call_args_list)


def test_root_hardening_is_skipped_when_already_present(pm, tmp_path):
    with patch.object(PermissionManager, "_run_icacls", return_value=_ok()) as run, \
            patch.object(PermissionManager, "_root_hardening_present", return_value=True):
        pm.harden_writable_root(str(tmp_path))
    run.assert_not_called()


# --- live check of the icacls -> mask mapping (Windows only) -------------------


@pytest.mark.skipif(sys.platform != "win32", reason="real ACLs")
def test_icacls_writes_the_masks_the_classifier_expects(tmp_path):
    from agent_os.platform.windows.acl import read_dacl

    target = tmp_path / "f.txt"
    target.write_text("x")
    everyone = "*S-1-1-0"
    rights = W3_TARGET_RIGHTS["config"]
    subprocess.run(["icacls", str(target), "/deny", f"{everyone}:({','.join(rights)})", "/Q"],
                   check=True, capture_output=True)
    try:
        aces = read_dacl(str(target))
        assert aces is not None
        assert classify_deny_aces(aces, "S-1-1-0", "config") == "current"
    finally:
        subprocess.run(["icacls", str(target), "/remove:d", everyone, "/Q"], capture_output=True)


# --- the provider hardens each writable root before protecting it -------------


def test_provider_hardens_the_root_before_protecting_it(tmp_path):
    from unittest.mock import MagicMock

    from agent_os.platform.windows.provider import WindowsPlatformProvider

    provider = object.__new__(WindowsPlatformProvider)
    order = []
    provider._account_manager = MagicMock()
    provider._account_manager.get_username.return_value = USER
    pmock = MagicMock()
    pmock.find_repository_roots.return_value = []
    pmock.harden_writable_root.side_effect = lambda root: order.append("harden")
    pmock.protect_control_files.side_effect = lambda u, root: order.append("protect")
    provider._permission_manager = pmock
    provider._protect_control_files(str(tmp_path))
    assert order == ["harden", "protect"]
