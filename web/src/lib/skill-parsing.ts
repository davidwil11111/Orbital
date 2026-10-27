// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

/**
 * The `<use_skill>` block a composer-picked skill adds to a message (spec 132).
 *
 * The backend builds it (agent_os/api/routes/_skill_formatter.py) after the
 * `<attached_files>` block and before the user's text. The composer builds
 * the same pointer block for its optimistic bubble so it matches the WS echo,
 * and the user bubble parses it back into a chip. Keep the two in sync.
 */

export interface PickedSkill {
  name: string;
  path: string;
}

export interface ParseSkillResult {
  strippedContent: string;
  skill: (PickedSkill & { inline: boolean }) | null;
}

const POINTER_LINE =
  'The user chose this skill for this message. Read the file at `path` before acting and follow it.';
const INLINE_LINE =
  'The user chose this skill for this message. Its SKILL.md is below ' +
  '(the path is outside what you can read); follow it.';

// The opening tag's attributes are escaped, so neither `"` nor `>` occurs
// inside them; the inline body's own `</use_skill>` is escaped by the backend.
const BLOCK_RE = /^<use_skill name="([^"\n]*)" path="([^"\n]*)"( inline="true")?>\n[\s\S]*?\n<\/use_skill>\n\n?/;

function escapeAttr(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function unescapeAttr(value: string): string {
  return value
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
}

/** The pointer block exactly as the backend emits it (ends with `\n\n`). */
export function buildSkillBlock(skill: PickedSkill | null | undefined): string {
  if (!skill) return '';
  return (
    `<use_skill name="${escapeAttr(skill.name)}" path="${escapeAttr(skill.path)}">\n` +
    `${POINTER_LINE}\n</use_skill>\n\n`
  );
}

/**
 * The inline form of the block (the backend's `format_skill_prefix` with an
 * `inline_body`): the skill's text travels inside the message. Spec 137's
 * built-in skills (`/automate`) have no SKILL.md on disk for the backend to
 * resolve, so the composer sends this block as part of the message text —
 * the same shape a safe-mode manager receives, so the agent, the title strip
 * and the bubble's chip all read it the way they already do.
 */
export function buildInlineSkillBlock(name: string, path: string, body: string): string {
  const safeBody = body.split('</use_skill>').join('&lt;/use_skill&gt;').replace(/\n+$/, '');
  return (
    `<use_skill name="${escapeAttr(name)}" path="${escapeAttr(path)}" inline="true">\n` +
    `${INLINE_LINE}\n<skill_content>\n${safeBody}\n</skill_content>\n</use_skill>\n\n`
  );
}

/**
 * Parse a leading `<use_skill>` block. Run it on `parseAttachmentsBlock`'s
 * `strippedContent`: the skill block follows the attachments block, which
 * must stay at the very start of the message.
 */
export function parseSkillBlock(content: string): ParseSkillResult {
  const match = BLOCK_RE.exec(content);
  if (!match) return { strippedContent: content, skill: null };
  return {
    strippedContent: content.slice(match[0].length),
    skill: {
      name: unescapeAttr(match[1]),
      path: unescapeAttr(match[2]),
      inline: match[3] !== undefined,
    },
  };
}

const HEAD_RE = /^("?)<use_skill name="([^"\n]*)"/;

/**
 * A worker's "sent: …" activity line quotes the first 100 characters of the
 * dispatched message, which for a skill send is the block's opening tag (the
 * typed text never survives the cut). Show `/name …` instead. chatTransform
 * hands over the marker's tail too: `"<use_skill …". Transcript: …`.
 */
export function skillAwarePreview(preview: string): string {
  const m = HEAD_RE.exec(preview);
  if (!m) return preview;
  const label = `/${unescapeAttr(m[2])} …`;
  if (!m[1]) return label;
  const close = preview.indexOf('". ', m[0].length);
  return `"${label}${close >= 0 ? preview.slice(close) : '"'}`;
}
