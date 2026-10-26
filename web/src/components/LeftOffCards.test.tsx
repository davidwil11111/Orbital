// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

// @vitest-environment jsdom

import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const apiMock = vi.hoisted(() => vi.fn());
vi.mock('../config', () => ({ api: apiMock }));

import LeftOffCards from './LeftOffCards';

const HOUR = 3600_000;
const iso = (msAgo: number) => new Date(Date.now() - msAgo).toISOString();

let leftOff: unknown;
const telemetry: Array<{ event: string; fields: Record<string, unknown> }> = [];

beforeEach(() => {
  telemetry.length = 0;
  apiMock.mockReset();
  apiMock.mockImplementation(async (path: string, opts?: { method?: string; body?: string }) => {
    if (path === '/api/v2/telemetry/event') {
      telemetry.push(JSON.parse(opts?.body ?? '{}'));
      return undefined;
    }
    if (path === '/api/v2/projects/p1/left-off') return leftOff;
    return {};
  });
});
afterEach(() => cleanup());

const AGENTS = [{ slug: 'codex', name: 'Codex' }, { slug: 'claude-code', name: 'Claude Code' }];

function renderCards(onOpen = vi.fn()) {
  render(
    <LeftOffCards
      projectId="p1"
      agents={AGENTS}
      onOpen={onOpen}
      fallback={<div data-testid="examples">examples</div>}
    />,
  );
  return onOpen;
}

const LAST = {
  session_id: 'imp_codex_ab12',
  agent: 'codex',
  user_text: 'add a hide list to the agent picker',
  at: iso(2 * HOUR),
  git: { branch: 'feat/hide', session_branch: null, uncommitted: 3, commits_since: 0 },
};

describe('LeftOffCards', () => {
  it('falls back to the example prompts for a project with no sessions', async () => {
    leftOff = { last_time: null, unfinished: [] };
    renderCards();
    await waitFor(() => expect(screen.getByTestId('examples')).toBeTruthy());
    expect(screen.queryByTestId('left-off')).toBeNull();
    // Tells the tour the answer is in and has no cards.
    expect(document.querySelector('[data-left-off-state="none"]')).toBeTruthy();
    expect(telemetry.filter((e) => e.event === 'leftoff_shown')).toHaveLength(0);
  });

  it('falls back to the examples when the route fails (older daemon)', async () => {
    apiMock.mockImplementation(async () => { throw new Error('404'); });
    renderCards();
    await waitFor(() => expect(screen.getByTestId('examples')).toBeTruthy());
  });

  it('renders nothing while loading — never a flash of examples swapped for cards', () => {
    apiMock.mockImplementation(() => new Promise(() => {}));
    const { container } = render(
      <LeftOffCards projectId="p1" agents={AGENTS} onOpen={vi.fn()} fallback={<div data-testid="examples" />} />,
    );
    expect(screen.queryByTestId('examples')).toBeNull();
    expect(container.querySelector('[data-left-off-state="loading"]')).toBeTruthy();
  });

  it('shows the Last time card with the message verbatim, its agent, and only the non-zero git chips', async () => {
    leftOff = { last_time: LAST, unfinished: [] };
    renderCards();
    const card = await screen.findByTestId('left-off-last');
    expect(screen.queryByTestId('examples')).toBeNull();
    expect(card.textContent).toContain('You to Codex · 2h ago');
    expect(card.textContent).toContain('“add a hide list to the agent picker”');
    expect(screen.getByTestId('left-off-chip-branch').textContent).toBe('feat/hide');
    expect(screen.getByTestId('left-off-chip-uncommitted').textContent).toBe('3 uncommitted files');
    expect(screen.queryByTestId('left-off-chip-commits')).toBeNull();
    expect(screen.queryByTestId('left-off-unfinished')).toBeNull();
    await waitFor(() => expect(telemetry.filter((e) => e.event === 'leftoff_shown')).toHaveLength(1));
  });

  it('names the Orbital agent when the session ran on it', async () => {
    leftOff = { last_time: { ...LAST, agent: null, git: null }, unfinished: [] };
    renderCards();
    expect((await screen.findByTestId('left-off-last')).textContent).toContain('You to Orbital');
    expect(screen.queryByTestId('left-off-chip-branch')).toBeNull();
  });

  it('lists up to three unfinished rows with their cause pills; usage limit names the reset time', async () => {
    const resets = new Date(Date.now() + 3 * HOUR);
    leftOff = {
      last_time: null,
      unfinished: [
        { session_id: 'u1', agent: 'claude-code', user_text: 'refactor the parser', at: iso(HOUR), cause: 'usage_limit', resets_at: resets.toISOString() },
        { session_id: 'u2', agent: 'codex', user_text: 'fix the build', at: iso(5 * HOUR), cause: 'model_unavailable', resets_at: null },
        { session_id: 'u3', agent: 'codex', user_text: 'deploy', at: iso(30 * HOUR), cause: 'interrupted', resets_at: null },
        { session_id: 'u4', agent: 'codex', user_text: 'never shown', at: iso(40 * HOUR), cause: 'no_reply', resets_at: null },
      ],
    };
    renderCards();
    const rows = await screen.findAllByTestId(/^left-off-unfinished-row-/);
    expect(rows).toHaveLength(3);
    expect(screen.queryByTestId('left-off-last')).toBeNull();
    const pill = screen.getByTestId('left-off-cause-u1');
    expect(pill.textContent).toMatch(/^Usage limit · back at /);
    expect(pill.className).toContain('warning');
    expect(screen.getByTestId('left-off-cause-u2').textContent).toBe('Model not available');
    expect(screen.getByTestId('left-off-cause-u2').className).not.toContain('warning');
    expect(screen.getByTestId('left-off-cause-u3').textContent).toBe('Stopped');
    expect(rows[0].textContent).toContain('Claude Code · 1h ago');
    expect(rows[0].textContent).toContain('“refactor the parser”');
  });

  it('a usage limit with no reset time is just "Usage limit"', async () => {
    leftOff = {
      last_time: null,
      unfinished: [{ session_id: 'u1', agent: 'codex', user_text: 'x', at: iso(HOUR), cause: 'usage_limit', resets_at: null }],
    };
    renderCards();
    expect((await screen.findByTestId('left-off-cause-u1')).textContent).toBe('Usage limit');
  });

  it('each card is one button that opens its session and reports the open', async () => {
    leftOff = {
      last_time: LAST,
      unfinished: [{ session_id: 'u1', agent: 'codex', user_text: 'x', at: iso(HOUR), cause: 'network_auth', resets_at: null }],
    };
    const onOpen = renderCards();
    const last = await screen.findByTestId('left-off-last');
    expect(last.tagName).toBe('BUTTON');
    fireEvent.click(last);
    expect(onOpen).toHaveBeenCalledWith('imp_codex_ab12');
    fireEvent.click(screen.getByTestId('left-off-unfinished-row-u1'));
    expect(onOpen).toHaveBeenLastCalledWith('u1');
    await waitFor(() => {
      const opened = telemetry.filter((e) => e.event === 'leftoff_opened').map((e) => e.fields);
      expect(opened).toEqual([{ kind: 'last_time' }, { kind: 'unfinished', cause: 'network_auth' }]);
    });
  });

  it('carries the tour anchor on the cards', async () => {
    leftOff = { last_time: LAST, unfinished: [] };
    renderCards();
    expect((await screen.findByTestId('left-off')).getAttribute('data-tour')).toBe('left-off');
  });
});
