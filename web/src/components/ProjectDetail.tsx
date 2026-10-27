// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

import { useCallback, useEffect, useRef, useState } from 'react';
import type { Dispatch, ReactNode, SetStateAction } from 'react';
import { PanelRight } from 'lucide-react';
import type { AgentRunStatus, FileContent, Project, Trigger } from '../types';
import type { ProjectPage, ProjectRoute, Route } from '../route';
import { pageTrail, routeUp } from '../route';
import StatusBadge from './StatusBadge';
import TasksStatusLine, { type TasksPane } from './TasksStatusLine';
import SettingsIcon from './SettingsIcon';
import PageToolbar from './PageToolbar';
import { PROJECT_PAGES } from './projectPages';
import { withViewTransition } from '../utils/viewTransition';
import BudgetCorner from './BudgetCorner';
import FilePreviewDrawer from './FilePreviewDrawer';
import { OpenPathContext } from './filePreviewContext';
import { useQueue } from '../hooks/useQueue';
import { useFiles } from '../hooks/useFiles';
import { usePanelDockable, usePanelState } from '../hooks/usePanelState';
import { fetchPathWithFallback } from '../utils/openPathWithFallback';
import { useT } from '../i18n/useT';
import { projectDisplayName } from '../utils/projectDisplayName';
import { displayRunStatus } from '../utils/projectStatus';
import { useCredentialCards, resolveCard } from '../hooks/useCredentialCards';

interface ProjectDetailProps {
  project: Project;
  agentStatus: AgentRunStatus;
  /**
   * run-status's `sub_agents_running` for this project (spec 095/102): a
   * worker has an open turn in some session while the manager rests. Only
   * the header badge reads it — `agentStatus` itself keeps manager semantics
   * for everything else. Default false (older callers).
   */
  subAgentsRunning?: boolean;
  statusSummary?: string;
  route: Extract<Route, { name: 'project' }>;
  setRoute: Dispatch<SetStateAction<Route>>;
  triggers?: Trigger[];
  /**
   * Still accepted (App owns a trigger hook for the strip) but no longer used
   * here: management moved into the Automations pane, which drives its own
   * hook. Kept so App's existing props stay valid.
   */
  onTriggerToggle?: (triggerId: string, enabled: boolean) => void;
  onTriggerDelete?: (triggerId: string) => void;
  /**
   * The global/default LLM model (from app settings). Used as the header's
   * model label only when the project has no per-project model pinned. NOT a
   * substitute for `agent_name` — a model id, not an identity.
   */
  globalDefaultModel?: string;
  /**
   * Spec 079 — installed sub-agents, forwarded to the Automations pane so an
   * automation can name the worker that runs it. Pass-through only; App owns
   * the single /agents/available fetch (hoisted there so a tab switch never
   * re-hits the daemon's cache).
   */
  agentsAvailable?: Array<{ slug: string; name: string }>;
  /** The chat home's body (ChatTab). Rendered only when no page is open. */
  children?: ReactNode;
  /**
   * Spec 137 — what App supplies for each page's body: the queue for Tasks,
   * the settings form, the pricing table. The page registry decides what to
   * do with it (`PROJECT_PAGES[page].render`).
   */
  pageContent?: Partial<Record<ProjectPage, ReactNode>>;
}

// Workbench and Calendar are GLOBAL workspace surfaces — they aggregate
// across projects and deliberately have no per-project page (user decision,
// 2026-07-24). Spec 137 retired the Chat / Tasks / Files tab bar: chat is the
// home, Tasks and Settings are pages, Files is the workspace panel maximized.

export default function ProjectDetail({
  project,
  agentStatus,
  subAgentsRunning = false,
  statusSummary,
  route,
  setRoute,
  triggers = [],
  globalDefaultModel,
  agentsAvailable = [],
  children,
  pageContent = {},
}: ProjectDetailProps) {
  const t = useT();
  // Names the credential the header chip shows; the same flat list the
  // settings picker offers.
  const { cards, defaultCardId } = useCredentialCards();

  const page = route.page;
  const pageDef = page ? PROJECT_PAGES[page] : undefined;

  // The status line's queue half (spec 137 gap 4): without it a project with
  // queued work and no automations has no way to reach its queue.
  const { snapshot } = useQueue(project.project_id);
  const runningCount = snapshot?.items.filter((item) => item.state === 'running').length ?? 0;
  const queuedCount = snapshot?.items.filter((item) => item.state === 'queued').length ?? 0;

  // File preview drawer (spec 002). The open/close state lives on the route
  // (`previewPath`); the fetched content + lazy 404 probe live here. Opening is
  // PROBE-FIRST: fetch the content, then open the drawer only once it's in hand
  // (404/error just toasts, never opening) — so the panel never flashes blank
  // and a missing file never opens-then-closes.
  const { getFileContent, resolvePath, saveFileContent } = useFiles();
  const [previewContent, setPreviewContent] = useState<FileContent | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewToast, setPreviewToast] = useState<string | null>(null);
  // Latest requested path — guards against a slow fetch for an earlier click
  // resolving after a newer one (replace-on-click).
  const latestPreviewReqRef = useRef<string | null>(null);
  const previewPath = route.previewPath ?? null;

  // Spec 078 §5.1/§9.10 — on the chat home, above the push threshold, the
  // docked workspace panel in ChatTab shows the preview instead. Suppressing
  // the overlay here is what keeps a chat path click from opening BOTH. The
  // probe-first fetch above still runs: it is what resolves an abbreviated
  // path to a real one (and toasts a miss) before the panel is told about it.
  // Pages (Tasks, Settings), mobile, and narrow windows keep the overlay.
  const dockable = usePanelDockable();
  const chatPanelOwnsPreview = dockable && page === undefined;

  // Spec 137 — the workspace panel's shared state (ChatTab renders it). The
  // header reads it for two things: a docked panel maximized takes the whole
  // main area, header included; below the dock threshold the header carries
  // the button that opens the panel as an overlay / bottom sheet.
  const panel = usePanelState(project.project_id, route.sessionId);
  const panelFillsMain = dockable && page === undefined && panel.expanded && panel.maximized;

  const handleOpenPath = useCallback(
    async (path: string) => {
      latestPreviewReqRef.current = path;
      setPreviewLoading(true);
      // Probe-first: fetch BEFORE opening so the drawer never slides in blank.
      // On replace-on-click the already-open drawer keeps showing the prior
      // content until the new content is ready — no flash there either.
      // Agents abbreviate paths in chat ('drafts/x.md', bare 'DECISIONS.md'),
      // so a miss falls back to the resolve endpoint and transparently opens
      // a unique suffix match.
      const outcome = await fetchPathWithFallback(
        (p) => getFileContent(project.project_id, p),
        (p) => resolvePath(project.project_id, p),
        path,
      );
      // Drop the result if a newer open superseded this one.
      if (latestPreviewReqRef.current !== path) return;
      setPreviewLoading(false);
      if (outcome.status !== 'ok') {
        setPreviewToast(
          outcome.status === 'ambiguous'
            ? t('chat.path.ambiguous', { name: path })
            : t('chat.path.notFound'),
        );
        return;
      }
      const data = outcome.content;
      setPreviewContent(data);
      // Two-frame open (WKWebView "blink" fix). Render the content into the
      // drawer's OFF-SCREEN compositing layer first, then trigger the slide-in
      // one paint later — so WebKit can't composite the layer's stale/empty
      // backing store for the first animation frame (the blink you see in
      // pywebview but not Chromium). The double rAF guarantees a paint landed
      // between "content in" and "start sliding".
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          if (latestPreviewReqRef.current !== path) return;
          // Route to the RESOLVED path (not the clicked one) so save/deep-link
          // hit the real file when the fallback rewrote an abbreviated path.
          setRoute((prev) =>
            prev.name === 'project' ? { ...prev, previewPath: outcome.path } : prev,
          );
        }),
      );
    },
    [getFileContent, resolvePath, project.project_id, setRoute, t],
  );

  const handleClosePreview = useCallback(() => {
    latestPreviewReqRef.current = null;
    setRoute((prev) =>
      prev.name === 'project' ? { ...prev, previewPath: undefined } : prev,
    );
  }, [setRoute]);

  // Auto-dismiss the "file not found" toast.
  useEffect(() => {
    if (!previewToast) return;
    const id = setTimeout(() => setPreviewToast(null), 4000);
    return () => clearTimeout(id);
  }, [previewToast]);

  // Every page change goes through here: the one-shot preview path never
  // survives navigation (it is a chat overlay, not a persistent panel), and
  // the View Transition folds the header into the toolbar and back.
  function navigate(next: ProjectRoute) {
    withViewTransition(() => setRoute({ ...next, previewPath: undefined }));
  }

  function handleSettingsClick() {
    navigate({ ...route, page: 'settings' });
  }

  // Budget corner → open settings scrolled to the Budget section.
  function handleOpenBudgetSettings() {
    navigate({ ...route, page: 'settings', settingsAnchor: 'budget' });
  }

  // Status line → the Tasks page, on the pane that has something to show.
  function handleOpenTasks(pane: TasksPane) {
    navigate({ ...route, page: 'tasks', queuePane: pane });
  }

  // Automations "try this in chat" and "New automation" (spec 137 decision
  // 2): back to chat with `/automate` picked. Rides the existing one-shot
  // composer prefill (route.draft, the Workbench doorway) — filled, never sent.
  function handleTryInChat(text: string) {
    navigate({ ...route, page: undefined, draft: text, draftSkill: 'automate' });
  }

  function handleNewAutomation() {
    handleTryInChat(t('automations.example.schedule'));
  }

  // Back: up one level (a page's parent, else the chat home), carrying the
  // page's own landing patch (Pricing → Settings lands on Budget).
  function handleBack() {
    if (!page) return;
    navigate({ ...routeUp(route), ...(PROJECT_PAGES[page].backPatch ?? {}) });
  }

  const trail = pageTrail(page);
  const projectName = projectDisplayName(project, t);

  return (
    <div className="flex flex-col flex-1 min-h-0 bg-card">
      {pageDef ? (
        // A page: the header folds into the toolbar (both carry
        // `vt-project-header`, so the View Transition morphs one into the
        // other — see index.css). The crumb walks the registry's parent chain.
        <PageToolbar
          parent={projectName}
          onParent={() => navigate({ ...route, page: undefined })}
          crumbs={trail.slice(0, -1).map((p) => ({
            label: t(PROJECT_PAGES[p].titleKey),
            onClick: () => navigate({ ...route, page: p }),
          }))}
          title={t(pageDef.titleKey)}
          onBack={handleBack}
          backTestId={pageDef.backTestId}
          titleTestId={pageDef.titleTestId}
        />
      ) : panelFillsMain ? null : (
      // Everything above the conversation, as one unit (see above).
      <div className="vt-project-header shrink-0 bg-card">
      {/* Header */}
      <div className="flex items-center justify-between gap-2 px-6 pt-5 pb-4 max-md:px-4">
        {/* Left cluster keeps a readable minimum so a long budget pill on the
            right can never crush the title to 0 width (P3-J header collision). */}
        <div className="flex items-center gap-3 min-w-[40%] flex-1">
          {/* data-tour on the title + badge, not the whole flex-1 cluster: the
              tour spotlights it, and a half-screen-wide box around a short
              name points at nothing. */}
          <div data-tour="project-header" className="flex items-center gap-3 min-w-0">
          <h1 className="vt-project-title text-lg font-semibold tracking-[-0.01em] text-primary truncate min-w-0">{projectName}</h1>
          {/* Spec 102: Active while a pinned worker runs with the manager idle;
              a manager error/approval/waiting state is shown as-is. */}
          <StatusBadge status={displayRunStatus(agentStatus, subAgentsRunning)} />
          </div>
        </div>
        <div className="flex items-center gap-3 min-w-0 shrink">
          {/* Budget corner (P3-G): converted window spend / warn / exhausted
              pill, next to the model name. Reads the same GET /cost as the
              settings meter; event-driven (no polling). Click → Budget settings.
              Replaces the dead P3-F $0.00 segment properly. */}
          <BudgetCorner
            project={project}
            pauseReason={snapshot?.pause_reason}
            onOpenBudgetSettings={handleOpenBudgetSettings}
          />
          {(() => {
            // Credential label (spec 082 D13): the card this project runs on —
            // its own pinned card, else the global default. A card name
            // already carries the model ("DeepSeek · deepseek-v4-flash"), so
            // the default case reads as it always did. Falls back to the raw
            // model id against a pre-082 daemon, which serves no cards.
            // NEVER agent_name (an identity, not a model).
            const label =
              resolveCard(cards, project.card_id, defaultCardId)?.name ||
              project.model ||
              globalDefaultModel ||
              '';
            if (!label) return null;
            return (
              <span
                data-testid="project-credential-chip"
                className="font-mono text-[11px] text-secondary truncate max-w-[220px] max-md:hidden"
              >
                {label}
              </span>
            );
          })()}
          {/* Spec 137 gap 3: below the dock threshold there is no panel
              handle, so the panel gets a header button — an overlay on a
              narrow desktop window, the bottom sheet on a phone. */}
          {!dockable && (
            <button
              type="button"
              onClick={panel.openOverlay}
              aria-label={t('projectDetail.openPanel')}
              title={t('projectDetail.openPanel')}
              data-testid="panel-open-button"
              data-tour="panel-button"
              className="inline-flex shrink-0 items-center justify-center w-7 h-7 rounded-md text-secondary hover:text-primary hover:bg-card-hover transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 max-md:w-10 max-md:h-10"
            >
              <PanelRight size={16} aria-hidden="true" />
            </button>
          )}
          <span data-tour="project-settings" className="inline-flex">
            <SettingsIcon onClick={handleSettingsClick} />
          </span>
        </div>
      </div>


      {/* Status summary line */}
      {statusSummary && (
        <div className="px-6 pb-2 max-md:px-4">
          <p className="text-xs text-secondary truncate">{statusSummary}</p>
        </div>
      )}

      {/* The status line — the only way into Tasks (spec 137). Always
          rendered: a teaching line when nothing is scheduled or queued. */}
      <TasksStatusLine
        running={runningCount}
        queued={queuedCount}
        triggers={triggers}
        onOpen={handleOpenTasks}
      />
      </div>
      )}

      {/* Page body. `relative` so the file-preview drawer (absolute) overlays
          only this area, not the header/toolbar. The OpenPathContext hands
          the drawer-open handler down to the chat's MarkdownContent. */}
      <div
        className={`vt-project-body flex-1 overflow-hidden min-h-0 relative ${
          pageDef ? 'bg-background' : ''
        }`}
      >
        <OpenPathContext.Provider value={handleOpenPath}>
          {pageDef
            ? pageDef.render({
                project,
                route,
                setRoute,
                content: pageContent[pageDef.key],
                agentsAvailable,
                onTryInChat: handleTryInChat,
                onNewAutomation: handleNewAutomation,
              })
            : children}
        </OpenPathContext.Provider>
        <FilePreviewDrawer
          open={previewPath !== null && !chatPanelOwnsPreview}
          // Content-driven (NOT previewPath): the drawer renders its content
          // while still off-screen so the slide-in reveals an already-painted
          // layer. Also keeps content during the close slide-out (no empty
          // "select a file" flash on close either).
          selectedPath={previewContent?.path ?? null}
          fileContent={previewContent}
          loading={previewLoading}
          onClose={handleClosePreview}
          onSave={(path, content) => saveFileContent(project.project_id, path, content)}
        />
        {previewToast && (
          <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-50 px-4 py-2 rounded-lg bg-primary text-background text-[13px] shadow-lg max-w-[90%] text-center pointer-events-none">
            {previewToast}
          </div>
        )}
      </div>
    </div>
  );
}
