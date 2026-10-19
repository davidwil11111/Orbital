// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

import { afterEach, describe, expect, it, vi } from 'vitest';
import { withViewTransition } from './viewTransition';

// Loosely typed: the DOM lib's own startViewTransition signature would force
// the mocks to return a full ViewTransition.
const doc = document as unknown as { startViewTransition?: unknown };

function mockReducedMotion(reduce: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({ matches: reduce && q.includes('reduce') }));
}

afterEach(() => {
  delete doc.startViewTransition;
  vi.unstubAllGlobals();
});

describe('withViewTransition', () => {
  it('runs the update directly where the API is missing', () => {
    mockReducedMotion(false);
    const update = vi.fn();
    withViewTransition(update);
    expect(update).toHaveBeenCalledTimes(1);
  });

  it('runs the update inside startViewTransition where supported', () => {
    mockReducedMotion(false);
    const start = vi.fn((cb: () => void) => cb());
    doc.startViewTransition = start;
    const update = vi.fn();
    withViewTransition(update);
    expect(start).toHaveBeenCalledTimes(1);
    expect(update).toHaveBeenCalledTimes(1);
  });

  it('skips the transition when the user prefers reduced motion', () => {
    mockReducedMotion(true);
    const start = vi.fn();
    doc.startViewTransition = start;
    const update = vi.fn();
    withViewTransition(update);
    expect(start).not.toHaveBeenCalled();
    expect(update).toHaveBeenCalledTimes(1);
  });
});
