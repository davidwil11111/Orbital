// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

import MessageAvatar from './MessageAvatar';
import { useT } from '../i18n/useT';

interface Props {
  slug: string;
  name: string;
}

/** Spec 115 D4 — the compact row for a bring-your-own sub-agent whose CLI the
 *  daemon could not find.
 *
 *  Before this, such an agent simply vanished from the daemon-level list (and
 *  from project settings and the pin switcher, which keep hiding it — there is
 *  nothing to do with one there). The reporter of #57 read that as "Orbital
 *  does not support codex". Saying "not detected" with the one thing to try
 *  (install, or Refresh if it already works in Terminal, or fix the login
 *  PATH) turns the silence into a next step. No login / config controls: the
 *  full card returns on its own once the binary resolves. */
export default function NotDetectedRow({ slug, name }: Props) {
  const t = useT();
  return (
    <div
      data-testid={`sub-agent-not-detected-${slug}`}
      className="bg-sidebar/30 border border-border rounded-lg px-4 py-3"
    >
      <div className="flex items-center gap-2 flex-wrap">
        <MessageAvatar variant="agent" agentHandle={slug} />
        <span className="text-base font-medium text-primary">{name}</span>
        <code className="text-xs text-secondary/80 font-mono">{slug}</code>
        <span className="px-2 py-0.5 rounded-full border text-[11px] bg-sidebar text-secondary border-border">
          {t('subAgentCard.notDetected')}
        </span>
      </div>
      <p className="text-xs text-secondary mt-1.5">
        {t('subAgentCard.notDetectedHint', { name })}
      </p>
    </div>
  );
}
