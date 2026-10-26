// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

// @vitest-environment jsdom

import { renderHook, waitFor, act, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const apiMock = vi.hoisted(() => vi.fn());
vi.mock('../config', () => ({ api: apiMock }));

import { useOnboardingScan } from './useOnboardingScan';

const HISTORY = { status: 'scanning', sessions_total: 10, by_agent: { codex: 10 } };
const CAND = { source: 'codex', name: 'a', path: '/a', session_count: 3, last_activity: null };

beforeEach(() => apiMock.mockReset());
afterEach(() => cleanup());

const calls = (path: string) => apiMock.mock.calls.filter(([p]) => p === path).length;

describe('useOnboardingScan', () => {
  it('asks for the history first (that starts the scan), then the importable list', async () => {
    apiMock.mockImplementation(async (path: string) =>
      path === '/api/v2/onboarding/history' ? HISTORY : { status: 'ready', candidates: [CAND], groups: [] });
    const { result } = renderHook(() => useOnboardingScan({ pollMs: 10 }));
    await waitFor(() => expect(result.current.settled).toBe(true));
    expect(apiMock.mock.calls.map(([p]) => p)).toEqual([
      '/api/v2/onboarding/history',
      '/api/v2/onboarding/importable-projects',
    ]);
    expect(result.current.history?.sessions_total).toBe(10);
    expect(result.current.importable?.candidates).toHaveLength(1);
  });

  it('polls while scanning and stops once ready, keeping the groups that arrive', async () => {
    let n = 0;
    apiMock.mockImplementation(async (path: string) => {
      if (path === '/api/v2/onboarding/history') return HISTORY;
      n += 1;
      return n < 3
        ? { status: 'scanning', candidates: [CAND], groups: [] }
        : { status: 'ready', candidates: [CAND], groups: [{ id: 'g', session_count: 2, last_activity: null, by_agent: {}, reasons: [], suggested_folder: null, members: [] }] };
    });
    const { result } = renderHook(() => useOnboardingScan({ pollMs: 10 }));
    // Candidates are usable before the scan is done.
    await waitFor(() => expect(result.current.importable?.candidates).toHaveLength(1));
    await waitFor(() => expect(result.current.settled).toBe(true));
    expect(calls('/api/v2/onboarding/importable-projects')).toBe(3);
    expect(result.current.importable?.groups).toHaveLength(1);
    await new Promise((r) => setTimeout(r, 40));
    expect(calls('/api/v2/onboarding/importable-projects')).toBe(3);
  });

  it('gives up polling after maxMs and settles with what it has', async () => {
    apiMock.mockImplementation(async (path: string) =>
      path === '/api/v2/onboarding/history' ? HISTORY : { status: 'scanning', candidates: [], groups: [] });
    const { result } = renderHook(() => useOnboardingScan({ pollMs: 10, maxMs: 60 }));
    await waitFor(() => expect(result.current.settled).toBe(true));
    const made = calls('/api/v2/onboarding/importable-projects');
    expect(made).toBeGreaterThan(1);
    await new Promise((r) => setTimeout(r, 40));
    expect(calls('/api/v2/onboarding/importable-projects')).toBe(made);
  });

  it('a failed list settles with an error, and retry starts over', async () => {
    let fail = true;
    apiMock.mockImplementation(async (path: string) => {
      if (path === '/api/v2/onboarding/history') throw new Error('older daemon');
      if (fail) throw new Error('boom');
      return { candidates: [CAND] }; // older daemon: no status, no groups
    });
    const { result } = renderHook(() => useOnboardingScan({ pollMs: 10 }));
    await waitFor(() => expect(result.current.error).toBe(true));
    expect(result.current.settled).toBe(true);
    expect(result.current.history).toBeNull();

    fail = false;
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.importable?.candidates).toHaveLength(1));
    expect(result.current.error).toBe(false);
    expect(result.current.settled).toBe(true);
    expect(result.current.importable?.groups).toEqual([]);
  });
});
