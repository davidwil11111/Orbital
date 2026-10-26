# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Desktop-app discovery: found by identity, never by a hardcoded name.

Layouts asserted here were read off real installs on 2026-10-26: DeepSeek
Harness 0.2.1-rc.3 (``com.deepseek.dsh``, launcher at
``Contents/Resources/runtime/cli/bin/dsh``) and ChatGPT 26.1013
(``com.openai.codex``, ``Contents/Resources/codex-cli/bin/codex``). The Windows
branch runs on any host through the registry seam.
"""

from __future__ import annotations

from agent_os.agents.desktop_apps import (
    DesktopApp,
    DesktopAppPlatform,
    download_url_for,
    find_desktop_app,
    parse_desktop_apps,
)

HOME = "/Users/u"
DSH_EXE = "Contents/Resources/runtime/cli/bin/dsh"


def _dsh_app(**mac_over) -> DesktopApp:
    mac = DesktopAppPlatform(executable=DSH_EXE, bundle_ids=["com.deepseek.dsh"],
                             app_names=["DeepSeek Harness.app"])
    for k, v in mac_over.items():
        setattr(mac, k, v)
    win = DesktopAppPlatform(
        executable="resources\\runtime\\cli\\bin\\dsh.cmd",
        display_names=["DeepSeek Harness"],
        install_dirs=["%LOCALAPPDATA%\\Programs\\DeepSeek Harness"])
    return DesktopApp(label="DeepSeek Harness", order="before_cli",
                      platforms={"macos": mac, "windows": win})


def _plists(table):
    return lambda app_path: table.get(app_path)


def _exists(paths):
    return lambda p: p in paths


def test_found_by_bundle_id_even_when_renamed_and_moved():
    app = "/Users/u/Tools/Renamed Harness.app"
    hit = find_desktop_app(
        _dsh_app(), "macos", home=HOME,
        mdfind=lambda bid: [app] if bid == "com.deepseek.dsh" else [],
        read_plist=_plists({app: {"CFBundleIdentifier": "com.deepseek.dsh",
                                  "CFBundleShortVersionString": "0.2.1-rc.3"}}),
        is_executable=_exists({f"{app}/{DSH_EXE}"}),
    )
    assert hit is not None
    assert hit.executable == f"{app}/{DSH_EXE}"
    assert hit.version == "0.2.1-rc.3"
    assert hit.app_path == app


def test_name_fallback_when_spotlight_is_off_still_checks_identity():
    real = "/Applications/DeepSeek Harness.app"
    hit = find_desktop_app(
        _dsh_app(), "macos", home=HOME, mdfind=lambda bid: [],
        read_plist=_plists({real: {"CFBundleIdentifier": "com.deepseek.dsh"}}),
        is_executable=_exists({f"{real}/{DSH_EXE}"}),
    )
    assert hit is not None and hit.app_path == real

    # Same name, different vendor: not our agent.
    impostor = find_desktop_app(
        _dsh_app(), "macos", home=HOME, mdfind=lambda bid: [],
        read_plist=_plists({real: {"CFBundleIdentifier": "com.example.other"}}),
        is_executable=_exists({f"{real}/{DSH_EXE}"}),
    )
    assert impostor is None


def test_installed_copy_beats_a_mounted_dmg_and_the_trash_is_ignored():
    dmg = "/Volumes/DeepSeek Harness/DeepSeek Harness.app"
    trash = "/Users/u/.Trash/DeepSeek Harness.app"
    user = "/Users/u/Applications/DeepSeek Harness.app"
    ident = {"CFBundleIdentifier": "com.deepseek.dsh"}
    hit = find_desktop_app(
        _dsh_app(app_names=[]), "macos", home=HOME,
        mdfind=lambda bid: [dmg, trash, user],
        read_plist=_plists({dmg: ident, trash: ident, user: ident}),
        is_executable=_exists({f"{p}/{DSH_EXE}" for p in (dmg, trash, user)}),
    )
    assert hit is not None and hit.app_path == user

    only_dmg = find_desktop_app(
        _dsh_app(app_names=[]), "macos", home=HOME,
        mdfind=lambda bid: [dmg, trash], read_plist=_plists({dmg: ident, trash: ident}),
        is_executable=_exists({f"{dmg}/{DSH_EXE}", f"{trash}/{DSH_EXE}"}),
    )
    assert only_dmg is None


def test_app_without_the_cli_inside_is_not_a_hit():
    real = "/Applications/DeepSeek Harness.app"
    hit = find_desktop_app(
        _dsh_app(), "macos", home=HOME, mdfind=lambda bid: [real],
        read_plist=_plists({real: {"CFBundleIdentifier": "com.deepseek.dsh"}}),
        is_executable=_exists(set()),
    )
    assert hit is None


def test_windows_install_location_from_the_uninstall_registry():
    entries = [
        {"DisplayName": "Something Else", "InstallLocation": "C:\\Other"},
        {"DisplayName": "DeepSeek Harness 0.2.1-rc.3",
         "InstallLocation": "D:\\Apps\\DSH", "DisplayVersion": "0.2.1-rc.3"},
    ]
    exe = "D:\\Apps\\DSH\\resources\\runtime\\cli\\bin\\dsh.cmd"
    hit = find_desktop_app(_dsh_app(), "windows", env={},
                           registry_entries=lambda: entries, is_file=_exists({exe}))
    assert hit is not None
    assert hit.executable == exe
    assert hit.version == "0.2.1-rc.3"


def test_windows_default_dir_when_the_registry_has_nothing():
    exe = ("C:\\Users\\u\\AppData\\Local\\Programs\\DeepSeek Harness"
           "\\resources\\runtime\\cli\\bin\\dsh.cmd")
    hit = find_desktop_app(
        _dsh_app(), "windows", env={"LOCALAPPDATA": "C:\\Users\\u\\AppData\\Local"},
        registry_entries=lambda: [], is_file=_exists({exe}))
    assert hit is not None and hit.executable == exe

    # An unset variable is not expanded into a relative path.
    assert find_desktop_app(_dsh_app(), "windows", env={},
                            registry_entries=lambda: [], is_file=lambda p: True) is None


def test_no_spec_for_this_os_means_no_hit():
    assert find_desktop_app(_dsh_app(), "linux") is None


def test_a_failing_seam_never_raises():
    def boom(_):
        raise RuntimeError("spotlight exploded")
    assert find_desktop_app(_dsh_app(), "macos", home=HOME, mdfind=boom) is None


def test_parse_skips_malformed_entries_and_defaults_order():
    apps = parse_desktop_apps([
        {"label": "DeepSeek Harness", "order": "before_cli",
         "macos": {"executable": DSH_EXE, "bundle_ids": ["com.deepseek.dsh"]},
         "download_urls": {"macos-arm64": "https://x/arm.dmg"}},
        {"label": "No platforms"},
        {"order": "before_cli"},
        "garbage",
        {"label": "Bad order", "order": "sideways", "macos": {"executable": "x"}},
    ])
    assert [a.label for a in apps] == ["DeepSeek Harness", "No platforms", "Bad order"]
    assert apps[0].order == "before_cli"
    assert apps[0].platforms["macos"].bundle_ids == ["com.deepseek.dsh"]
    assert apps[1].platforms == {}
    assert apps[2].order == "after_cli"


def test_download_url_by_os_and_arch():
    app = DesktopApp(label="x", download_urls={
        "macos-arm64": "https://x/arm.dmg", "macos-x86_64": "https://x/intel.dmg",
        "windows": "https://x/setup.exe"})
    assert download_url_for(app, "macos", "arm64") == "https://x/arm.dmg"
    assert download_url_for(app, "macos", "x86_64") == "https://x/intel.dmg"
    assert download_url_for(app, "windows", "AMD64") == "https://x/setup.exe"
    assert download_url_for(app, "linux", "x86_64") is None


# ---------------------------------------------------------------------------
# The locate card: the user picked the app itself
# ---------------------------------------------------------------------------

from agent_os.agents.desktop_apps import launcher_inside  # noqa: E402


def test_a_picked_app_bundle_maps_to_the_launcher_inside():
    app = "/Users/u/Downloads/DeepSeek Harness.app"
    exe = f"{app}/{DSH_EXE}"
    assert launcher_inside(
        [_dsh_app()], "macos", app + "/",
        read_plist=_plists({app: {"CFBundleIdentifier": "com.deepseek.dsh"}}),
        is_executable=_exists({exe}), is_dir=lambda p: p == app,
    ) == exe


def test_a_picked_app_of_another_vendor_is_not_mapped():
    app = "/Applications/Other.app"
    assert launcher_inside(
        [_dsh_app()], "macos", app,
        read_plist=_plists({app: {"CFBundleIdentifier": "com.example.other"}}),
        is_executable=lambda p: True, is_dir=lambda p: True,
    ) is None


def test_a_plain_executable_is_left_alone():
    assert launcher_inside([_dsh_app()], "macos", "/usr/local/bin/dsh",
                           is_dir=lambda p: False) is None


def test_a_picked_windows_install_folder_maps_to_its_cmd():
    folder = "D:\\Apps\\DSH"
    exe = folder + "\\resources\\runtime\\cli\\bin\\dsh.cmd"
    assert launcher_inside([_dsh_app()], "windows", folder,
                           is_dir=lambda p: True, is_file=_exists({exe})) == exe
