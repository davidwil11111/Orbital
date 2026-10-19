// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

import { useCallback, useSyncExternalStore } from 'react';

/**
 * Light / dark ("Graphite") theme preference, per device.
 *
 * The theme itself is nothing but `data-theme="dark"` on <html>: index.css
 * redefines the colour tokens under that attribute, and the `dark:` variant
 * keys off it. This module owns the preference and keeps the attribute in
 * step with it.
 *
 * The FIRST stamp happens in an inline script in index.html, before React
 * mounts, so a dark user never sees a white frame. That script also follows
 * live OS appearance changes while the preference is "system", so the theme
 * tracks the OS even when nothing here is mounted. Keep its storage key and
 * resolution rule in step with this file.
 */

export type ThemePreference = 'system' | 'light' | 'dark';
export type ResolvedTheme = 'light' | 'dark';

/** Mirrors `orbital.locale` — per-device, no backend. */
export const THEME_STORAGE_KEY = 'orbital.theme';

const DARK_QUERY = '(prefers-color-scheme: dark)';

export function readThemePreference(): ThemePreference {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored;
  } catch {
    /* localStorage unavailable (private mode) — use default */
  }
  return 'system';
}

function systemPrefersDark(): boolean {
  try {
    return window.matchMedia(DARK_QUERY).matches;
  } catch {
    return false;
  }
}

export function resolveTheme(pref: ThemePreference, systemDark: boolean): ResolvedTheme {
  if (pref === 'system') return systemDark ? 'dark' : 'light';
  return pref;
}

function applyTheme(resolved: ResolvedTheme) {
  document.documentElement.dataset.theme = resolved;
}

// ------------------------------------------------------------------- store
// One module-level snapshot shared by every hook instance, so two mounted
// pickers can never disagree.

const listeners = new Set<() => void>();
let snapshot: { preference: ThemePreference; resolved: ResolvedTheme } | null = null;

function computeSnapshot() {
  const preference = readThemePreference();
  return { preference, resolved: resolveTheme(preference, systemPrefersDark()) };
}

function getSnapshot() {
  if (!snapshot) snapshot = computeSnapshot();
  return snapshot;
}

function refresh() {
  const next = computeSnapshot();
  if (!snapshot || next.preference !== snapshot.preference || next.resolved !== snapshot.resolved) {
    snapshot = next;
    applyTheme(next.resolved);
    listeners.forEach((l) => l());
  }
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) {
    try {
      window.matchMedia(DARK_QUERY).addEventListener('change', refresh);
    } catch {
      /* matchMedia unavailable */
    }
    window.addEventListener('storage', onStorage);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      try {
        window.matchMedia(DARK_QUERY).removeEventListener('change', refresh);
      } catch {
        /* matchMedia unavailable */
      }
      window.removeEventListener('storage', onStorage);
    }
  };
}

// Another window of the same origin changed the preference.
function onStorage(e: StorageEvent) {
  if (e.key === THEME_STORAGE_KEY) refresh();
}

export function setThemePreference(pref: ThemePreference) {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, pref);
  } catch {
    /* ignore persistence failure — still apply for this page load */
  }
  const resolved = resolveTheme(pref, systemPrefersDark());
  snapshot = { preference: pref, resolved };
  applyTheme(resolved);
  listeners.forEach((l) => l());
}

export function useTheme() {
  const { preference, resolved } = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const setPreference = useCallback((pref: ThemePreference) => setThemePreference(pref), []);
  return { preference, resolved, setPreference };
}

/** Test-only: drop the cached snapshot so a test can re-seed localStorage. */
export function __resetThemeStoreForTests() {
  snapshot = null;
}
