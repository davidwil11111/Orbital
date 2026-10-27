// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

import type { Project, ProjectUpdateRequest } from '../types';
import SettingsView from './SettingsView';

interface SettingsModalPageProps {
  project: Project;
  onSave: (data: ProjectUpdateRequest) => Promise<unknown> | void;
  onDelete: () => void;
  /** Open the pricing-table page (P3-I). */
  onEditPricing?: () => void;
  /** A `data-settings-section` id to scroll to on open (route.settingsAnchor). */
  scrollToSection?: string;
}

/**
 * The project Settings page body. Spec 137: the toolbar (Back · `Project /
 * Settings`) is no longer drawn here — ProjectDetail draws it for every page
 * from the page registry, so Settings, Tasks and Pricing share one header.
 */
export default function SettingsModalPage({
  project,
  onSave,
  onDelete,
  onEditPricing,
  scrollToSection,
}: SettingsModalPageProps) {
  return (
    // Scrollable body
    <div className="h-full overflow-y-auto min-h-0" data-testid="settings-page-body">
      {/* Keyed per project: a switch remounts it, so edits still pending
          from the previous project flush to that project, not this one. */}
      <SettingsView
        key={project.project_id}
        project={project}
        onSave={onSave}
        onDelete={onDelete}
        onEditPricing={onEditPricing}
        scrollToSection={scrollToSection}
      />
    </div>
  );
}
