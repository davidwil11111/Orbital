// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

// @vitest-environment jsdom

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { LocaleProvider } from '../i18n/LocaleContext';

import NotDetectedRow from './NotDetectedRow';

// The daemon seam: the three locate routes, keyed by method + path suffix.
const apiMock = vi.hoisted(() => vi.fn());
vi.mock('../config', () => ({ api: apiMock }));

const CODEX_ANSWER = `agent: Codex
command: /opt/homebrew/bin/codex
target: /opt/homebrew/lib/node_modules/@openai/codex/bin/codex.js
version: 0.157.1
env: {}
path:
  - /opt/homebrew/opt/node@24/bin`;

function routeApi(overrides: {
  test?: (body: { answer: string }) => unknown;
  put?: (body: { answer: string }) => unknown;
} = {}) {
  apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
    const body = options?.body ? JSON.parse(String(options.body)) : undefined;
    if (path.endsWith('/locate-prompt')) {
      return { slug: 'codex', prompt: 'Orbital (a desktop app) could not find Codex on this machine.' };
    }
    if (path.endsWith('/locate/test')) {
      return overrides.test
        ? overrides.test(body)
        : { ok: true, binary: '/opt/homebrew/bin/codex', path_prepend: ['/opt/homebrew/opt/node@24/bin'],
            env_names: [], version_output: 'codex-cli 0.157.1' };
    }
    if (path.endsWith('/locate') && options?.method === 'PUT') {
      return overrides.put ? overrides.put(body) : { slug: 'codex', installed: true };
    }
    throw new Error(`unexpected api call ${options?.method ?? 'GET'} ${path}`);
  });
}

beforeEach(() => {
  apiMock.mockReset();
  routeApi();
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});

const row = () => screen.getByTestId('sub-agent-not-detected-codex');
const answerBox = () => screen.getByTestId('sub-agent-locate-answer-codex') as HTMLTextAreaElement;
const testBtn = () => screen.getByTestId('sub-agent-locate-test-codex') as HTMLButtonElement;
const saveBtn = () => screen.getByTestId('sub-agent-locate-save-codex') as HTMLButtonElement;

describe('NotDetectedRow (the locate card)', () => {
  it('names the agent, says it is not detected, and asks for the paste', () => {
    render(<NotDetectedRow slug="codex" name="Codex" />);
    expect(row()).toHaveTextContent('Codex');
    expect(row()).toHaveTextContent('Not detected');
    expect(row()).toHaveTextContent('Copy a prompt into Codex and paste its answer below.');
    expect(row()).not.toHaveTextContent('zprofile');
    expect(row()).not.toHaveTextContent('Refresh');
    expect(answerBox().placeholder).toBe('Paste the answer here');
  });

  it('a fallback hint replaces the paste-into-the-agent line (spec 127 D3)', () => {
    render(
      <NotDetectedRow
        slug="codex"
        name="Codex"
        fallbackHint="Or point Orbital at a Codex you installed yourself."
      />,
    );
    expect(row()).toHaveTextContent('Or point Orbital at a Codex you installed yourself.');
    expect(row()).not.toHaveTextContent('Copy a prompt into Codex and paste its answer below.');
    // The prompt is still on offer: the copy button stays.
    expect(screen.getByTestId('sub-agent-locate-copy-codex')).toBeInTheDocument();
  });

  it('a passing Test with no version output says so instead of a blank (spec 127 D2)', async () => {
    routeApi({
      test: () => ({ ok: true, binary: '/opt/dsh/node_modules/.bin/dsh-acp-demo', path_prepend: [],
        env_names: [], version_output: '' }),
    });
    render(<NotDetectedRow slug="codex" name="Codex" />);
    fireEvent.change(answerBox(), { target: { value: '/opt/dsh/node_modules/.bin/dsh-acp-demo' } });
    fireEvent.click(testBtn());
    await waitFor(() => expect(screen.getByTestId('sub-agent-locate-result-codex')).toBeInTheDocument());
    expect(screen.getByTestId('sub-agent-locate-result-codex')).toHaveTextContent(
      'Found Codex at /opt/dsh/node_modules/.bin/dsh-acp-demo (no version check for this agent)',
    );
    expect(saveBtn().disabled).toBe(false);
  });

  it('Copy fetches the prompt once, writes it to the clipboard, and says Copied', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<NotDetectedRow slug="codex" name="Codex" />);
    fireEvent.click(screen.getByTestId('sub-agent-locate-copy-codex'));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(
      'Orbital (a desktop app) could not find Codex on this machine.'));
    expect(screen.getByTestId('sub-agent-locate-copy-codex')).toHaveTextContent('Copied');
    fireEvent.click(screen.getByTestId('sub-agent-locate-copy-codex'));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(2));
    const promptCalls = apiMock.mock.calls.filter(c => String(c[0]).endsWith('/locate-prompt'));
    expect(promptCalls).toHaveLength(1);
  });

  it('shows the prompt text when the clipboard is unavailable', async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } });
    render(<NotDetectedRow slug="codex" name="Codex" />);
    fireEvent.click(screen.getByTestId('sub-agent-locate-copy-codex'));
    await waitFor(() => expect(screen.getByTestId('sub-agent-locate-prompt-codex'))
      .toHaveTextContent('could not find Codex'));
  });

  it('Test is disabled until there is text; Save until a Test passed on that text', async () => {
    render(<NotDetectedRow slug="codex" name="Codex" />);
    expect(testBtn().disabled).toBe(true);
    expect(saveBtn().disabled).toBe(true);
    fireEvent.change(answerBox(), { target: { value: CODEX_ANSWER } });
    expect(testBtn().disabled).toBe(false);
    expect(saveBtn().disabled).toBe(true);
    fireEvent.click(testBtn());
    await waitFor(() => expect(screen.getByTestId('sub-agent-locate-result-codex'))
      .toHaveTextContent('Found Codex 0.157.1 at /opt/homebrew/bin/codex'));
    expect(saveBtn().disabled).toBe(false);
    const [path, options] = apiMock.mock.calls.find(c => String(c[0]).endsWith('/locate/test'))!;
    expect(path).toBe('/api/v2/settings/sub-agents/codex/locate/test');
    expect(JSON.parse(String((options as RequestInit).body))).toEqual({ answer: CODEX_ANSWER });
  });

  it('editing the box after a Test disables Save again and hides the stale result', async () => {
    render(<NotDetectedRow slug="codex" name="Codex" />);
    fireEvent.change(answerBox(), { target: { value: CODEX_ANSWER } });
    fireEvent.click(testBtn());
    await waitFor(() => expect(saveBtn().disabled).toBe(false));
    fireEvent.change(answerBox(), { target: { value: CODEX_ANSWER + '\nextra: x' } });
    expect(saveBtn().disabled).toBe(true);
    expect(screen.queryByTestId('sub-agent-locate-result-codex')).toBeNull();
  });

  it('shows the daemon error verbatim and keeps Save disabled', async () => {
    routeApi({ test: () => ({ ok: false, error: 'env: node: No such file or directory' }) });
    render(<NotDetectedRow slug="codex" name="Codex" />);
    fireEvent.change(answerBox(), { target: { value: '/opt/homebrew/bin/codex' } });
    fireEvent.click(testBtn());
    await waitFor(() => expect(screen.getByTestId('sub-agent-locate-result-codex'))
      .toHaveTextContent('env: node: No such file or directory'));
    expect(saveBtn().disabled).toBe(true);
  });

  it('Save PUTs the tested text and refetches the list', async () => {
    const onChanged = vi.fn();
    render(<NotDetectedRow slug="codex" name="Codex" onChanged={onChanged} />);
    fireEvent.change(answerBox(), { target: { value: '/opt/homebrew/bin/codex' } });
    fireEvent.click(testBtn());
    await waitFor(() => expect(saveBtn().disabled).toBe(false));
    fireEvent.click(saveBtn());
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    const put = apiMock.mock.calls.find(c => (c[1] as RequestInit | undefined)?.method === 'PUT')!;
    expect(put[0]).toBe('/api/v2/settings/sub-agents/codex/locate');
    expect(JSON.parse(String((put[1] as RequestInit).body))).toEqual({ answer: '/opt/homebrew/bin/codex' });
  });

  it('a refused Save shows the daemon detail', async () => {
    routeApi({ put: () => { throw new Error("'/opt/homebrew/bin/codex' is not a file on this machine."); } });
    render(<NotDetectedRow slug="codex" name="Codex" />);
    fireEvent.change(answerBox(), { target: { value: '/opt/homebrew/bin/codex' } });
    fireEvent.click(testBtn());
    await waitFor(() => expect(saveBtn().disabled).toBe(false));
    fireEvent.click(saveBtn());
    await waitFor(() => expect(row()).toHaveTextContent('is not a file on this machine'));
  });

  it('the gone state names the missing file and still offers the paste', () => {
    render(
      <NotDetectedRow
        slug="codex"
        name="Codex"
        overridePath="/opt/homebrew/bin/codex"
        overrideMissing
      />,
    );
    expect(screen.getByTestId('sub-agent-locate-gone-codex'))
      .toHaveTextContent('The file you chose is gone: /opt/homebrew/bin/codex');
    expect(answerBox()).toBeInTheDocument();
  });

  it('renders in Simplified Chinese', () => {
    localStorage.setItem('orbital.locale', 'zh');
    render(
      <LocaleProvider>
        <NotDetectedRow slug="codex" name="Codex" overridePath="/x/codex" overrideMissing />
      </LocaleProvider>,
    );
    expect(row()).toHaveTextContent('未检测到');
    expect(row()).toHaveTextContent('将提示词复制给 Codex，把它的回答粘贴到下面。');
    expect(row()).toHaveTextContent('你选择的文件已不存在：/x/codex');
    expect(answerBox().placeholder).toBe('把回答粘贴到这里');
    expect(testBtn()).toHaveTextContent('测试');
    expect(saveBtn()).toHaveTextContent('保存');
  });
});
