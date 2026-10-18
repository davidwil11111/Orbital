// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

// @vitest-environment jsdom

import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { LocaleProvider } from '../i18n/LocaleContext';

import NotDetectedRow from './NotDetectedRow';

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe('NotDetectedRow (spec 115 D4)', () => {
  it('names the agent, says it is not detected, and gives the next step', () => {
    render(<NotDetectedRow slug="codex" name="Codex" />);
    const row = screen.getByTestId('sub-agent-not-detected-codex');
    expect(row).toHaveTextContent('Codex');
    expect(row).toHaveTextContent('Not detected');
    expect(row).toHaveTextContent("Codex is not on Orbital's PATH.");
    expect(row).toHaveTextContent('click Refresh');
    expect(row).toHaveTextContent('~/.zprofile');
  });

  it('offers no login, install or config controls', () => {
    render(<NotDetectedRow slug="codex" name="Codex" />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  });

  it('renders in Simplified Chinese', () => {
    localStorage.setItem('orbital.locale', 'zh');
    render(
      <LocaleProvider>
        <NotDetectedRow slug="codex" name="Codex" />
      </LocaleProvider>,
    );
    const row = screen.getByTestId('sub-agent-not-detected-codex');
    expect(row).toHaveTextContent('未检测到');
    expect(row).toHaveTextContent('Codex');
  });
});
