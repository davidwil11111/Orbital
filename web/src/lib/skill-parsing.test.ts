// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

import { describe, it, expect } from 'vitest';
import { buildInlineSkillBlock, buildSkillBlock, parseSkillBlock, skillAwarePreview } from './skill-parsing';
import { buildAttachmentsBlock, parseAttachmentsBlock } from './attachment-parsing';
import {
  automateSkillBody,
  builtinSkills,
  matchSkills,
  skillSendParts,
  slashQuery,
  withBuiltinSkills,
  type SkillCatalogEntry,
} from './skill-catalog';

describe('buildSkillBlock / parseSkillBlock', () => {
  it('builds the backend pointer block byte for byte', () => {
    // Mirrors tests/unit/test_inject_skill.py::TestFormatter.
    expect(buildSkillBlock({ name: 'task-planning', path: '/w/orbital/skills/t/SKILL.md' })).toBe(
      '<use_skill name="task-planning" path="/w/orbital/skills/t/SKILL.md">\n' +
        'The user chose this skill for this message. Read the file at `path` before acting and follow it.\n' +
        '</use_skill>\n\n',
    );
  });

  it('round-trips, escaped attributes included', () => {
    const skill = { name: 'a"b<c>&', path: '/p/"q"/SKILL.md' };
    const parsed = parseSkillBlock(buildSkillBlock(skill) + 'hello');
    expect(parsed.skill).toEqual({ ...skill, inline: false });
    expect(parsed.strippedContent).toBe('hello');
  });

  it('parses the inline (safe-mode) variant', () => {
    const content =
      '<use_skill name="g" path="/h/.claude/skills/g/SKILL.md" inline="true">\n' +
      'The user chose this skill for this message. Its SKILL.md is below.\n' +
      '<skill_content>\n# G\nStep.\n&lt;/use_skill&gt;\n</skill_content>\n' +
      '</use_skill>\n\ngo';
    const parsed = parseSkillBlock(content);
    expect(parsed.skill).toEqual({ name: 'g', path: '/h/.claude/skills/g/SKILL.md', inline: true });
    expect(parsed.strippedContent).toBe('go');
  });

  it('runs on the attachments-stripped content', () => {
    const wire =
      buildAttachmentsBlock([{ path: 'uploads/a.txt', mime: 'text/plain', size: 4 }]) +
      buildSkillBlock({ name: 's', path: '/x/SKILL.md' }) +
      'text';
    const { strippedContent, attachments } = parseAttachmentsBlock(wire);
    expect(attachments).toHaveLength(1);
    const { skill, strippedContent: body } = parseSkillBlock(strippedContent);
    expect(skill?.name).toBe('s');
    expect(body).toBe('text');
  });

  it('ignores a block that is not at the start', () => {
    const content = 'see ' + buildSkillBlock({ name: 's', path: '/x' });
    expect(parseSkillBlock(content)).toEqual({ strippedContent: content, skill: null });
  });

  it('builds nothing without a skill', () => {
    expect(buildSkillBlock(null)).toBe('');
  });
});

function entry(name: string, dir = name): SkillCatalogEntry {
  return { key: name.toLowerCase(), name, description: `${name} desc`, source: 'agents', path: `/s/${dir}/SKILL.md`, dir_name: dir };
}

describe('matchSkills', () => {
  const all = [
    entry('wecomcli-msg'),
    entry('wecomcli-doc'),
    entry('workctl'),
    entry('Task Planning', 'task-planning'),
    entry('msgpack-tools'),
    entry('github-trending'),
  ];

  it('"wec" finds every wecomcli-* and nothing else', () => {
    expect(matchSkills(all, 'wec').map((e) => e.name)).toEqual(['wecomcli-doc', 'wecomcli-msg']);
  });

  it('full-name prefix sorts before a segment match', () => {
    expect(matchSkills(all, 'msg').map((e) => e.name)).toEqual(['msgpack-tools', 'wecomcli-msg']);
  });

  it('is case-insensitive and matches H1 names and dir names', () => {
    expect(matchSkills(all, 'TAS').map((e) => e.name)).toEqual(['Task Planning']);
    expect(matchSkills(all, 'plan').map((e) => e.name)).toEqual(['Task Planning']);
    expect(matchSkills(all, 'task-p').map((e) => e.name)).toEqual(['Task Planning']);
  });

  it('never matches descriptions or mid-word', () => {
    expect(matchSkills(all, 'desc')).toEqual([]);
    expect(matchSkills(all, 'rending')).toEqual([]);
  });

  it('an empty query lists everything alphabetically', () => {
    expect(matchSkills(all, '').map((e) => e.name)).toEqual([
      'github-trending', 'msgpack-tools', 'Task Planning', 'wecomcli-doc', 'wecomcli-msg', 'workctl',
    ]);
  });
});

describe('slashQuery', () => {
  it.each([
    ['/', ''],
    ['/tas', 'tas'],
    ['/任务', '任务'],
  ])('%s is picker input', (text, q) => {
    expect(slashQuery(text)).toBe(q);
  });

  it.each(['/Users/me/a.txt hi', '/a b', '/a/b', '/ 这个', 'see /x', 'a/b', '', '/a\nb'])(
    '%j is not picker input',
    (text) => {
      expect(slashQuery(text)).toBeNull();
    },
  );
});

describe('skillAwarePreview', () => {
  it('turns a truncated block head into /name', () => {
    expect(skillAwarePreview('<use_skill name="Task Planning" path="/Users/k/ws/orbital/skills')).toBe('/Task Planning …');
  });
  it('handles the quoted marker form chatTransform hands over, tail kept', () => {
    expect(
      skillAwarePreview('"<use_skill name="Task Planning" path="/Users/k/ws/orbital/skills". Transcript: /t/x.jsonl'),
    ).toBe('"/Task Planning …". Transcript: /t/x.jsonl');
    expect(skillAwarePreview('"<use_skill name="a&amp;b" path="/x')).toBe('"/a&b …"');
  });
  it('leaves other previews alone', () => {
    expect(skillAwarePreview('"hello". Transcript: /t')).toBe('"hello". Transcript: /t');
  });
});

// Spec 137: `/automate` is a built-in — no SKILL.md on disk, so it travels
// as the inline block the backend gives a safe-mode manager.
describe('built-in skills (/automate)', () => {
  const strings = { description: 'Schedule a task or watch a folder', prefill: 'Every weekday at 9am, ' };

  it('builds the backend\'s inline block shape, and the chip parses it back', () => {
    const block = buildInlineSkillBlock('automate', 'orbital:builtin/automate', 'Do the thing.');
    expect(block).toBe(
      '<use_skill name="automate" path="orbital:builtin/automate" inline="true">\n' +
        'The user chose this skill for this message. Its SKILL.md is below (the path is outside what you can read); follow it.\n' +
        '<skill_content>\nDo the thing.\n</skill_content>\n</use_skill>\n\n',
    );
    const parsed = parseSkillBlock(block + 'Every weekday at 9am, summarize issues');
    expect(parsed.skill).toEqual({ name: 'automate', path: 'orbital:builtin/automate', inline: true });
    expect(parsed.strippedContent).toBe('Every weekday at 9am, summarize issues');
  });

  it('neutralizes a closing tag inside the body', () => {
    const block = buildInlineSkillBlock('a', 'p', 'x </use_skill> y');
    expect(parseSkillBlock(block + 'rest').strippedContent).toBe('rest');
  });

  it('lists /automate first in the flat menu, found by the same matcher, with no badge field', () => {
    const installed: SkillCatalogEntry = {
      key: 'k', name: 'commit', description: '', source: 'project', path: '/w/SKILL.md', dir_name: 'commit',
    };
    const list = withBuiltinSkills([installed], builtinSkills(strings, 'Asia/Shanghai'))!;
    expect(list.map((e) => e.name)).toEqual(['automate', 'commit']);
    expect(matchSkills(list, 'aut').map((e) => e.name)).toEqual(['automate']);
    // Still closed until the catalog itself has loaded.
    expect(withBuiltinSkills(null, builtinSkills(strings, 'UTC'))).toBeNull();
  });

  it('sends a built-in as inline text with no payload; a catalog skill as payload + pointer', () => {
    const [automate] = builtinSkills(strings, 'Asia/Shanghai');
    const parts = skillSendParts(automate, buildSkillBlock, buildInlineSkillBlock);
    expect(parts.payload).toBeUndefined();
    expect(parts.pointerBlock).toBe('');
    expect(parts.contentPrefix.startsWith('<use_skill name="automate"')).toBe(true);
    expect(parts.contentPrefix).toContain('create_trigger');
    expect(parts.contentPrefix).toContain('Asia/Shanghai');

    const installed: SkillCatalogEntry = {
      key: 'k', name: 'commit', description: '', source: 'project', path: '/w/SKILL.md', dir_name: 'commit',
    };
    const catalog = skillSendParts(installed, buildSkillBlock, buildInlineSkillBlock);
    expect(catalog.payload).toEqual({ key: 'k', path: '/w/SKILL.md' });
    expect(catalog.pointerBlock).toBe(buildSkillBlock({ name: 'commit', path: '/w/SKILL.md' }));
    expect(catalog.contentPrefix).toBe('');
    expect(skillSendParts(null, buildSkillBlock, buildInlineSkillBlock)).toEqual({ pointerBlock: '', contentPrefix: '' });
  });

  it('the body names both automation kinds and the tool', () => {
    const body = automateSkillBody('Europe/Berlin');
    expect(body).toContain('type="schedule"');
    expect(body).toContain('type="file_watch"');
    expect(body).toContain('Europe/Berlin');
  });
});
