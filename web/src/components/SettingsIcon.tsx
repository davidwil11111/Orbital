// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

import { Settings } from 'lucide-react';
import { useT } from '../i18n/useT';

interface SettingsIconProps {
  onClick: () => void;
  title?: string;
  /**
   * Spec 137 (the folder shell): Settings, or a page under it, fills the right
   * area. The gear in the project column is then the chosen place and shows
   * it the way a chosen row does (a raised fill). Only the column passes it;
   * the phone header has no column and keeps the bare icon.
   */
  selected?: boolean;
  /** `column`: the 28px icon button of the project column's name row. */
  size?: 'inline' | 'column';
}

export default function SettingsIcon({ onClick, title, selected = false, size = 'inline' }: SettingsIconProps) {
  const t = useT();
  const box =
    size === 'column'
      ? `w-7 h-7 rounded-md shrink-0 transition-[color,background-color,box-shadow] focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 ${
          selected
            ? 'bg-card text-primary shadow-[0_1px_2px_rgb(0_0_0/0.06)]'
            : 'text-secondary hover:text-primary hover:bg-card-hover'
        }`
      : 'p-1 rounded text-secondary hover:text-primary transition-colors';
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={t('settingsIcon.aria')}
      aria-current={selected ? 'page' : undefined}
      title={title ?? t('settingsIcon.aria')}
      className={`flex items-center justify-center duration-150 ${box}`}
    >
      <Settings size={size === 'column' ? 16 : 14} />
    </button>
  );
}
