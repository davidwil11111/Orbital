# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Read a file's DACL as masks, not as ``icacls`` text.

``icacls`` renders access masks through a lossy table: on Windows 10 the
write-only mask ``0x116`` and generic-write ``0x100116`` both print as
``(W)``, so a classifier reading its output can never tell them apart (bench
2026-10-19, issue 2). This module asks the Win32 API for the ACEs instead.

Only what W3 needs: the ACE type, flags, mask and SID string. Off Windows
every reader returns ``None`` and callers fall back to "unknown".
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

logger = logging.getLogger("agent_os.platform.windows.acl")

ACCESS_ALLOWED_ACE_TYPE = 0
ACCESS_DENIED_ACE_TYPE = 1

OBJECT_INHERIT_ACE = 0x01
CONTAINER_INHERIT_ACE = 0x02
NO_PROPAGATE_INHERIT_ACE = 0x04
INHERIT_ONLY_ACE = 0x08
INHERITED_ACE = 0x10

#: icacls specific-rights abbreviations -> access-mask bits.
RIGHT_BITS: dict[str, int] = {
    "RD": 0x1,        # read data / list folder
    "WD": 0x2,        # write data / add file
    "AD": 0x4,        # append data / add subfolder
    "REA": 0x8,
    "WEA": 0x10,
    "X": 0x20,
    "DC": 0x40,       # delete child
    "RA": 0x80,
    "WA": 0x100,
    "DE": 0x10000,    # delete
    "RC": 0x20000,
    "WDAC": 0x40000,  # change permissions
    "WO": 0x80000,    # take ownership
    "S": 0x100000,    # synchronize
}


def rights_mask(rights) -> int:
    """The access mask for a tuple of icacls right abbreviations."""
    mask = 0
    for right in rights:
        mask |= RIGHT_BITS[right]
    return mask


@dataclass(frozen=True)
class Ace:
    type: int
    flags: int
    mask: int
    sid: str


def read_dacl(path: str) -> list[Ace] | None:
    """The allow/deny ACEs on *path*, or ``None`` when they cannot be read."""
    if sys.platform != "win32":
        return None
    try:
        return _read_dacl_win32(path)
    except Exception:  # noqa: BLE001 — unreadable means "unknown", never a crash
        logger.debug("read_dacl failed for %s", path, exc_info=True)
        return None


def account_sid(account: str) -> str | None:
    """The string SID of a local or domain account name, or ``None``."""
    if sys.platform != "win32":
        return None
    try:
        return _lookup_sid_win32(account)
    except Exception:  # noqa: BLE001
        logger.debug("account_sid failed for %s", account, exc_info=True)
        return None


# --- Win32 -------------------------------------------------------------------

_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x4


def _sid_to_string(psid) -> str:
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    out = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(psid, ctypes.byref(out)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return out.value
    finally:
        kernel32.LocalFree(out)


def _read_dacl_win32(path: str) -> list[Ace]:
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class ACL(ctypes.Structure):
        _fields_ = [("AclRevision", ctypes.c_ubyte), ("Sbz1", ctypes.c_ubyte),
                    ("AclSize", wintypes.WORD), ("AceCount", wintypes.WORD),
                    ("Sbz2", wintypes.WORD)]

    class ACE_HEADER(ctypes.Structure):
        _fields_ = [("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte),
                    ("AceSize", wintypes.WORD)]

    class ACCESS_ACE(ctypes.Structure):
        _fields_ = [("Header", ACE_HEADER), ("Mask", wintypes.DWORD),
                    ("SidStart", wintypes.DWORD)]

    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.POINTER(ACL)), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetAce.argtypes = [ctypes.POINTER(ACL), wintypes.DWORD,
                                ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetAce.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    dacl = ctypes.POINTER(ACL)()
    sd = ctypes.c_void_p()
    err = advapi32.GetNamedSecurityInfoW(
        path, _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION,
        None, None, ctypes.byref(dacl), None, ctypes.byref(sd))
    if err != 0:
        raise ctypes.WinError(err)
    try:
        aces: list[Ace] = []
        if not dacl:
            return aces  # a NULL DACL grants everyone everything; no denies
        for index in range(dacl.contents.AceCount):
            pace = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(pace)):
                raise ctypes.WinError(ctypes.get_last_error())
            header = ctypes.cast(pace, ctypes.POINTER(ACE_HEADER)).contents
            if header.AceType not in (ACCESS_ALLOWED_ACE_TYPE, ACCESS_DENIED_ACE_TYPE):
                continue  # object/callback ACEs never come from icacls
            ace = ctypes.cast(pace, ctypes.POINTER(ACCESS_ACE)).contents
            psid = pace.value + ACCESS_ACE.SidStart.offset
            aces.append(Ace(type=header.AceType, flags=header.AceFlags,
                            mask=ace.Mask, sid=_sid_to_string(psid)))
        return aces
    finally:
        kernel32.LocalFree(sd)


def _lookup_sid_win32(account: str) -> str:
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.LookupAccountNameW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]

    sid_size = wintypes.DWORD(0)
    domain_size = wintypes.DWORD(0)
    use = wintypes.DWORD(0)
    advapi32.LookupAccountNameW(None, account, None, ctypes.byref(sid_size),
                                None, ctypes.byref(domain_size), ctypes.byref(use))
    if sid_size.value == 0:
        raise ctypes.WinError(ctypes.get_last_error())
    sid = ctypes.create_string_buffer(sid_size.value)
    domain = ctypes.create_unicode_buffer(domain_size.value)
    if not advapi32.LookupAccountNameW(None, account, sid, ctypes.byref(sid_size),
                                       domain, ctypes.byref(domain_size), ctypes.byref(use)):
        raise ctypes.WinError(ctypes.get_last_error())
    return _sid_to_string(ctypes.cast(sid, ctypes.c_void_p))
