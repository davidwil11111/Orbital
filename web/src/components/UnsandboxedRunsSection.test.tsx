// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';

import { UnsandboxedRunsSection, allowlistExecutable } from './UnsandboxedRunsSection';
import type { PendingUnsandboxedRun } from '../types';

afterEach(() => cleanup());

function run(over: Partial<PendingUnsandboxedRun>): PendingUnsandboxedRun {
  return {
    id: 'r1',
    command: 'cmd /c curl.exe https://example.com',
    reason: 'Sandbox denied network access',
    created_at: '2026-10-19T00:00:00Z',
    session_id: null,
    ...over,
  };
}

function renderSection(pendingRuns: PendingUnsandboxedRun[], onResolve = vi.fn(async () => {})) {
  render(
    <UnsandboxedRunsSection
      executables={[]}
      pendingRuns={pendingRuns}
      onRemoveExecutable={() => {}}
      onResolve={onResolve}
    />,
  );
  return onResolve;
}

describe('allowlistExecutable', () => {
  it('prefers the backend field, including "" (not allowlistable)', () => {
    expect(allowlistExecutable('cmd /c curl.exe x', 'curl.exe')).toBe('curl.exe');
    expect(allowlistExecutable('git pull && npm i', '')).toBe('');
  });

  it('falls back to the first word only when the field is absent', () => {
    expect(allowlistExecutable('git push origin main')).toBe('git');
    expect(allowlistExecutable('  ls -la ', undefined)).toBe('ls');
  });
});

describe('UnsandboxedRunsSection — parked runs use the backend executable', () => {
  it('labels "Always allow" with the executable field and answers always', async () => {
    const onResolve = renderSection([run({ executable: 'curl.exe' })]);
    const btn = screen.getByRole('button', { name: 'Always allow curl.exe in this project' });
    expect(screen.queryByRole('button', { name: 'Always allow cmd in this project' })).toBeNull();
    fireEvent.click(btn);
    await waitFor(() => expect(onResolve).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'r1' }), 'always',
    ));
  });

  it('hides "Always allow" when the command cannot be allowlisted', () => {
    renderSection([run({ command: 'git pull && npm i', executable: '' })]);
    expect(screen.queryByRole('button', { name: /^Always allow/ })).toBeNull();
    expect(screen.getByRole('button', { name: 'Allow once' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Turn off Safe mode for this project' })).toBeTruthy();
  });

  it('falls back to the first word for an older daemon (field absent)', () => {
    renderSection([run({})]);
    expect(screen.getByRole('button', { name: 'Always allow cmd in this project' })).toBeTruthy();
  });
});
