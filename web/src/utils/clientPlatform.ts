// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

/**
 * Spec 093 — the OS of the machine showing this UI, for "Reveal in Finder" /
 * "Show in File Explorer". The reveal itself runs in the daemon; in every
 * topology where it can work (the packaged app, a browser on localhost) the
 * client and the daemon are the same machine, so the client's OS names the
 * file manager that will open.
 */
import { isRelayMode } from '../config';
import type { StringKey } from '../i18n/strings';

export type ClientOS = 'mac' | 'windows' | 'other';

/** The slice of `navigator` read here; a parameter so tests can pass one. */
export interface NavigatorLike {
  platform?: string;
  userAgentData?: { platform?: string };
}

function currentNavigator(): NavigatorLike {
  return typeof navigator === 'undefined' ? {} : (navigator as NavigatorLike);
}

/**
 * `userAgentData.platform` ("macOS", "Windows") where the engine has it
 * (Chromium, WebView2); `navigator.platform` ("MacIntel", "Win32") otherwise
 * (WebKit, which the macOS app's pywebview window uses).
 */
export function clientOS(nav: NavigatorLike = currentNavigator()): ClientOS {
  const raw = (nav.userAgentData?.platform || nav.platform || '').toLowerCase();
  if (raw.startsWith('win')) return 'windows';
  if (raw.startsWith('mac')) return 'mac';
  return 'other';
}

export function isWindowsClient(nav?: NavigatorLike): boolean {
  return clientOS(nav) === 'windows';
}

/** "Show in File Explorer" on Windows, "Reveal in Finder" everywhere else. */
export function revealLabelKey(nav?: NavigatorLike): StringKey {
  return isWindowsClient(nav) ? 'fileExplorer.revealExplorer' : 'fileExplorer.revealFinder';
}

/**
 * The sub-agent "not detected" row's next step names the PATH fix for the OS
 * the UI runs on — the daemon's machine whenever the row is actionable (the
 * packaged app, a browser on localhost). `~/.zprofile` is macOS-only; Windows
 * reads the user PATH from the environment-variables dialog and Orbital only
 * picks a change up after a restart. Anything else gets the OS-neutral line.
 */
export function notDetectedHintKey(nav?: NavigatorLike): StringKey {
  switch (clientOS(nav)) {
    case 'mac':
      return 'subAgentCard.notDetectedHint.mac';
    case 'windows':
      return 'subAgentCard.notDetectedHint.windows';
    default:
      return 'subAgentCard.notDetectedHint.other';
  }
}

/**
 * Whether to offer the reveal actions at all. Never through the relay (the
 * file manager would open on the desktop, not in the user's hand), and never
 * on a client with no Finder / File Explorer (a phone on the LAN, Linux,
 * where the daemon answers 501).
 */
export function canRevealInFileManager(nav?: NavigatorLike): boolean {
  return !isRelayMode && clientOS(nav) !== 'other';
}
