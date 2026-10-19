// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

import { ChevronLeft } from 'lucide-react';
import { useT } from '../i18n/useT';

interface SettingsToolbarProps {
  /** Already translated. The crumb the page sits under (the project name);
   *  omitted for global settings, which has no parent. */
  parent?: string;
  /** Already translated. The page itself — rendered as the h1. */
  title: string;
  onBack: () => void;
  backTestId: string;
  titleTestId: string;
}

/**
 * The bar across the top of a settings page, shared by project and global
 * settings: a filled Back button, a divider, then `Parent / Title`.
 *
 * It replaces a bordered "Back to <project>" pill that floated above the page
 * title. The pill was a control dressed as a card, sitting alone on the
 * canvas, and users called it the ugliest thing on the page. A toolbar puts
 * the way out where windowed apps keep it (top-left, same row as where you
 * are), and the breadcrumb carries the "where am I" the old title and pill
 * both repeated.
 *
 * The Back button is FILLED rather than outlined or icon-only: an icon-only
 * chevron was rejected in review as too easy to miss.
 *
 * The `vt-*` classes pair this bar with the project header for the
 * chat <-> settings View Transition: the header folds into this toolbar and
 * the project title glides into the parent crumb, so the toolbar reads as the
 * same header in a compact state rather than a bar that exists nowhere else.
 */
export default function SettingsToolbar({ parent, title, onBack, backTestId, titleTestId }: SettingsToolbarProps) {
  const t = useT();
  return (
    <header className="vt-project-header flex items-center gap-3 h-[52px] shrink-0 px-4 bg-card border-b border-border max-md:h-14">
      <button
        type="button"
        onClick={onBack}
        data-testid={backTestId}
        className="vt-settings-back inline-flex shrink-0 items-center gap-1 h-8 pl-2 pr-3 rounded-lg bg-soft-button text-[13px] font-medium text-primary transition-colors duration-150 hover:bg-soft-button-hover max-md:min-h-[44px]"
      >
        <ChevronLeft size={16} strokeWidth={2.2} aria-hidden="true" />
        {t('settingsToolbar.back')}
      </button>
      <span className="w-px h-5 shrink-0 bg-border" aria-hidden="true" />
      <nav aria-label={t('settingsToolbar.breadcrumb')} className="flex items-center gap-2 min-w-0 text-[13px]">
        {parent && (
          <>
            <span className="vt-project-title truncate text-secondary">{parent}</span>
            <span className="text-muted" aria-hidden="true">/</span>
          </>
        )}
        <h1 className="truncate font-semibold text-primary" data-testid={titleTestId}>
          {title}
        </h1>
      </nav>
    </header>
  );
}
