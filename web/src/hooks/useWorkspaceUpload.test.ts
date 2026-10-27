// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MAX_UPLOAD_SIZE, useWorkspaceUpload } from './useWorkspaceUpload';

afterEach(() => vi.unstubAllGlobals());

function fileOfSize(size: number, name = 'notes.md'): File {
  const f = new File(['x'], name);
  Object.defineProperty(f, 'size', { value: size });
  return f;
}

describe('useWorkspaceUpload', () => {
  it('POSTs the file to the project upload route and reports success', async () => {
    const fetchMock = vi.fn(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useWorkspaceUpload('proj 1'));
    let ok = false;
    await act(async () => {
      ok = await result.current.upload(fileOfSize(10));
    });
    expect(ok).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain('/api/v2/projects/proj%201/files/upload');
    expect(init.method).toBe('POST');
    expect(init.body).toBeInstanceOf(FormData);
    expect(result.current.error).toBeNull();
    expect(result.current.uploading).toBe(false);
  });

  it('refuses a file over the cap without a request', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useWorkspaceUpload('p'));
    let ok = true;
    await act(async () => {
      ok = await result.current.upload(fileOfSize(MAX_UPLOAD_SIZE + 1));
    });
    expect(ok).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(result.current.error).toMatch(/10\.0 MB/);
  });

  it('surfaces the daemon\'s detail on an HTTP error', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'Disk full' }), { status: 500 })));
    const { result } = renderHook(() => useWorkspaceUpload('p'));
    await act(async () => {
      await result.current.upload(fileOfSize(10));
    });
    expect(result.current.error).toBe('Disk full');
    act(() => result.current.clearError());
    expect(result.current.error).toBeNull();
  });
});
