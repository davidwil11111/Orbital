// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import PageToolbar from './PageToolbar';

describe('PageToolbar', () => {
  it('Back calls onBack', () => {
    const onBack = vi.fn();
    render(<PageToolbar parent="Proj" title="Settings" onBack={onBack} backTestId="back" titleTestId="title" />);
    fireEvent.click(screen.getByTestId('back'));
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it('without a parent renders the title alone, with no dangling separator', () => {
    render(<PageToolbar title="Global Settings" onBack={vi.fn()} backTestId="back" titleTestId="title" />);
    expect(screen.getByRole('navigation', { name: 'Location' }).textContent).toBe('Global Settings');
  });

  // Spec 137 decision 4: deeper pages nest in the crumb and every segment
  // above the current page is a link.
  it('nests intermediate pages in the crumb, and every segment above the page is a link', () => {
    const onParent = vi.fn();
    const onSettings = vi.fn();
    render(
      <PageToolbar
        parent="Proj"
        onParent={onParent}
        crumbs={[{ label: 'Settings', onClick: onSettings }]}
        title="Pricing"
        onBack={vi.fn()}
        backTestId="back"
        titleTestId="title"
      />,
    );
    expect(screen.getByRole('navigation', { name: 'Location' }).textContent).toBe('Proj/Settings/Pricing');
    fireEvent.click(screen.getByRole('button', { name: 'Proj' }));
    expect(onParent).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    expect(onSettings).toHaveBeenCalledTimes(1);
    // The current page is the heading, not a link.
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Pricing');
    expect(screen.queryByRole('button', { name: 'Pricing' })).toBeNull();
  });

  it('a parent without onParent stays plain text (global settings has none)', () => {
    render(<PageToolbar parent="Proj" title="Settings" onBack={vi.fn()} backTestId="back" titleTestId="title" />);
    expect(screen.queryByRole('button', { name: 'Proj' })).toBeNull();
    expect(screen.getByRole('navigation', { name: 'Location' }).textContent).toBe('Proj/Settings');
  });
});
