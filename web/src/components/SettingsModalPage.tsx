// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

import type { Dispatch, SetStateAction } from 'react';
import type { Project, ProjectUpdateRequest } from '../types';
import type { Route } from '../route';
import SettingsView from './SettingsView';
import SettingsToolbar from './SettingsToolbar';
import { withViewTransition } from '../utils/viewTransition';
import { projectDisplayName } from '../utils/projectDisplayName';
import { useT } from '../i18n/useT';

interface SettingsModalPageProps {
  project: Project;
  route: Extract<Route, { name: 'project' }>;
  setRoute: Dispatch<SetStateAction<Route>>;
  onSave: (data: ProjectUpdateRequest) => Promise<unknown> | void;
  onDelete: () => void;
  /** Open the pricing-table editor overlay (P3-I). */
  onEditPricing?: () => void;
}

export default function SettingsModalPage({
  project,
  route,
  setRoute,
  onSave,
  onDelete,
  onEditPricing,
}: SettingsModalPageProps) {
  const t = useT();
  function handleBack() {
    withViewTransition(() => setRoute({
      name: 'project',
      projectId: route.projectId,
      tab: route.tab,
      sessionId: route.sessionId,
      settings: false,
    }));
  }

  return (
    <div className="flex flex-col flex-1 min-h-0 bg-background">
      <SettingsToolbar
        parent={projectDisplayName(project, t)}
        title={t('settingsModal.crumb')}
        onBack={handleBack}
        backTestId="settings-back-button"
        titleTestId="settings-modal-title"
      />

      {/* Scrollable body */}
      <div className="vt-project-body flex-1 overflow-y-auto min-h-0">
        {/* Keyed per project: a switch remounts it, so edits still pending
            from the previous project flush to that project, not this one. */}
        <SettingsView
          key={project.project_id}
          project={project}
          onSave={onSave}
          onDelete={onDelete}
          onEditPricing={onEditPricing}
          scrollToSection={route.settingsAnchor}
        />
      </div>
    </div>
  );
}
