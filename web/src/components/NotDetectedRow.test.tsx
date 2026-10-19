// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

// @vitest-environment jsdom

import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { LocaleProvider } from '../i18n/LocaleContext';

import NotDetectedRow from './NotDetectedRow';

// The hint names the PATH fix for the OS the UI runs on (the daemon's machine
// in every topology where the row is actionable).
const platform = vi.hoisted(() => ({ value: 'MacIntel' }));
vi.mock('../utils/clientPlatform', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../utils/clientPlatform')>();
  return {
    ...actual,
    notDetectedHintKey: () => actual.notDetectedHintKey({ platform: platform.value }),
  };
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  platform.value = 'MacIntel';
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

  it('on Windows, points at the user PATH and a restart, never ~/.zprofile', () => {
    platform.value = 'Win32';
    render(<NotDetectedRow slug="codex" name="Codex" />);
    const row = screen.getByTestId('sub-agent-not-detected-codex');
    expect(row).toHaveTextContent("Codex is not on Orbital's PATH.");
    expect(row).toHaveTextContent('user PATH');
    expect(row).toHaveTextContent('restart Orbital');
    expect(row).not.toHaveTextContent('zprofile');
    expect(row).not.toHaveTextContent('macOS');
  });

  it('on Windows in Chinese, says 系统环境变量 and 重启 Orbital', () => {
    platform.value = 'Win32';
    localStorage.setItem('orbital.locale', 'zh');
    render(
      <LocaleProvider>
        <NotDetectedRow slug="codex" name="Codex" />
      </LocaleProvider>,
    );
    const row = screen.getByTestId('sub-agent-not-detected-codex');
    expect(row).toHaveTextContent('系统环境变量');
    expect(row).toHaveTextContent('重启 Orbital');
    expect(row).not.toHaveTextContent('zprofile');
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
