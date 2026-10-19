// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';

import AppearanceSection from './AppearanceSection';
import { THEME_STORAGE_KEY, __resetThemeStoreForTests } from '../hooks/useTheme';

type ChangeListener = (e: { matches: boolean }) => void;
let systemDark = false;
let mqlListeners: ChangeListener[] = [];

function stubMatchMedia() {
  vi.stubGlobal('matchMedia', (query: string) => ({
    get matches() { return systemDark; },
    media: query,
    addEventListener: (_: string, l: ChangeListener) => { mqlListeners.push(l); },
    removeEventListener: (_: string, l: ChangeListener) => {
      mqlListeners = mqlListeners.filter((x) => x !== l);
    },
  }));
}

function radio(value: string) {
  return screen.getByTestId(`appearance-option-${value}`).querySelector('input') as HTMLInputElement;
}

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  systemDark = false;
  mqlListeners = [];
  stubMatchMedia();
  __resetThemeStoreForTests();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('AppearanceSection', () => {
  it('renders three radio options with System selected by default', () => {
    render(<AppearanceSection />);
    expect(screen.getAllByRole('radio')).toHaveLength(3);
    expect(radio('system').checked).toBe(true);
    expect(radio('light').checked).toBe(false);
    expect(radio('dark').checked).toBe(false);
    expect(screen.getByText(/System follows your computer/)).toBeTruthy();
  });

  it('picking Dark stores the preference and stamps data-theme', () => {
    render(<AppearanceSection />);
    fireEvent.click(radio('dark'));
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(radio('dark').checked).toBe(true);
  });

  it('restores a stored preference', () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'light');
    systemDark = true;
    render(<AppearanceSection />);
    expect(radio('light').checked).toBe(true);
  });

  it('System follows the OS appearance live', () => {
    render(<AppearanceSection />);
    systemDark = true;
    mqlListeners.forEach((l) => l({ matches: true }));
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('an explicit Light ignores a dark OS', () => {
    systemDark = true;
    render(<AppearanceSection />);
    fireEvent.click(radio('light'));
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('survives localStorage throwing', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    render(<AppearanceSection />);
    expect(radio('system').checked).toBe(true);
    fireEvent.click(radio('dark'));
    expect(document.documentElement.dataset.theme).toBe('dark');
    vi.restoreAllMocks();
  });
});
