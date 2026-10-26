// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Spec 134 Part B (P1): background job groups render with the fanout card.
// The card is built from the `shell(background=true)` tool results'
// `_meta.background_job` (one card per group, the jobs as rows) or, for a
// Codex/Pi worker's launcher job, from the daemon's `job_started` row. The
// join / check-in / stop rows render as plain system rows (their
// display_content) and backfill the card's row statuses.

import { describe, expect, it } from 'vitest';
import { transformChatHistory } from './chatTransform';
import type { ChatMessage } from '../types';

const TS = '2026-10-27T10:00:00Z';
const TS2 = '2026-10-27T10:00:01Z';
const TS3 = '2026-10-27T10:05:00Z';

function bgCall(ids: string[]): ChatMessage {
  return {
    role: 'assistant', content: null, source: 'management', timestamp: TS,
    tool_calls: ids.map((id, i) => ({
      id, type: 'function' as const,
      function: { name: 'shell', arguments: JSON.stringify({ command: `cmd ${i}`, background: true }) },
    })),
  };
}

function bgResult(callId: string, jobId: string, command: string, group = 'grp_11112222'): ChatMessage {
  return {
    role: 'tool', source: 'management', timestamp: TS2, tool_call_id: callId,
    content: `Started background job ${jobId} (pid 1) in group ${group}.`,
    _meta: {
      yield_after_batch: true,
      background_job: { job_id: jobId, group_id: group, project_id: 'proj_1',
                        session_id: 's', command, pid: 1, started_at_ms: 1 },
    },
  };
}

function jobRow(event: string, display: string, jobs: Array<Record<string, unknown>>,
                extra: Record<string, unknown> = {}): ChatMessage {
  return {
    role: 'system', source: 'daemon', timestamp: TS3, content: `${display}\n(agent-facing tail…)`,
    _meta: { event, display_content: display, job_group: { group_id: 'grp_11112222', jobs }, ...extra },
  };
}

describe('background job cards', () => {
  it('two background calls in one message make one card with two job rows', () => {
    const items = transformChatHistory([
      bgCall(['c1', 'c2']),
      bgResult('c1', 'job_aaaa0001', 'npm run build'),
      bgResult('c2', 'job_aaaa0002', 'pytest -q'),
    ]);
    const cards = items.filter((it) => it.type === 'fanout_card');
    expect(cards).toHaveLength(1);
    const card = cards[0] as Extract<typeof items[number], { type: 'fanout_card' }>;
    expect(card.fanout_id).toBe('grp_11112222');
    expect(card.tasks).toEqual([
      { handle: 'job_aaaa0001', label: 'npm run build', job: { project_id: 'proj_1', job_id: 'job_aaaa0001', agent: null } },
      { handle: 'job_aaaa0002', label: 'pytest -q', job: { project_id: 'proj_1', job_id: 'job_aaaa0002', agent: null } },
    ]);
    // the shell calls stay visible in the capsule before the card
    const capsuleIdx = items.findIndex((it) => it.type === 'agent_run');
    expect(capsuleIdx).toBeGreaterThanOrEqual(0);
    expect(capsuleIdx).toBeLessThan(items.indexOf(card));
  });

  it('a join row renders as a system row and backfills the row statuses', () => {
    const items = transformChatHistory([
      bgCall(['c1', 'c2']),
      bgResult('c1', 'job_aaaa0001', 'npm run build'),
      bgResult('c2', 'job_aaaa0002', 'pytest -q'),
      jobRow('job_terminal', '[Background jobs grp_11112222] 1 of 2 background jobs did not succeed.', [
        { job_id: 'job_aaaa0001', status: 'completed', command: 'npm run build' },
        { job_id: 'job_aaaa0002', status: 'error', command: 'pytest -q' },
      ]),
    ]);
    const card = items.find((it) => it.type === 'fanout_card') as Extract<typeof items[number], { type: 'fanout_card' }>;
    expect(card.statuses).toEqual({ job_aaaa0001: 'completed', job_aaaa0002: 'error' });
    expect(card.completedAtMs).toBe(Date.parse(TS3));
    const summary = items.find((it) => it.type === 'fanout_summary');
    expect(summary).toMatchObject({
      fanout_id: 'grp_11112222',
      content: '[Background jobs grp_11112222] 1 of 2 background jobs did not succeed.',
    });
  });

  it('a check-in leaves the card running and shows its line', () => {
    const items = transformChatHistory([
      bgCall(['c1']),
      bgResult('c1', 'job_aaaa0001', 'train.py'),
      jobRow('job_checkin', '[Background jobs grp_11112222] Check-in: 1 job still running.', [
        { job_id: 'job_aaaa0001', status: 'running', command: 'train.py' },
      ]),
    ]);
    const card = items.find((it) => it.type === 'fanout_card') as Extract<typeof items[number], { type: 'fanout_card' }>;
    expect(card.statuses).toEqual({ job_aaaa0001: 'running' });
    expect(card.completedAtMs).toBeUndefined();
    expect(items.filter((it) => it.type === 'fanout_summary')).toHaveLength(1);
  });

  it("a worker's launcher job gets its card from the job_started row", () => {
    const items = transformChatHistory([
      jobRow('job_started', 'codex started a background job: job_c0de0001 `make`', [
        { job_id: 'job_c0de0001', status: 'running', command: 'make', project_id: 'proj_1', agent: 'codex' },
      ], { suppress_wake: true }),
    ]);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      type: 'fanout_card', fanout_id: 'grp_11112222',
      tasks: [{ handle: 'job_c0de0001', label: 'make', job: { project_id: 'proj_1', job_id: 'job_c0de0001', agent: 'codex' } }],
    });
  });

  it('the stopped-because-Orbital-closed note is a visible row', () => {
    const items = transformChatHistory([
      jobRow('job_note', 'Background job job_aaaa0001 (`train.py`) was stopped because Orbital closed.', [
        { job_id: 'job_aaaa0001', status: 'interrupted', command: 'train.py' },
      ], { suppress_wake: true, kind: 'job_stopped' }),
    ]);
    expect(items).toEqual([expect.objectContaining({
      type: 'fanout_summary',
      content: 'Background job job_aaaa0001 (`train.py`) was stopped because Orbital closed.',
    })]);
  });

  it('a foreground shell result is untouched', () => {
    const items = transformChatHistory([
      bgCall(['c1']),
      { role: 'tool', source: 'management', timestamp: TS2, tool_call_id: 'c1', content: 'Exit code: 0\nhi' },
    ]);
    expect(items.some((it) => it.type === 'fanout_card')).toBe(false);
  });
});
