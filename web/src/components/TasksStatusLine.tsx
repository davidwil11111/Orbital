// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

import { ChevronRight, Clock, Play } from 'lucide-react';
import type { Trigger } from '../types';
import { useT } from '../i18n/useT';

export type TasksPane = 'queue' | 'automations';

interface TasksStatusLineProps {
  /** Queue items running now. */
  running: number;
  /** Queue items waiting their turn. */
  queued: number;
  triggers: Trigger[];
  /** Open the Tasks page on this pane. */
  onOpen: (pane: TasksPane) => void;
}

/** Stand-in for the `/automate` chip inside the translated teaching line. */
const COMMAND_SLOT = '\u0001';

/**
 * The status line under the project title — spec 137: the ONLY way into the
 * Tasks page now that the tab bar is gone (the user rejected a Tasks row in
 * the session column and a header icon). It always renders:
 *
 * - something running or queued, or any automation: `▶ N running · M queued ·
 *   ⏱ K automations · J off · Tasks ›`. The off-count stays glanceable (a
 *   paused automation is a silent failure mode).
 * - nothing at all: a quiet teaching line pointing at `/automate` (decision 6),
 *   so the queue stays reachable and the tour has something to anchor on.
 *
 * Click → the Tasks page: the Queue pane when something is running or
 * queued, otherwise Automations. Without the queue half of this gate a
 * project with queued work and no automations had no way to reach its queue.
 */
export default function TasksStatusLine({ running, queued, triggers, onOpen }: TasksStatusLineProps) {
  const t = useT();
  const offCount = triggers.filter((tr) => !tr.enabled).length;
  const busy = running + queued > 0;
  const empty = !busy && triggers.length === 0;

  const dot = (
    <span className="text-muted" aria-hidden="true">
      ·
    </span>
  );

  let content: React.ReactNode;
  if (empty) {
    const [before, after = ''] = t('statusLine.teach', { command: COMMAND_SLOT }).split(COMMAND_SLOT);
    content = (
      <>
        <Clock size={13} aria-hidden="true" className="shrink-0 text-muted" />
        <span className="min-w-0 truncate text-muted" data-testid="status-line-teach">
          {before}
          <kbd className="mx-0.5 rounded border border-border px-1 font-mono text-[11px] text-secondary">/automate</kbd>
          {after}
        </span>
      </>
    );
  } else {
    const parts: React.ReactNode[] = [];
    if (running > 0) {
      parts.push(
        <span key="running" className="flex items-center gap-1.5 shrink-0">
          <Play size={11} aria-hidden="true" className="shrink-0 text-success" fill="currentColor" />
          <span className="text-primary">{t('statusLine.running', { n: running })}</span>
        </span>,
      );
    }
    if (queued > 0) {
      parts.push(
        <span key="queued" className="shrink-0 text-primary">
          {t('statusLine.queued', { n: queued })}
        </span>,
      );
    }
    if (triggers.length > 0) {
      parts.push(
        <span key="automations" className="flex items-center gap-1.5 shrink-0">
          <Clock size={13} aria-hidden="true" className="shrink-0 text-muted" />
          <span className="text-primary">
            {t(triggers.length === 1 ? 'statusLine.automations.one' : 'statusLine.automations.other', {
              n: triggers.length,
            })}
          </span>
          {offCount > 0 && <span className="text-muted">{t('trigger.summary.off', { n: offCount })}</span>}
        </span>,
      );
    }
    content = parts.flatMap((part, i) => (i === 0 ? [part] : [<span key={`dot-${i}`}>{dot}</span>, part]));
  }

  return (
    <div className="border-y border-border">
      <button
        type="button"
        onClick={() => onOpen(busy ? 'queue' : 'automations')}
        data-testid="trigger-summary"
        data-tour="status-line"
        data-state={empty ? 'empty' : busy ? 'busy' : 'automations'}
        className="flex w-full min-w-0 items-center gap-2 px-6 py-1.5 text-left text-xs text-secondary transition-colors duration-100 hover:bg-sidebar/50 max-md:min-h-[44px] max-md:px-4"
      >
        {content}
        <span className="ml-auto flex shrink-0 items-center gap-1 pl-2 text-muted">
          {t('projectDetail.tab.queue')}
          <ChevronRight size={13} aria-hidden="true" className="shrink-0" />
        </span>
      </button>
    </div>
  );
}
