# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Find an agent CLI that ships inside a vendor's desktop app.

Some vendors now ship their agent as a desktop app with the CLI inside it
(DeepSeek Harness, ChatGPT's codex). Users install the app and expect Orbital
to see the agent; nothing lands on PATH unless they opt in.

Apps are found by identity, not by a fixed path. On macOS that is the bundle
id: Spotlight is asked for it, and the usual Applications folders are scanned
by name as a fallback, but a candidate only counts when its Info.plist carries
one of the declared ids — a renamed or moved app is still found, and an
unrelated app that happens to share a name is not. (A product that hardcoded
``DSH Desktop.app/...`` was already wrong by the time the app shipped as
``DeepSeek Harness.app``.) On Windows the uninstall registry's
``InstallLocation`` is the identity, with the installer's default directories
as the fallback.

Every OS access goes through a seam (``mdfind``, ``read_plist``,
``registry_entries``, ``is_executable``) so both platforms' rules are testable
from either host.
"""

from __future__ import annotations

import logging
import os
import plistlib
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Iterable

from agent_os.utils.subprocess_flags import win_no_window_flags

logger = logging.getLogger(__name__)

MDFIND_TIMEOUT_S = 3.0


@dataclass
class DesktopAppPlatform:
    """How one desktop app is laid out on one OS."""
    # The CLI's path inside the app (macOS: relative to the ``.app``;
    # Windows: relative to the install directory).
    executable: str = ""
    # macOS
    bundle_ids: list[str] = field(default_factory=list)
    app_names: list[str] = field(default_factory=list)   # "DeepSeek Harness.app"
    # Windows
    display_names: list[str] = field(default_factory=list)  # uninstall DisplayName prefixes
    install_dirs: list[str] = field(default_factory=list)   # "%LOCALAPPDATA%\\Programs\\..."


@dataclass
class DesktopApp:
    """A vendor desktop app that bundles an agent CLI (``setup.desktop_apps``)."""
    label: str                              # "DeepSeek Harness"
    # Relative to the CLI found on PATH / auto_detect. "before_cli" when the
    # desktop app is the vendor's primary distribution; "after_cli" when a
    # standalone CLI the user installed should win.
    order: str = "after_cli"
    platforms: dict[str, DesktopAppPlatform] = field(default_factory=dict)
    # Where the "Install" link points, keyed "macos-arm64" | "macos-x86_64" |
    # "windows" | "linux" (or a bare OS name for every arch).
    download_urls: dict[str, str] = field(default_factory=dict)


@dataclass
class DesktopAppHit:
    """A desktop app that was found, with the CLI inside it."""
    label: str
    app_path: str        # the .app bundle / install directory
    executable: str      # absolute path to the CLI to spawn
    version: str | None  # the app's own version, when it states one


def parse_desktop_apps(raw) -> list[DesktopApp]:
    """``setup.desktop_apps`` from a manifest dict; malformed entries skipped."""
    apps: list[DesktopApp] = []
    for entry in raw or []:
        if not isinstance(entry, dict) or not entry.get("label"):
            continue
        platforms: dict[str, DesktopAppPlatform] = {}
        for os_name in ("macos", "windows", "linux"):
            spec = entry.get(os_name)
            if not isinstance(spec, dict) or not spec.get("executable"):
                continue
            platforms[os_name] = DesktopAppPlatform(
                executable=spec["executable"],
                bundle_ids=list(spec.get("bundle_ids") or []),
                app_names=list(spec.get("app_names") or []),
                display_names=list(spec.get("display_names") or []),
                install_dirs=list(spec.get("install_dirs") or []),
            )
        order = entry.get("order", "after_cli")
        apps.append(DesktopApp(
            label=entry["label"],
            order=order if order in ("before_cli", "after_cli") else "after_cli",
            platforms=platforms,
            download_urls=dict(entry.get("download_urls") or {}),
        ))
    return apps


def download_url_for(app: DesktopApp, os_name: str, machine: str) -> str | None:
    """The install link for this OS/arch, falling back to the bare OS key."""
    arch = {"aarch64": "arm64", "amd64": "x86_64", "x64": "x86_64"}.get(
        (machine or "").lower(), (machine or "").lower())
    return (app.download_urls.get(f"{os_name}-{arch}")
            or app.download_urls.get(os_name))


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------

def _default_mdfind(bundle_id: str) -> list[str]:
    """Spotlight's answer for a bundle id; [] when Spotlight is off or slow."""
    try:
        result = subprocess.run(
            ["mdfind", f"kMDItemCFBundleIdentifier == '{bundle_id}'"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=MDFIND_TIMEOUT_S, creationflags=win_no_window_flags(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _default_read_plist(app_path: str) -> dict | None:
    try:
        with open(os.path.join(app_path, "Contents", "Info.plist"), "rb") as f:
            data = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _default_is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _macos_rank(path: str, home: str) -> int:
    """Installed copies first; a mounted DMG or the Trash last."""
    if path.startswith("/Applications/"):
        return 0
    if home and path.startswith(os.path.join(home, "Applications") + os.sep):
        return 1
    if path.startswith("/Volumes/") or "/.Trash/" in path:
        return 9
    return 5


def find_macos(spec: DesktopAppPlatform, label: str, *, home: str,
               mdfind: Callable[[str], list[str]] = _default_mdfind,
               read_plist: Callable[[str], dict | None] = _default_read_plist,
               is_executable: Callable[[str], bool] = _default_is_executable,
               ) -> DesktopAppHit | None:
    candidates: list[str] = []
    for bundle_id in spec.bundle_ids:
        candidates.extend(p for p in mdfind(bundle_id) if p.endswith(".app"))
    for name in spec.app_names:
        candidates.append(os.path.join("/Applications", name))
        if home:
            candidates.append(os.path.join(home, "Applications", name))
    seen: set[str] = set()
    ordered = []
    for path in candidates:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    ordered.sort(key=lambda p: _macos_rank(p, home))
    for app_path in ordered:
        if _macos_rank(app_path, home) == 9:
            continue  # a DMG still mounted or an app in the Trash is not installed
        info = read_plist(app_path)
        if info is None:
            continue
        if spec.bundle_ids and info.get("CFBundleIdentifier") not in spec.bundle_ids:
            continue
        executable = os.path.join(app_path, spec.executable)
        if not is_executable(executable):
            continue
        version = info.get("CFBundleShortVersionString")
        return DesktopAppHit(label=label, app_path=app_path, executable=executable,
                             version=str(version) if version else None)
    return None


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------

_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"


def _default_registry_entries() -> Iterable[dict]:
    """Uninstall entries (DisplayName, InstallLocation, DisplayVersion) from
    HKCU then HKLM (both views). Empty off Windows."""
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return []
    roots = [
        (winreg.HKEY_CURRENT_USER, 0),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
    ]
    entries: list[dict] = []
    for root, view in roots:
        try:
            parent = winreg.OpenKey(root, _UNINSTALL_KEY, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with parent:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(parent, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(parent, name) as sub:
                        entry = {}
                        for field_name in ("DisplayName", "InstallLocation", "DisplayVersion"):
                            try:
                                entry[field_name] = winreg.QueryValueEx(sub, field_name)[0]
                            except OSError:
                                pass
                        entries.append(entry)
                except OSError:
                    continue
    return entries


_PERCENT_VAR = re.compile(r"%([^%]+)%")


def _expand_windows(path: str, env: dict[str, str]) -> str | None:
    """``%VAR%`` expansion against ``env``; None when a variable is unset."""
    missing = False

    def sub(match: re.Match) -> str:
        nonlocal missing
        value = env.get(match.group(1)) or env.get(match.group(1).upper())
        if not value:
            missing = True
            return ""
        return value

    expanded = _PERCENT_VAR.sub(sub, path)
    return None if missing else expanded


def find_windows(spec: DesktopAppPlatform, label: str, *, env: dict[str, str],
                 registry_entries: Callable[[], Iterable[dict]] = _default_registry_entries,
                 is_file: Callable[[str], bool] = os.path.isfile,
                 ) -> DesktopAppHit | None:
    candidates: list[tuple[str, str | None]] = []
    prefixes = [n.lower() for n in spec.display_names]
    if prefixes:
        for entry in registry_entries():
            name = str(entry.get("DisplayName") or "").lower()
            location = str(entry.get("InstallLocation") or "").strip().strip('"')
            if location and any(name.startswith(p) for p in prefixes):
                version = entry.get("DisplayVersion")
                candidates.append((location, str(version) if version else None))
    for raw in spec.install_dirs:
        expanded = _expand_windows(raw, env)
        if expanded:
            candidates.append((expanded, None))
    for install_dir, version in candidates:
        executable = _join_windows(install_dir, spec.executable)
        if is_file(executable):
            return DesktopAppHit(label=label, app_path=install_dir,
                                 executable=executable, version=version)
    return None


def _join_windows(base: str, rel: str) -> str:
    return base.rstrip("\\/") + "\\" + rel.replace("/", "\\").lstrip("\\")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def find_desktop_app(app: DesktopApp, os_name: str, *,
                     home: str | None = None,
                     env: dict[str, str] | None = None,
                     **seams) -> DesktopAppHit | None:
    """The first installed copy of ``app`` on ``os_name``, or None."""
    spec = app.platforms.get(os_name)
    if spec is None:
        return None
    try:
        if os_name == "macos":
            return find_macos(spec, app.label,
                              home=home if home is not None else os.path.expanduser("~"),
                              **{k: v for k, v in seams.items()
                                 if k in ("mdfind", "read_plist", "is_executable")})
        if os_name == "windows":
            return find_windows(spec, app.label,
                                env=env if env is not None else dict(os.environ),
                                **{k: v for k, v in seams.items()
                                   if k in ("registry_entries", "is_file")})
    except Exception:  # noqa: BLE001 — discovery must never fail the status sweep
        logger.warning("desktop app lookup failed for %s", app.label, exc_info=True)
    return None


def launcher_inside(apps: list[DesktopApp], os_name: str, path: str, *,
                    read_plist: Callable[[str], dict | None] = _default_read_plist,
                    is_executable: Callable[[str], bool] = _default_is_executable,
                    is_dir: Callable[[str], bool] = os.path.isdir,
                    is_file: Callable[[str], bool] = os.path.isfile,
                    ) -> str | None:
    """The agent CLI inside an app the user picked by hand.

    The locate card takes the path the user has — on macOS that is the
    ``.app`` they see in Finder, on Windows the install folder — and this maps
    it to the launcher inside, using the same layout rules discovery uses. A
    bundle that declares a bundle id must carry it. None when ``path`` is not
    such an app (the card then tests the path as given).
    """
    path = (path or "").rstrip("/\\")
    if not path or not is_dir(path):
        return None
    for app in apps:
        spec = app.platforms.get(os_name)
        if spec is None:
            continue
        if os_name == "macos":
            if not path.endswith(".app"):
                continue
            if spec.bundle_ids:
                info = read_plist(path) or {}
                if info.get("CFBundleIdentifier") not in spec.bundle_ids:
                    continue
            candidate = os.path.join(path, spec.executable)
            if is_executable(candidate):
                return candidate
        elif os_name == "windows":
            candidate = _join_windows(path, spec.executable)
            if is_file(candidate):
                return candidate
    return None
