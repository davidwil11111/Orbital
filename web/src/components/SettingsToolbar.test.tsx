// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import SettingsToolbar from './SettingsToolbar';

describe('SettingsToolbar', () => {
  it('Back calls onBack', () => {
    const onBack = vi.fn();
    render(<SettingsToolbar parent="Proj" title="Settings" onBack={onBack} backTestId="back" titleTestId="title" />);
    fireEvent.click(screen.getByTestId('back'));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it('without a parent renders the title alone, with no dangling separator', () => {
    render(<SettingsToolbar title="Global Settings" onBack={vi.fn()} backTestId="back" titleTestId="title" />);
    expect(screen.getByRole('navigation', { name: 'Location' }).textContent).toBe('Global Settings');
  });
});
