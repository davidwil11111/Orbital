// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

import { describe, it, expect } from 'vitest';
import {
  agentDisplayName,
  buildImportRequest,
  folderKey,
  groupKey,
  hasImportables,
  hasLeftOff,
  landingProjectId,
  parseLeftOff,
  uncheckedCount,
  type ImportCandidate,
  type ImportGroup,
  type ImportableResponse,
} from './onboarding';

function cand(over: Partial<ImportCandidate> = {}): ImportCandidate {
  return {
    source: 'claude-code',
    name: 'alpha',
    path: '/Users/x/alpha',
    session_count: 12,
    last_activity: '2026-10-20T08:00:00+00:00',
    already_imported: false,
    kind: 'folder',
    minor: false,
    is_git: true,
    by_agent: { 'claude-code': 12 },
    ...over,
  };
}

function group(over: Partial<ImportGroup> = {}): ImportGroup {
  return {
    id: 'grp_1',
    session_count: 5,
    last_activity: '2026-10-21T08:00:00+00:00',
    by_agent: { codex: 5 },
    reasons: [{ kind: 'same_skill', value: 'pptx', count: 3 }],
    suggested_folder: '/Users/x/decks',
    members: [{ agent: 'codex', native_id: 'a' }],
    ...over,
  };
}

const DATA: ImportableResponse = {
  status: 'ready',
  candidates: [
    cand({ name: 'alpha', path: '/Users/x/alpha' }),
    cand({ name: 'beta', path: '/Users/x/beta', minor: true, is_git: false, session_count: 2 }),
    cand({ name: 'gamma', path: '/Users/x/gamma', already_imported: true }),
  ],
  groups: [
    group({ id: 'grp_1' }),
    group({ id: 'grp_2', suggested_folder: null }),
  ],
};

describe('buildImportRequest — what "Import N projects" sends', () => {
  it('defaults: major folders checked, minor unchecked, imported excluded, groups need a folder', () => {
    const req = buildImportRequest(DATA, {}, {});
    expect(req.folders).toEqual(['/Users/x/alpha']);
    // grp_2 has no folder: checked, but neither sent nor counted.
    expect(req.groups).toEqual([{ id: 'grp_1', workspace: '/Users/x/decks' }]);
  });

  it('honours the user\'s ticks and a chosen folder', () => {
    const req = buildImportRequest(
      DATA,
      { [folderKey('/Users/x/alpha')]: false, [folderKey('/Users/x/beta')]: true, [groupKey('grp_1')]: false },
      { grp_2: '/Users/x/new-folder' },
    );
    expect(req.folders).toEqual(['/Users/x/beta']);
    expect(req.groups).toEqual([{ id: 'grp_2', workspace: '/Users/x/new-folder' }]);
  });

  it('never sends an already-imported folder even if something ticked it', () => {
    const req = buildImportRequest(DATA, { [folderKey('/Users/x/gamma')]: true }, {});
    expect(req.folders).not.toContain('/Users/x/gamma');
  });

  it('tolerates a response without groups (older daemon)', () => {
    const req = buildImportRequest({ candidates: [cand()] }, {}, {});
    expect(req).toEqual({ folders: ['/Users/x/alpha'], groups: [] });
  });
});

describe('uncheckedCount — projects_unchecked telemetry', () => {
  it('counts only items that were offered checked and are now unchecked', () => {
    expect(uncheckedCount(DATA, {})).toBe(0);
    expect(
      uncheckedCount(DATA, {
        [folderKey('/Users/x/alpha')]: false,
        [groupKey('grp_2')]: false,
        // minor folder left unchecked is the default, not an uncheck
        [folderKey('/Users/x/beta')]: false,
      }),
    ).toBe(2);
  });
});

describe('hasImportables — whether the Projects step shows at all', () => {
  it('false when there is nothing to import', () => {
    expect(hasImportables({ status: 'ready', candidates: [], groups: [] })).toBe(false);
    expect(hasImportables({ status: 'ready', candidates: [cand({ already_imported: true })], groups: [] })).toBe(false);
  });
  it('true with a candidate or a group', () => {
    expect(hasImportables({ status: 'ready', candidates: [cand()], groups: [] })).toBe(true);
    expect(hasImportables({ status: 'ready', candidates: [], groups: [group()] })).toBe(true);
  });
});

describe('landingProjectId — where Finish lands', () => {
  it('picks the created project whose source was most recently active', () => {
    const req = { folders: ['/Users/x/alpha'], groups: [{ id: 'grp_1', workspace: '/Users/x/decks' }] };
    const result = {
      created: [
        { project_id: 'p_alpha', name: 'alpha', workspace: '/Users/x/alpha', from: 'folder' as const },
        { project_id: 'p_decks', name: 'decks', workspace: '/Users/x/decks', from: 'group' as const },
      ],
      skipped: [],
    };
    // grp_1 (Oct 21) is newer than alpha (Oct 20).
    expect(landingProjectId(result, req, DATA)).toBe('p_decks');
  });

  it('null when nothing was created', () => {
    expect(landingProjectId({ created: [], skipped: [] }, { folders: [], groups: [] }, DATA)).toBeNull();
  });

  it('falls back to the first created project when no activity is known', () => {
    const result = {
      created: [{ project_id: 'p_z', name: 'z', workspace: '/Users/x/z', from: 'folder' as const }],
      skipped: [],
    };
    expect(landingProjectId(result, { folders: ['/Users/x/z'], groups: [] }, DATA)).toBe('p_z');
  });
});

describe('left-off parsing and selection', () => {
  it('an empty project (or a junk body) has nothing to show', () => {
    expect(hasLeftOff(parseLeftOff({ last_time: null, unfinished: [] }))).toBe(false);
    expect(hasLeftOff(parseLeftOff([]))).toBe(false);
    expect(hasLeftOff(parseLeftOff(undefined))).toBe(false);
  });

  it('shows with only a last-time card or only unfinished rows', () => {
    const last = { session_id: 's', agent: 'codex', user_text: 'hi', at: '2026-10-26T10:00:00Z', git: null };
    expect(hasLeftOff(parseLeftOff({ last_time: last, unfinished: [] }))).toBe(true);
    const row = { session_id: 'u', agent: null, user_text: 'x', at: '2026-10-26T10:00:00Z', cause: 'no_reply', resets_at: null };
    expect(hasLeftOff(parseLeftOff({ last_time: null, unfinished: [row] }))).toBe(true);
  });

  it('caps unfinished at three and drops malformed rows', () => {
    const row = (i: number) => ({ session_id: `u${i}`, agent: 'codex', user_text: 't', at: '2026-10-26T10:00:00Z', cause: 'interrupted', resets_at: null });
    const parsed = parseLeftOff({ last_time: null, unfinished: [row(1), { bogus: true }, row(2), row(3), row(4)] });
    expect(parsed?.unfinished.map((r) => r.session_id)).toEqual(['u1', 'u2', 'u3']);
  });
});

describe('agentDisplayName', () => {
  it('prefers the installed agent list, then the known history agents, then the slug', () => {
    expect(agentDisplayName('codex', [{ slug: 'codex', name: 'OpenAI Codex' }])).toBe('OpenAI Codex');
    expect(agentDisplayName('kimi-code')).toBe('Kimi Code');
    expect(agentDisplayName('minimax-code')).toBe('MiniMax Code');
    expect(agentDisplayName('mystery')).toBe('mystery');
    expect(agentDisplayName(null)).toBe('Orbital');
  });
});
