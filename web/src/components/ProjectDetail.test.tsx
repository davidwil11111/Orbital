// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ProjectDetail from './ProjectDetail';
import type { Project, SessionListEntry, Trigger } from '../types';
import type { Route } from '../route';
import { __resetPanelState, usePanelState } from '../hooks/usePanelState';
import { renderHook } from '@testing-library/react';

// The project column lists the sessions (useSessions) and ProjectDetail reads
// the queue (useQueue); both need a WebSocketProvider. Mock them to avoid the
// provider requirement in these unit tests. The session list is mutable so
// the column tests can put rows in it.
const sessionsMock = vi.hoisted(() => ({ sessions: [] as SessionListEntry[] }));
vi.mock('../hooks/useSessions', () => ({
  useSessions: () => ({
    sessions: sessionsMock.sessions,
    loading: false,
    error: null,
    refresh: vi.fn(),
    renameSession: vi.fn(),
    pinSession: vi.fn(),
    deleteSession: vi.fn(),
  }),
}));
// "+ new session" in the column mints through useAgent.
const mintMock = vi.hoisted(() => ({ newSession: vi.fn(async () => ({ status: 'ok', session_id: 'sess-new' })) }));
vi.mock('../hooks/useAgent', () => ({
  useAgent: () => ({ newSession: mintMock.newSession }),
}));
// A mutable queue snapshot, so the status-line tests can put work in it.
const queueMock = vi.hoisted(() => ({ snapshot: null as null | { items: Array<{ state: string }> } }));
vi.mock('../hooks/useQueue', () => ({
  useQueue: () => ({
    snapshot: queueMock.snapshot,
    loading: false,
    error: null,
    refresh: vi.fn(),
    addItem: vi.fn(),
    removeItem: vi.fn(),
    editItem: vi.fn(),
    stopQueue: vi.fn(),
    resumeQueue: vi.fn(),
  }),
}));
// The Automations pane mounts useTriggers (→ useWebSocket). These tests only
// care about which pane renders, so stub it empty.
vi.mock('../hooks/useTriggers', () => ({
  useTriggers: () => ({
    triggers: [],
    loading: false,
    fetchTriggers: vi.fn(async () => []),
    createTrigger: vi.fn(),
    updateTrigger: vi.fn(),
    toggleTrigger: vi.fn(),
    deleteTrigger: vi.fn(),
  }),
}));
// BudgetCorner (in the header) calls useCost → useWebSocket. Mock it to a
// no-spend response so the corner renders nothing and the header label tests
// stay focused on the model label.
vi.mock('../hooks/useCost', () => ({
  useCost: () => ({ cost: null, loading: false, error: null, refresh: vi.fn() }),
}));

const mockProject: Project = {
  project_id: 'proj-42',
  name: 'Alpha Project',
  workspace: '/tmp/alpha',
  model: 'claude-3-5-sonnet',
  api_key: '',
  base_url: null,
  autonomy: 'hands_off',
  instructions: '',
};

const baseRoute: Extract<Route, { name: 'project' }> = {
  name: 'project',
  projectId: 'proj-42',
  sessionId: 'sess-xyz',
};

function makeTrigger(over: Partial<Trigger> = {}): Trigger {
  return {
    id: 'trg-1',
    name: 'Daily build',
    enabled: true,
    type: 'schedule',
    schedule: { cron: '0 9 * * *', human: 'Every day at 09:00', timezone: 'UTC' },
    task: 'Build',
    autonomy: null,
    last_triggered: null,
    trigger_count: 0,
    created_at: '2026-01-01T00:00:00Z',
    ...over,
  } as Trigger;
}

afterEach(() => {
  queueMock.snapshot = null;
  sessionsMock.sessions = [];
  __resetPanelState();
  setViewportWidth(1024);
});

/** usePanelDockable / usePanelMobile read window.innerWidth. jsdom's 1024 is a
 * narrow desktop window: the column layout without a docked panel. */
function setViewportWidth(width: number) {
  Object.defineProperty(window, 'innerWidth', {
    value: width,
    configurable: true,
    writable: true,
  });
}

function session(id: string): SessionListEntry {
  return {
    session_id: id,
    status: 'idle',
    session_uuid: `uuid-${id}`,
    last_terminal_event: null,
    last_activity_at: '2026-10-28T09:00:00Z',
  };
}

function renderProjectDetail(
  routeOverride?: Partial<Extract<Route, { name: 'project' }>>,
  setRoute = vi.fn(),
) {
  const route = { ...baseRoute, ...routeOverride };
  return render(
    <ProjectDetail
      project={mockProject}
      agentStatus="idle"
      route={route}
      setRoute={setRoute}
    />,
  );
}

describe('ProjectDetail — header badge while a worker runs (spec 102)', () => {
  function renderBadge(agentStatus: 'idle' | 'error' | 'running', subAgentsRunning?: boolean) {
    return render(
      <ProjectDetail
        project={mockProject}
        agentStatus={agentStatus}
        subAgentsRunning={subAgentsRunning}
        route={baseRoute}
        setRoute={vi.fn()}
      />,
    );
  }

  it('reads Active with the manager idle and a worker running', () => {
    renderBadge('idle', true);
    expect(screen.getByText('Active')).toBeInTheDocument();
    expect(screen.queryByText('Idle')).toBeNull();
  });

  it('reads Idle with the manager idle and no worker running', () => {
    renderBadge('idle', false);
    expect(screen.getByText('Idle')).toBeInTheDocument();
  });

  it('reads Idle when the prop is omitted (older callers)', () => {
    renderBadge('idle');
    expect(screen.getByText('Idle')).toBeInTheDocument();
  });

  it('keeps a manager error over a running worker', () => {
    renderBadge('error', true);
    expect(screen.getByText('Error')).toBeInTheDocument();
    expect(screen.queryByText('Active')).toBeNull();
  });
});

describe('ProjectDetail — gear icon in header', () => {
  it('renders the gear icon button in the project header', () => {
    renderProjectDetail();
    expect(screen.getByRole('button', { name: /project settings/i })).toBeInTheDocument();
  });

  it('clicking the gear icon opens the settings page, preserving projectId and sessionId', () => {
    const setRoute = vi.fn();
    renderProjectDetail({}, setRoute);
    fireEvent.click(screen.getByRole('button', { name: /project settings/i }));
    expect(setRoute).toHaveBeenCalledTimes(1);
    expect(setRoute).toHaveBeenCalledWith({
      name: 'project',
      projectId: 'proj-42',
      sessionId: 'sess-xyz',
      page: 'settings',
      previewPath: undefined,
    });
  });
});

// Spec 137: the Chat / Tasks / Files tab bar is gone. Chat is the only home;
// Tasks and Settings are pages under the shared toolbar.
describe('ProjectDetail — no tab bar, one page at a time', () => {
  it('renders no tab bar: no Chat, Tasks or Files buttons', () => {
    renderProjectDetail();
    expect(screen.queryByRole('button', { name: /^Chat$/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Files$/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Tasks/i })).toBeNull();
    expect(document.querySelector('[data-tour^="tab-"]')).toBeNull();
  });

  it('home (no page) renders the header and the chat children', () => {
    render(
      <ProjectDetail project={mockProject} agentStatus="idle" route={baseRoute} setRoute={vi.fn()}>
        <div data-testid="chat-children" />
      </ProjectDetail>,
    );
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Alpha Project');
    expect(screen.getByTestId('chat-children')).toBeInTheDocument();
  });

  it('a page replaces the chat in the right area; the column stays', () => {
    render(
      <ProjectDetail
        project={mockProject}
        agentStatus="idle"
        route={{ ...baseRoute, page: 'settings' }}
        setRoute={vi.fn()}
        pageContent={{ settings: <div data-testid="settings-body" /> }}
      >
        <div data-testid="chat-children" />
      </ProjectDetail>,
    );
    const main = screen.getByTestId('project-main');
    expect(within(main).getByTestId('settings-body')).toBeInTheDocument();
    expect(screen.queryByTestId('chat-children')).toBeNull();
    // The column is still the project: name, gear, status line, sessions.
    const column = screen.getByTestId('project-column');
    expect(within(column).getByRole('heading', { level: 1 }).textContent).toBe('Alpha Project');
    expect(within(column).getByRole('button', { name: /project settings/i })).toBeInTheDocument();
    expect(within(column).getByTestId('trigger-summary')).toBeInTheDocument();
    expect(within(column).getByTestId('session-sidebar')).toBeInTheDocument();
    // The page carries only its title: no project crumb (the column names it).
    expect(screen.getByRole('navigation', { name: 'Location' }).textContent).toBe('Settings');
    expect(screen.getByTestId('settings-modal-title').textContent).toBe('Settings');
  });

  it('Pricing nests under Settings: crumb Settings / Pricing, the parent a link', () => {
    const setRoute = vi.fn();
    renderProjectDetail({ page: 'pricing' }, setRoute);
    expect(screen.getByRole('navigation', { name: 'Location' }).textContent).toBe('Settings/Pricing table');
    fireEvent.click(within(screen.getByRole('navigation', { name: 'Location' })).getByRole('button', { name: 'Settings' }));
    expect(setRoute).toHaveBeenCalledWith(expect.objectContaining({ page: 'settings' }));
  });
});

// Spec 137 item 10: the column is the navigation on desktop and narrow
// windows, so pages have no Back there. The phone has no column and keeps
// Back + `Project / Page` (item 11).
describe('ProjectDetail — Back exists on the phone only', () => {
  it.each([1440, 1000])('no Back on the Tasks, Settings and Pricing pages at %ipx', (width) => {
    setViewportWidth(width);
    for (const [page, back] of [
      ['tasks', 'tasks-back-button'],
      ['settings', 'settings-back-button'],
      ['pricing', 'pricing-back-button'],
    ] as const) {
      const { unmount } = renderProjectDetail({ page });
      expect(screen.queryByTestId(back)).toBeNull();
      expect(screen.queryByRole('button', { name: 'Back' })).toBeNull();
      unmount();
    }
  });

  it('phone: a page is full-screen with Back + `Project / Page`, the header folded away', () => {
    setViewportWidth(390);
    render(
      <ProjectDetail
        project={mockProject}
        agentStatus="idle"
        route={{ ...baseRoute, page: 'settings' }}
        setRoute={vi.fn()}
        pageContent={{ settings: <div data-testid="settings-body" /> }}
      >
        <div data-testid="chat-children" />
      </ProjectDetail>,
    );
    expect(screen.getByTestId('settings-body')).toBeInTheDocument();
    expect(screen.queryByTestId('chat-children')).toBeNull();
    expect(screen.queryByTestId('project-column')).toBeNull();
    expect(screen.queryByRole('button', { name: /project settings/i })).toBeNull();
    expect(screen.getByRole('navigation', { name: 'Location' }).textContent).toBe('Alpha Project/Settings');
    expect(screen.getByTestId('settings-back-button')).toBeInTheDocument();
  });

  it('phone: Back from Settings returns to chat with the session kept', () => {
    setViewportWidth(390);
    const setRoute = vi.fn();
    renderProjectDetail({ page: 'settings', settingsAnchor: 'budget' }, setRoute);
    fireEvent.click(screen.getByTestId('settings-back-button'));
    expect(setRoute).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'project', projectId: 'proj-42', sessionId: 'sess-xyz', page: undefined }),
    );
  });

  it('phone: the project crumb is a link home', () => {
    setViewportWidth(390);
    const setRoute = vi.fn();
    renderProjectDetail({ page: 'tasks' }, setRoute);
    fireEvent.click(screen.getByRole('button', { name: 'Alpha Project' }));
    expect(setRoute).toHaveBeenCalledWith(expect.objectContaining({ page: undefined }));
  });

  it('phone: Pricing nests under Settings: Project / Settings / Pricing, Back goes up one level to Budget', () => {
    setViewportWidth(390);
    const setRoute = vi.fn();
    renderProjectDetail({ page: 'pricing' }, setRoute);
    expect(screen.getByRole('navigation', { name: 'Location' }).textContent).toBe(
      'Alpha Project/Settings/Pricing table',
    );
    fireEvent.click(screen.getByTestId('pricing-back-button'));
    expect(setRoute).toHaveBeenCalledWith(
      expect.objectContaining({ page: 'settings', settingsAnchor: 'budget' }),
    );
    setRoute.mockClear();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    expect(setRoute).toHaveBeenCalledWith(expect.objectContaining({ page: 'settings' }));
  });
});

describe('ProjectDetail — Tasks page', () => {
  it('is a page titled Tasks (Project / Tasks on the phone)', () => {
    const { unmount } = renderProjectDetail({ page: 'tasks' });
    expect(screen.getByRole('navigation', { name: 'Location' }).textContent).toBe('Tasks');
    expect(screen.getByTestId('tasks-title').textContent).toBe('Tasks');
    unmount();

    setViewportWidth(390);
    renderProjectDetail({ page: 'tasks' });
    expect(screen.getByRole('navigation', { name: 'Location' }).textContent).toBe('Alpha Project/Tasks');
  });

  it('shows the Queue│Automations switch only on the Tasks page', () => {
    const { unmount } = renderProjectDetail();
    expect(screen.queryByTestId('queue-pane-automations')).toBeNull();
    unmount();

    renderProjectDetail({ page: 'tasks' });
    expect(screen.getByTestId('queue-pane-queue')).toBeInTheDocument();
    expect(screen.getByTestId('queue-pane-automations')).toBeInTheDocument();
  });

  it('defaults to the queue pane and renders the queue content there', () => {
    render(
      <ProjectDetail
        project={mockProject}
        agentStatus="idle"
        route={{ ...baseRoute, page: 'tasks' }}
        setRoute={vi.fn()}
        pageContent={{ tasks: <div data-testid="queue-children" /> }}
      />,
    );
    expect(screen.getByTestId('queue-children')).toBeInTheDocument();
    expect(screen.getByTestId('queue-pane-queue')).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByTestId('automations-pane')).toBeNull();
  });

  it('renders the automations pane (not the queue content) when queuePane=automations', () => {
    render(
      <ProjectDetail
        project={mockProject}
        agentStatus="idle"
        route={{ ...baseRoute, page: 'tasks', queuePane: 'automations' }}
        setRoute={vi.fn()}
        pageContent={{ tasks: <div data-testid="queue-children" /> }}
      />,
    );
    expect(screen.getByTestId('automations-pane')).toBeInTheDocument();
    expect(screen.queryByTestId('queue-children')).toBeNull();
  });

  it('clicking the Automations pane sets route.queuePane', () => {
    const setRoute = vi.fn();
    renderProjectDetail({ page: 'tasks' }, setRoute);
    fireEvent.click(screen.getByTestId('queue-pane-automations'));
    expect(setRoute).toHaveBeenCalledWith(
      expect.objectContaining({ page: 'tasks', queuePane: 'automations' }),
    );
  });

  it('"New automation" goes back to chat with /automate picked and the schedule example as the draft', () => {
    const setRoute = vi.fn();
    renderProjectDetail({ page: 'tasks', queuePane: 'automations' }, setRoute);
    fireEvent.click(screen.getByTestId('automations-new'));
    expect(setRoute).toHaveBeenCalledWith(
      expect.objectContaining({
        page: undefined,
        draftSkill: 'automate',
        draft: 'Every weekday at 9am, summarize new GitHub issues',
      }),
    );
  });

  it('the empty state\'s examples go back to chat with /automate picked', () => {
    const setRoute = vi.fn();
    renderProjectDetail({ page: 'tasks', queuePane: 'automations' }, setRoute);
    fireEvent.click(screen.getByText('When a new spreadsheet lands in this folder, write up a data analysis report'));
    expect(setRoute).toHaveBeenCalledWith(
      expect.objectContaining({
        page: undefined,
        draftSkill: 'automate',
        draft: 'When a new spreadsheet lands in this folder, write up a data analysis report',
      }),
    );
  });
});

// Spec 137 gap 4 + decision 6: the status line under the title is the only
// way into Tasks, so it renders always — with queued work and no
// automations too, and as a teaching line when there is nothing at all.
describe('ProjectDetail — the status line', () => {
  it('renders with something queued and zero automations, and opens the Queue pane', () => {
    queueMock.snapshot = { items: [{ state: 'queued' }] };
    const setRoute = vi.fn();
    renderProjectDetail({}, setRoute);
    const line = screen.getByTestId('trigger-summary');
    expect(line.textContent).toContain('1 queued');
    expect(line.textContent).not.toContain('automation');
    fireEvent.click(line);
    expect(setRoute).toHaveBeenCalledWith(expect.objectContaining({ page: 'tasks', queuePane: 'queue' }));
  });

  it('counts running and queued items separately', () => {
    queueMock.snapshot = { items: [{ state: 'running' }, { state: 'queued' }, { state: 'queued' }, { state: 'completed' }] };
    renderProjectDetail();
    const line = screen.getByTestId('trigger-summary');
    expect(line.textContent).toContain('1 running');
    expect(line.textContent).toContain('2 queued');
  });

  it('with automations only, shows the count and the off-count and opens the Automations pane', () => {
    const setRoute = vi.fn();
    render(
      <ProjectDetail
        project={mockProject}
        agentStatus="idle"
        route={baseRoute}
        setRoute={setRoute}
        triggers={[makeTrigger(), makeTrigger({ id: 'trg-2', enabled: false })]}
      />,
    );
    const line = screen.getByTestId('trigger-summary');
    expect(line.textContent).toContain('2 automations');
    expect(line.textContent).toContain('1 off');
    // No management controls on the line — one home for that.
    expect(screen.queryByRole('switch')).toBeNull();
    fireEvent.click(line);
    expect(setRoute).toHaveBeenCalledWith(expect.objectContaining({ page: 'tasks', queuePane: 'automations' }));
  });

  it('with nothing scheduled or queued, still renders: the teaching line, opening Automations', () => {
    const setRoute = vi.fn();
    renderProjectDetail({}, setRoute);
    const line = screen.getByTestId('trigger-summary');
    // The column: two deliberate lines — a title, then the /automate hint —
    // neither of which wraps.
    expect(screen.getByTestId('status-line-teach-title').textContent).toBe('Nothing scheduled');
    expect(screen.getByTestId('status-line-teach-hint').textContent).toBe('Type /automate to add one');
    expect(screen.getByTestId('status-line-teach-hint').querySelector('kbd')?.textContent).toBe('/automate');
    // <kbd> defaults to monospace; the column has none.
    expect(screen.getByTestId('status-line-teach-hint').querySelector('kbd')?.className).toContain('font-sans');
    expect(line.className).toContain('whitespace-nowrap');
    expect(screen.getByTestId('status-line-teach').innerHTML).not.toMatch(/truncate/);
    expect(line).toHaveAttribute('title', 'Nothing scheduled · Type /automate to add one');
    expect(line).toHaveAttribute('data-tour', 'status-line');
    fireEvent.click(line);
    expect(setRoute).toHaveBeenCalledWith(expect.objectContaining({ page: 'tasks', queuePane: 'automations' }));
  });

  it('stays in the column on a page, and shows as the chosen row while Tasks is open', () => {
    const { unmount } = renderProjectDetail();
    expect(screen.getByTestId('trigger-summary')).not.toHaveAttribute('aria-current');
    unmount();

    const tasks = renderProjectDetail({ page: 'tasks' });
    expect(screen.getByTestId('trigger-summary')).toHaveAttribute('aria-current', 'page');
    tasks.unmount();

    renderProjectDetail({ page: 'settings' });
    expect(screen.getByTestId('trigger-summary')).not.toHaveAttribute('aria-current');
  });

  it('phone keeps the full teaching sentence in its full-width band', () => {
    setViewportWidth(390);
    renderProjectDetail();
    expect(screen.getByTestId('status-line-teach').textContent).toBe(
      'Nothing scheduled · type /automate in the composer to add one',
    );
  });

  it('in the column it is ONE line: no wrapping, truncation with the facts as the tooltip; no fill at rest', () => {
    render(
      <ProjectDetail
        project={mockProject}
        agentStatus="idle"
        route={baseRoute}
        setRoute={vi.fn()}
        triggers={[makeTrigger(), makeTrigger({ id: 'trg-2', enabled: false })]}
      />,
    );
    const line = screen.getByTestId('trigger-summary');
    expect(line.className).toContain('whitespace-nowrap');
    expect(line.className).not.toMatch(/flex-wrap/);
    expect(line.className).not.toMatch(/(^|\s)bg-card(\s|$)/);
    expect(line.className).toContain('hover:bg-card-hover');
    expect(line).toHaveAttribute('title', '2 automations 1 off');
  });

  it('phone: not on a page — the toolbar replaces the whole header', () => {
    setViewportWidth(390);
    renderProjectDetail({ page: 'tasks' });
    expect(screen.queryByTestId('trigger-summary')).toBeNull();
  });
});

describe('ProjectDetail — model header label', () => {
  // P3-F coupled removal: the header spend segment (the old budget_spent_usd
  // "$X.XX" suffix) is GONE. The GET /cost-backed spend corner ships in P3-G.
  // The header now renders the model label alone.
  it('Test 4: model label uses the global default (not agent_name) when project.model is empty', () => {
    const project: Project = {
      ...mockProject,
      model: '',
      agent_name: 'Research Bot',
    };
    render(
      <ProjectDetail
        project={project}
        agentStatus="idle"
        route={baseRoute}
        setRoute={vi.fn()}
        globalDefaultModel="deepseek-chat"
      />,
    );
    expect(screen.getByText('deepseek-chat')).toBeInTheDocument();
    expect(screen.queryByText(/Research Bot/)).not.toBeInTheDocument();
    // No fabricated $0.00 cost suffix.
    expect(screen.queryByText(/\$0\.00/)).not.toBeInTheDocument();
  });

  it('Test 5: with no model anywhere, the header renders no model/cost label', () => {
    const project: Project = {
      ...mockProject,
      model: '',
      agent_name: 'Research Bot',
    };
    render(
      <ProjectDetail
        project={project}
        agentStatus="idle"
        route={baseRoute}
        setRoute={vi.fn()}
        globalDefaultModel=""
      />,
    );
    expect(screen.queryByText(/Research Bot/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\$/)).not.toBeInTheDocument();
    // No "model · cost" separator in the header. (The status line's teaching
    // copy below it has its own "·"; that is not a label.)
    const dots = screen.queryAllByText(/·/).filter((el) => !el.closest('[data-testid="trigger-summary"]'));
    expect(dots).toEqual([]);
  });

  it('prefers the project-pinned model over the global default', () => {
    render(
      <ProjectDetail
        project={{ ...mockProject, model: 'gpt-4o' }}
        agentStatus="idle"
        route={baseRoute}
        setRoute={vi.fn()}
        globalDefaultModel="deepseek-chat"
      />,
    );
    expect(screen.getByText('gpt-4o')).toBeInTheDocument();
    expect(screen.queryByText(/\$/)).not.toBeInTheDocument();
  });
});

describe('ProjectDetail — calendar lens tab visibility', () => {
  it('never shows Calendar or Workbench tabs — they are global workspace surfaces', () => {
    // User decision 2026-07-24: per-project lenses removed; aggregation
    // surfaces live only in the Workspace section of the sidebar.
    const project = { ...mockProject, enabled_connectors: ['google-calendar'] } as Project;
    render(
      <ProjectDetail
        project={project}
        agentStatus="idle"
        route={baseRoute}
        setRoute={vi.fn()}
      />,
    );
    expect(screen.queryByRole('button', { name: /^Calendar/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Workbench/i })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Spec 078 §5.1/§9.10 — who owns route.previewPath
//
// On the Chat tab, above the push threshold, the docked workspace panel
// (rendered by ChatTab) shows the preview. ProjectDetail must suppress its
// overlay drawer there, or a chat path click opens both at once. Everywhere
// else — Queue, Files, settings, mobile, narrow windows — the overlay stays.
// ---------------------------------------------------------------------------

describe('ProjectDetail — overlay drawer vs. the docked panel', () => {
  afterEach(() => setViewportWidth(1024));

  // The drawer is always mounted; `inert` is what says "closed" (it is off
  // screen and out of the a11y tree, so getByRole cannot see it).
  function drawer(container: HTMLElement) {
    const el = container.querySelector('[role="dialog"]');
    expect(el).not.toBeNull();
    return el as HTMLElement;
  }

  it('keeps the overlay closed on a wide chat home — the panel has the preview', () => {
    setViewportWidth(1440);
    const { container } = renderProjectDetail({ previewPath: 'docs/plan.md' });
    expect(drawer(container)).toHaveAttribute('inert');
  });

  it('still opens the overlay on the chat home below the push threshold', () => {
    setViewportWidth(1024);
    const { container } = renderProjectDetail({ previewPath: 'docs/plan.md' });
    expect(drawer(container)).not.toHaveAttribute('inert');
  });

  it('still opens the overlay on the Tasks and Settings pages at any width', () => {
    setViewportWidth(1440);
    const tasks = renderProjectDetail({ page: 'tasks', previewPath: 'docs/plan.md' });
    expect(drawer(tasks.container)).not.toHaveAttribute('inert');
    tasks.unmount();

    const settings = renderProjectDetail({ page: 'settings', previewPath: 'docs/plan.md' });
    expect(drawer(settings.container)).not.toHaveAttribute('inert');
  });
});

// Spec 137 gap 3: below the dock threshold the panel has no handle, so the
// header gets a button for it — rendered only there.
describe('ProjectDetail — the narrow-window panel button', () => {
  afterEach(() => setViewportWidth(1024));

  it('is not rendered at or above the dock threshold', () => {
    setViewportWidth(1440);
    renderProjectDetail();
    expect(screen.queryByTestId('panel-open-button')).toBeNull();
  });

  it('is rendered below the dock threshold and on phones, and opens the panel overlay', () => {
    setViewportWidth(1000);
    const { unmount } = renderProjectDetail();
    const button = screen.getByTestId('panel-open-button');
    expect(button).toHaveAttribute('data-tour', 'panel-button');
    const panel = renderHook(() => usePanelState('proj-42', 'sess-xyz'));
    expect(panel.result.current.overlayOpen).toBe(false);
    act(() => fireEvent.click(button));
    expect(panel.result.current.overlayOpen).toBe(true);
    unmount();

    setViewportWidth(390);
    renderProjectDetail();
    expect(screen.getByTestId('panel-open-button')).toBeInTheDocument();
  });

  it('narrow: the button sits in the column\'s name row, beside the gear', () => {
    setViewportWidth(1000);
    renderProjectDetail();
    const column = screen.getByTestId('project-column');
    expect(within(column).getByTestId('panel-open-button')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Spec 137 items 8–13 — the folder shell: the project is the left column, the
// right area is what was opened from it.
// ---------------------------------------------------------------------------

describe('ProjectDetail — the project column (desktop, narrow)', () => {
  it('holds the header content; the chat area renders no header and no status line', () => {
    setViewportWidth(1440);
    render(
      <ProjectDetail project={mockProject} agentStatus="idle" route={baseRoute} setRoute={vi.fn()}>
        <div data-testid="chat-children" />
      </ProjectDetail>,
    );
    const column = screen.getByTestId('project-column');
    expect(within(column).getByRole('heading', { level: 1 }).textContent).toBe('Alpha Project');
    expect(within(column).getByText('Idle')).toBeInTheDocument();
    expect(within(column).getByTestId('project-credential-chip').textContent).toBe('claude-3-5-sonnet');
    expect(within(column).getByRole('button', { name: /project settings/i })).toBeInTheDocument();
    expect(within(column).getByTestId('trigger-summary')).toBeInTheDocument();
    expect(within(column).getByTestId('session-sidebar')).toBeInTheDocument();
    expect(within(column).getByTestId('session-new-button')).toBeInTheDocument();
    // Order, top to bottom: name, state, status line, sessions.
    const order = [
      within(column).getByRole('heading', { level: 1 }),
      within(column).getByTestId('project-state-row'),
      within(column).getByTestId('trigger-summary'),
      within(column).getByTestId('session-sidebar'),
    ];
    for (let i = 1; i < order.length; i++) {
      expect(order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }

    const main = screen.getByTestId('project-main');
    expect(within(main).getByTestId('chat-children')).toBeInTheDocument();
    expect(within(main).queryByRole('heading')).toBeNull();
    expect(within(main).queryByTestId('trigger-summary')).toBeNull();
    expect(within(main).queryByRole('button', { name: /project settings/i })).toBeNull();
  });

  it('reads as words: no monospace anywhere in the column', () => {
    setViewportWidth(1440);
    sessionsMock.sessions = [session('sess-xyz')];
    renderProjectDetail();
    const column = screen.getByTestId('project-column');
    expect(column.querySelector('.font-mono')).toBeNull();
  });

  it('shows only the model of a "Provider · model" card, one line, the full card name as the tooltip', () => {
    setViewportWidth(1000);
    render(
      <ProjectDetail
        project={{ ...mockProject, model: 'OpenCode Go · deepseek-v4.1-flash' }}
        agentStatus="idle"
        route={baseRoute}
        setRoute={vi.fn()}
      />,
    );
    const chip = within(screen.getByTestId('project-column')).getByTestId('project-credential-chip');
    expect(chip.textContent).toBe('deepseek-v4.1-flash');
    expect(chip).toHaveAttribute('title', 'OpenCode Go · deepseek-v4.1-flash');
    expect(chip.className).toContain('truncate');
  });

  it('keeps the tour anchors on the column (spec 138)', () => {
    setViewportWidth(1000);
    renderProjectDetail();
    const column = screen.getByTestId('project-column');
    for (const id of ['project-header', 'project-settings', 'status-line', 'panel-button']) {
      expect(column.querySelector(`[data-tour="${id}"]`)).not.toBeNull();
    }
  });

  it('is 260px beside a docked panel and 220px on a narrow window, tinted, with no border', () => {
    setViewportWidth(1440);
    const wide = renderProjectDetail();
    const column = screen.getByTestId('project-column');
    expect(column.style.width).toBe('260px');
    expect(column.className).toContain('bg-sidebar');
    expect(column.className).not.toMatch(/\bborder/);
    wide.unmount();

    setViewportWidth(1000);
    renderProjectDetail();
    expect(screen.getByTestId('project-column').style.width).toBe('220px');
  });

  it('draws no lines across the window: no header border, no banded status line', () => {
    setViewportWidth(1440);
    const { container } = renderProjectDetail();
    expect(screen.getByTestId('trigger-summary').parentElement?.className ?? '').not.toContain('border-y');
    expect(screen.getByTestId('session-sidebar').className).not.toMatch(/\bborder|shadow-well/);
    // Nothing folds on desktop: the header→toolbar View Transition is the phone's.
    expect(container.querySelector('.vt-project-header')).toBeNull();
    expect(container.querySelector('.vt-project-title')).toBeNull();
    // The right area swaps as one.
    expect(screen.getByTestId('project-main').className).toContain('vt-project-body');
  });

  it('a page keeps the column free of View Transition names too', () => {
    setViewportWidth(1440);
    const { container } = renderProjectDetail({ page: 'pricing' });
    expect(container.querySelector('.vt-project-header')).toBeNull();
    expect(container.querySelector('.vt-project-title')).toBeNull();
    expect(container.querySelector('.vt-settings-back')).toBeNull();
  });

  it('phone keeps the compact header + banded status line and the View Transition names', () => {
    setViewportWidth(390);
    const { container } = renderProjectDetail();
    expect(screen.queryByTestId('project-column')).toBeNull();
    expect(container.querySelector('.vt-project-header')).not.toBeNull();
    expect(screen.getByTestId('trigger-summary').parentElement?.className).toContain('border-y');
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Alpha Project');
  });

  it('the gear shows as chosen on Settings and on a page under it, not elsewhere', () => {
    for (const [page, chosen] of [
      [undefined, false],
      ['tasks', false],
      ['settings', true],
      ['pricing', true],
    ] as const) {
      const { unmount } = renderProjectDetail({ page });
      const gear = screen.getByRole('button', { name: /project settings/i });
      if (chosen) expect(gear).toHaveAttribute('aria-current', 'page');
      else expect(gear).not.toHaveAttribute('aria-current');
      unmount();
    }
  });

  it('the session you came from keeps a faint "was here" mark while a page is open', () => {
    sessionsMock.sessions = [session('sess-xyz'), session('sess-other')];
    const chat = renderProjectDetail();
    const row = screen.getByTestId('session-list-item-sess-xyz');
    expect(row).toHaveAttribute('aria-selected', 'true');
    expect(row).not.toHaveAttribute('data-was-here');
    chat.unmount();

    renderProjectDetail({ page: 'tasks' });
    const was = screen.getByTestId('session-list-item-sess-xyz');
    expect(was).toHaveAttribute('aria-selected', 'false');
    expect(was).toHaveAttribute('data-was-here', 'true');
    expect(within(was).getByTestId('session-name').className).toContain('text-secondary');
    expect(screen.getByTestId('session-list-item-sess-other')).not.toHaveAttribute('data-was-here');
  });

  it('clicking any session from a page returns to the chat on that session', () => {
    sessionsMock.sessions = [session('sess-xyz'), session('sess-other')];
    const setRoute = vi.fn();
    renderProjectDetail({ page: 'settings' }, setRoute);
    for (const id of ['sess-xyz', 'sess-other']) {
      setRoute.mockClear();
      fireEvent.click(screen.getByTestId(`session-list-item-${id}`));
      const updater = setRoute.mock.calls[0][0] as (prev: Route) => Route;
      expect(updater({ ...baseRoute, page: 'settings' })).toMatchObject({ sessionId: id, page: undefined });
    }
  });

  it('"New session" in the column mints one and opens it in the chat', async () => {
    const setRoute = vi.fn();
    renderProjectDetail({ page: 'tasks' }, setRoute);
    await act(async () => {
      fireEvent.click(screen.getByTestId('session-new-button'));
    });
    expect(mintMock.newSession).toHaveBeenCalledWith('proj-42');
    const updater = setRoute.mock.calls[setRoute.mock.calls.length - 1][0] as (prev: Route) => Route;
    expect(updater({ ...baseRoute, page: 'tasks' })).toMatchObject({ sessionId: 'sess-new', page: undefined });
  });
});

// Spec 137 item 12: maximized Files fills the right area while the column
// stays, and opening anything from the column needs the right area back.
describe('ProjectDetail — maximize and the column', () => {
  function maximized() {
    setViewportWidth(1440);
    const panel = renderHook(() => usePanelState('proj-42', 'sess-xyz'));
    act(() => {
      panel.result.current.expand();
      panel.result.current.maximize();
    });
    return panel;
  }

  it('the column stays visible while the panel is maximized', () => {
    maximized();
    renderProjectDetail();
    const column = screen.getByTestId('project-column');
    expect(column.className).not.toMatch(/(^|\s)hidden(\s|$)/);
    expect(within(column).getByRole('button', { name: /project settings/i })).toBeInTheDocument();
    expect(within(column).getByTestId('trigger-summary')).toBeInTheDocument();
  });

  it('opening a session while maximized restores the panel to its docked width', () => {
    sessionsMock.sessions = [session('sess-xyz'), session('sess-other')];
    const panel = maximized();
    renderProjectDetail();
    act(() => {
      fireEvent.click(screen.getByTestId('session-list-item-sess-other'));
    });
    expect(panel.result.current.maximized).toBe(false);
    expect(panel.result.current.expanded).toBe(true);
  });

  it('the status line and the gear restore it too', () => {
    for (const open of [
      () => fireEvent.click(screen.getByTestId('trigger-summary')),
      () => fireEvent.click(screen.getByRole('button', { name: /project settings/i })),
    ]) {
      const panel = maximized();
      const { unmount } = renderProjectDetail();
      act(open);
      expect(panel.result.current.maximized).toBe(false);
      unmount();
      __resetPanelState();
    }
  });
});
