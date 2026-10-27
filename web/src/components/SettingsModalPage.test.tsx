// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import SettingsModalPage from './SettingsModalPage';
import type { Project } from '../types';

// Mock SettingsView to avoid its heavy API/data deps — the shell tests focus on
// SettingsModalPage's own layout, not settings form internals. It records the
// anchor it was handed.
const seen = vi.hoisted(() => ({ scrollToSection: undefined as string | undefined }));
vi.mock('./SettingsView', () => ({
  default: (props: { scrollToSection?: string }) => {
    seen.scrollToSection = props.scrollToSection;
    return <div data-testid="settings-view-stub">SettingsView</div>;
  },
}));

const mockProject: Project = {
  project_id: 'proj-1',
  name: 'My Test Project',
  workspace: '/tmp/proj',
  model: 'claude-3-5-sonnet',
  api_key: '',
  base_url: null,
  autonomy: 'hands_off',
  instructions: '',
};

// Spec 137: the toolbar (Back · crumb) moved to ProjectDetail, which draws it
// for every page from the registry — see ProjectDetail.test.tsx "a page swaps
// the header for the toolbar". This component is the page body.
describe('SettingsModalPage', () => {
  it('renders the embedded SettingsView in a scrolling body', () => {
    render(<SettingsModalPage project={mockProject} onSave={vi.fn()} onDelete={vi.fn()} />);
    expect(screen.getByTestId('settings-view-stub')).toBeInTheDocument();
    expect(screen.getByTestId('settings-page-body').className).toContain('overflow-y-auto');
  });

  it('draws no toolbar of its own — ProjectDetail owns the page header', () => {
    render(<SettingsModalPage project={mockProject} onSave={vi.fn()} onDelete={vi.fn()} />);
    expect(screen.queryByTestId('settings-back-button')).toBeNull();
    expect(screen.queryByRole('navigation', { name: 'Location' })).toBeNull();
  });

  it('hands the route\'s settings anchor to SettingsView', () => {
    render(
      <SettingsModalPage project={mockProject} onSave={vi.fn()} onDelete={vi.fn()} scrollToSection="budget" />,
    );
    expect(seen.scrollToSection).toBe('budget');
  });
});
