// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Spec 134 Part B (P1): a background job group renders in the fanout card —
// rows are jobs (command, state, duration) with a Cancel button while the
// job runs. Rows are not drill-ins (a job has no transcript).

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import FanoutCard from './FanoutCard';

const apiMock = vi.fn();
vi.mock('../config', () => ({ api: (...args: unknown[]) => apiMock(...args) }));

const jobs = [
  { handle: 'job_aaaa0001', label: 'npm run build', job: { project_id: 'proj_1', job_id: 'job_aaaa0001' } },
  { handle: 'job_aaaa0002', label: 'pytest -q', job: { project_id: 'proj_1', job_id: 'job_aaaa0002', agent: 'codex' } },
];

beforeEach(() => {
  apiMock.mockReset();
  apiMock.mockResolvedValue({ jobs: [] });
});

describe('FanoutCard — background jobs', () => {
  it('titles the card as background jobs and shows each command', () => {
    render(<FanoutCard fanoutId="grp_1" tasks={jobs} statuses={{}} startedAtMs={Date.now()}
                       onSelectTask={() => {}} />);
    expect(screen.getByTestId('fanout-card')).toHaveTextContent('2 background jobs');
    expect(screen.getByTestId('fanout-row-job_aaaa0001')).toHaveTextContent('npm run build');
    expect(screen.getByTestId('fanout-row-job_aaaa0002')).toHaveTextContent('codex');
  });

  it('labels job states and offers Cancel only while running', () => {
    render(<FanoutCard fanoutId="grp_1" tasks={jobs}
                       statuses={{ job_aaaa0001: { status: 'running' }, job_aaaa0002: { status: 'error', completedAtMs: Date.now() } }}
                       startedAtMs={Date.now()} onSelectTask={() => {}} />);
    expect(screen.getByTestId('fanout-status-job_aaaa0001')).toHaveTextContent('Running');
    expect(screen.getByTestId('fanout-status-job_aaaa0002')).toHaveTextContent('Failed');
    expect(screen.getByTestId('job-cancel-job_aaaa0001')).toBeInTheDocument();
    expect(screen.queryByTestId('job-cancel-job_aaaa0002')).toBeNull();
  });

  it('done and cancelled read as such', () => {
    render(<FanoutCard fanoutId="grp_1" tasks={jobs}
                       statuses={{ job_aaaa0001: { status: 'completed' }, job_aaaa0002: { status: 'interrupted' } }}
                       startedAtMs={Date.now()} onSelectTask={() => {}} />);
    expect(screen.getByTestId('fanout-status-job_aaaa0001')).toHaveTextContent('Done');
    expect(screen.getByTestId('fanout-status-job_aaaa0002')).toHaveTextContent('Stopped');
  });

  it('Cancel posts to the job cancel route and shows it is stopping', async () => {
    let resolve: (v: unknown) => void = () => {};
    apiMock.mockReturnValue(new Promise((r) => { resolve = r; }));
    const onSelect = vi.fn();
    render(<FanoutCard fanoutId="grp_1" tasks={jobs} statuses={{}} startedAtMs={Date.now()}
                       onSelectTask={onSelect} />);
    fireEvent.click(screen.getByTestId('job-cancel-job_aaaa0001'));
    expect(apiMock).toHaveBeenCalledWith('/api/v2/projects/proj_1/jobs/job_aaaa0001/cancel', { method: 'POST' });
    expect(screen.getByTestId('job-cancel-job_aaaa0001')).toBeDisabled();
    expect(screen.getByTestId('job-cancel-job_aaaa0001')).toHaveTextContent('Stopping');
    resolve({ job: { state: 'cancelled' } });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('a failed cancel says so and can be retried', async () => {
    apiMock.mockRejectedValue(new Error('404'));
    render(<FanoutCard fanoutId="grp_1" tasks={jobs} statuses={{}} startedAtMs={Date.now()}
                       onSelectTask={() => {}} />);
    fireEvent.click(screen.getByTestId('job-cancel-job_aaaa0001'));
    await waitFor(() => expect(screen.getByTestId('job-cancel-job_aaaa0001')).toHaveTextContent("Couldn't stop"));
    expect(screen.getByTestId('job-cancel-job_aaaa0001')).not.toBeDisabled();
  });

  it("a job row's own end time wins over the batch time history stamps on every row", () => {
    const start = Date.now() - 125_000;
    const ended = [{ ...jobs[0], job: { ...jobs[0].job, ended_at_ms: start + 15_000 } }];
    render(<FanoutCard fanoutId="grp_1" tasks={ended}
                       statuses={{ job_aaaa0001: { status: 'completed', completedAtMs: start + 30_000 } }}
                       startedAtMs={start} onSelectTask={() => {}} />);
    expect(screen.getByTestId('fanout-row-job_aaaa0001')).toHaveTextContent('15s');
  });

  it('a row with a known end time freezes its duration', () => {
    const start = Date.now() - 125_000;
    const ended = [{ ...jobs[0], job: { ...jobs[0].job, ended_at_ms: start + 65_000 } }];
    render(<FanoutCard fanoutId="grp_1" tasks={ended} statuses={{ job_aaaa0001: { status: 'completed' } }}
                       startedAtMs={start} onSelectTask={() => {}} />);
    expect(screen.getByTestId('fanout-row-job_aaaa0001')).toHaveTextContent('1m 5s');
  });

  it('on mount, rows not known to be finished take their state from the daemon', async () => {
    apiMock.mockResolvedValue({ jobs: [
      { job_id: 'job_aaaa0001', status: 'completed', ended_at_ms: Date.now() },
      { job_id: 'job_aaaa0002', status: 'running', ended_at_ms: null },
    ] });
    render(<FanoutCard fanoutId="grp_1" tasks={jobs} statuses={{}} startedAtMs={Date.now()}
                       onSelectTask={() => {}} />);
    expect(apiMock).toHaveBeenCalledWith('/api/v2/projects/proj_1/jobs');
    await waitFor(() => expect(screen.getByTestId('fanout-status-job_aaaa0001')).toHaveTextContent('Done'));
    expect(screen.queryByTestId('job-cancel-job_aaaa0001')).toBeNull();
    expect(screen.getByTestId('job-cancel-job_aaaa0002')).toBeInTheDocument();
  });

  it('a finished state from the live overlay is not overridden, and nothing is fetched', () => {
    render(<FanoutCard fanoutId="grp_1" tasks={jobs}
                       statuses={{ job_aaaa0001: { status: 'completed' }, job_aaaa0002: { status: 'error' } }}
                       startedAtMs={Date.now()} onSelectTask={() => {}} />);
    expect(apiMock).not.toHaveBeenCalled();
  });
});
