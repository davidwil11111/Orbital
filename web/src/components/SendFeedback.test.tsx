// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

// @vitest-environment jsdom

/**
 * Send feedback with diagnostics (spec 113 D). Consent is the preview: the
 * dialog shows the daemon's sanitised bundle and its size before Send is
 * possible, Send posts the message + context (never the preview text — the
 * daemon rebuilds it), and a failed send keeps what the user typed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

import SendFeedback, { SendDiagnosticsLink } from './SendFeedback';

let fetchCalls: { url: string; init?: RequestInit }[] = [];
let previewResponse: unknown = {
  text: 'Orbital diagnostic bundle\ninstall_id: inst_0123456789ab\n== daemon.log ==\nhello',
  bytes: 12345,
  telemetry_enabled: true,
};
let sendImpl: () => Promise<Response>;

function jsonResponse(body: unknown, ok = true, status = 200) {
  return Promise.resolve({ ok, status, json: () => Promise.resolve(body) } as Response);
}

beforeEach(() => {
  fetchCalls = [];
  sendImpl = () => jsonResponse({ ok: true, bytes: 12345 });
  vi.stubGlobal('fetch', (url: string, init?: RequestInit) => {
    fetchCalls.push({ url, init });
    if (url.includes('/feedback/send')) return sendImpl();
    return jsonResponse(previewResponse);
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  previewResponse = { text: 'bundle', bytes: 12345, telemetry_enabled: true };
});

describe('SendDiagnosticsLink', () => {
  it('opens the dialog with the failure context and shows the preview + size', async () => {
    render(<SendDiagnosticsLink context={{ kind: 'login_failed', agent: 'codex', detail: 'exit 1' }} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('Send diagnostic logs'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    const pre = await screen.findByTestId('feedback-preview');
    expect(pre.textContent).toContain('install_id: inst_0123456789ab');
    expect(screen.getByText('12.1 KB will be sent.')).toBeInTheDocument();
    expect(screen.getByText('Context: sign-in to codex failed')).toBeInTheDocument();

    const previewCall = fetchCalls.find((c) => c.url.includes('/feedback/preview'));
    expect(previewCall?.url).toContain('kind=login_failed');
    expect(previewCall?.url).toContain('agent=codex');
    expect(previewCall?.url).toContain('detail=exit+1');
  });

  it('sends the message and context, never the preview text, and confirms with the size', async () => {
    render(<SendDiagnosticsLink context={{ kind: 'dispatch_failed', agent: 'claude-code' }} />);
    fireEvent.click(screen.getByText('Send diagnostic logs'));
    await screen.findByTestId('feedback-preview');

    fireEvent.change(screen.getByTestId('feedback-message'), { target: { value: 'it hangs' } });
    fireEvent.click(screen.getByTestId('feedback-send'));

    await screen.findByText('Sent (12.1 KB).');
    const send = fetchCalls.find((c) => c.url.includes('/feedback/send'));
    expect(send?.init?.method).toBe('POST');
    const body = JSON.parse(String(send?.init?.body));
    expect(body).toEqual({
      message: 'it hangs',
      context: { kind: 'dispatch_failed', agent: 'claude-code' },
    });
    expect(screen.queryByTestId('feedback-send')).not.toBeInTheDocument();
  });

  it('keeps the message and shows the retry-later line when the upload fails', async () => {
    sendImpl = () => Promise.reject(new TypeError('Failed to fetch'));
    render(<SendDiagnosticsLink context={{ kind: 'general' }} />);
    fireEvent.click(screen.getByText('Send diagnostic logs'));
    await screen.findByTestId('feedback-preview');

    fireEvent.change(screen.getByTestId('feedback-message'), { target: { value: 'keep me' } });
    fireEvent.click(screen.getByTestId('feedback-send'));

    await screen.findByText('Sending failed. You can try again later.');
    expect(screen.getByTestId('feedback-message')).toHaveValue('keep me');
    expect(screen.getByTestId('feedback-send')).not.toBeDisabled();
  });

  it('treats a non-2xx daemon answer as the same failure', async () => {
    sendImpl = () => jsonResponse({ detail: { error: 'upload_failed', status: 404 } }, false, 502);
    render(<SendDiagnosticsLink context={{ kind: 'general' }} />);
    fireEvent.click(screen.getByText('Send diagnostic logs'));
    await screen.findByTestId('feedback-preview');
    fireEvent.click(screen.getByTestId('feedback-send'));
    await screen.findByText('Sending failed. You can try again later.');
  });

  it('says in one line that usage statistics are off when they are', async () => {
    previewResponse = { text: 'bundle', bytes: 10, telemetry_enabled: false };
    render(<SendDiagnosticsLink context={{ kind: 'general' }} />);
    fireEvent.click(screen.getByText('Send diagnostic logs'));
    await screen.findByTestId('feedback-preview');
    expect(screen.getByTestId('feedback-telemetry-off')).toBeInTheDocument();
  });

  it('does not allow sending before the preview has loaded, and reports a preview error', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('down')));
    render(<SendDiagnosticsLink context={{ kind: 'general' }} />);
    fireEvent.click(screen.getByText('Send diagnostic logs'));
    await screen.findByText("Couldn't build the preview.");
    expect(screen.getByTestId('feedback-send')).toBeDisabled();
  });

  it('closes on Escape and on Cancel', async () => {
    render(<SendDiagnosticsLink context={{ kind: 'general' }} />);
    fireEvent.click(screen.getByText('Send diagnostic logs'));
    await screen.findByTestId('feedback-preview');
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    fireEvent.click(screen.getByText('Send diagnostic logs'));
    await screen.findByTestId('feedback-preview');
    fireEvent.click(screen.getByText('Cancel'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('SendFeedback (Global Settings entry)', () => {
  it('renders the intro and a button that opens the dialog with no context', async () => {
    render(<SendFeedback />);
    expect(fetchCalls).toEqual([]); // nothing is fetched until the user asks
    fireEvent.click(screen.getByTestId('feedback-open'));
    await screen.findByTestId('feedback-preview');
    const previewCall = fetchCalls.find((c) => c.url.includes('/feedback/preview'));
    expect(previewCall?.url).not.toContain('kind=');
    expect(screen.queryByText(/^Context:/)).not.toBeInTheDocument();
  });
});
