# Orbital — An operating system for AI agents
# Copyright (C) 2026 Orbital Contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Spec 115 §6 (O2) — the macOS close button hides the window; a Dock click
or app activation brings it back; Cmd+Q still quits.

The red close button used to *miniaturize* the window, which users read as
"I pressed the wrong button" (issue #57 part 2). The macOS convention for a
document-less app whose process keeps running (Slack, Discord, Spotify) is
``orderOut:`` plus ``applicationShouldHandleReopen:hasVisibleWindows:``.

AppKit is not loaded here: the delegate class is built over a plain base and
the window is a fake, so the decision logic is pinned on every host. The
visual half is the manual check on the CI ``.dmg`` in the spec.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

from agent_os.desktop import main


@pytest.fixture
def window():
    w = MagicMock()
    w.native = MagicMock()
    return w


@pytest.fixture(autouse=True)
def _reset_state():
    main._close_state["hidden"] = False
    yield
    main._close_state["hidden"] = False


def test_close_button_hides_instead_of_miniaturizing_on_macos(window, monkeypatch):
    monkeypatch.setattr(main.sys, "platform", "darwin")
    assert main._handle_window_close(window) is False
    window.hide.assert_called_once_with()
    window.native.miniaturize_.assert_not_called()
    assert main._close_state["hidden"] is True


def test_quit_still_closes_on_macos(window, monkeypatch):
    monkeypatch.setattr(main.sys, "platform", "darwin")

    def applicationShouldTerminate_():
        # Cmd+Q and Dock → Quit reach the closing event through this frame.
        return main._handle_window_close(window)

    assert applicationShouldTerminate_() is True
    window.hide.assert_not_called()
    assert main._close_state["hidden"] is False


def test_close_button_hides_to_the_tray_elsewhere(window, monkeypatch):
    monkeypatch.setattr(main.sys, "platform", "win32")
    assert main._handle_window_close(window) is False
    window.hide.assert_called_once_with()


def test_restore_shows_the_window_once(window):
    main._close_state["hidden"] = True
    main._restore_hidden_window(window)
    main._restore_hidden_window(window)
    window.show.assert_called_once_with()
    assert main._close_state["hidden"] is False


def test_restore_is_a_no_op_when_nothing_was_hidden(window):
    main._restore_hidden_window(window)
    window.show.assert_not_called()


class _FakeDelegateBase:
    pass


def test_dock_click_reopens_a_hidden_window():
    restore = MagicMock()
    cls = main._reopen_delegate_class(_FakeDelegateBase, restore)
    assert issubclass(cls, _FakeDelegateBase)
    d = cls()
    main._close_state["hidden"] = True
    # No visible windows + we hid one: restore, and tell AppKit it is handled.
    assert d.applicationShouldHandleReopen_hasVisibleWindows_(None, False) is False
    restore.assert_called_once_with()


def test_dock_click_leaves_a_miniaturized_or_visible_window_to_appkit():
    restore = MagicMock()
    d = main._reopen_delegate_class(_FakeDelegateBase, restore)()
    # Visible window: default handling.
    assert d.applicationShouldHandleReopen_hasVisibleWindows_(None, True) is True
    # Not visible but we did not hide it (user minimized it): AppKit
    # deminiaturizes on its own.
    main._close_state["hidden"] = False
    assert d.applicationShouldHandleReopen_hasVisibleWindows_(None, False) is True
    restore.assert_not_called()


def test_app_activation_reopens_a_hidden_window():
    # Cmd+Tab back to Orbital fires no reopen event; activation does.
    restore = MagicMock()
    d = main._reopen_delegate_class(_FakeDelegateBase, restore)()
    d.applicationDidBecomeActive_(None)
    restore.assert_not_called()
    main._close_state["hidden"] = True
    d.applicationDidBecomeActive_(None)
    restore.assert_called_once_with()


def test_open_window_installs_the_reopen_handler_before_the_run_loop():
    source = inspect.getsource(main.open_window)
    assert "_install_macos_reopen_handler(" in source
    # The real call, not the docstring that mentions webview.start() earlier.
    assert source.index("_install_macos_reopen_handler(") < source.index("webview.start(icon=")
    # The closing hook delegates to the module-level decision tested above.
    assert "_handle_window_close(" in source
    assert "miniaturize_" not in source


def test_reopen_handler_patches_pywebviews_app_delegate(monkeypatch):
    """pywebview builds ``BrowserView.AppDelegate`` inside ``webview.start``;
    swapping the class attribute beforehand is the only seam, and it must be
    a subclass so ``applicationShouldTerminate_`` keeps working."""
    import sys
    import types

    monkeypatch.setattr(main.sys, "platform", "darwin")
    cocoa = types.ModuleType("webview.platforms.cocoa")

    class BrowserView:
        class AppDelegate:
            pass

    cocoa.BrowserView = BrowserView
    platforms = types.ModuleType("webview.platforms")
    platforms.cocoa = cocoa
    webview = types.ModuleType("webview")
    webview.platforms = platforms
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setitem(sys.modules, "webview.platforms", platforms)
    monkeypatch.setitem(sys.modules, "webview.platforms.cocoa", cocoa)
    monkeypatch.setattr(main, "_REOPEN_DELEGATE_CLASS", None)

    original = BrowserView.AppDelegate
    main._install_macos_reopen_handler(lambda: None)
    assert BrowserView.AppDelegate is not original
    assert issubclass(BrowserView.AppDelegate, original)
    assert hasattr(BrowserView.AppDelegate, "applicationShouldHandleReopen_hasVisibleWindows_")
