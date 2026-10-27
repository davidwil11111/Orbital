// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

/**
 * Upload one file into a project's workspace (`POST /files/upload`).
 *
 * Lifted out of FileExplorer (spec 137 gap 2) so the workspace panel's Files
 * view can offer the same affordance the old Files tab had, from one
 * implementation: the same 10 MB cap, the same relay auth header, the same
 * error text.
 */
import { useCallback, useState } from 'react';
import { BASE_URL, isRelayMode } from '../config';
import { useT } from '../i18n/useT';

export const MAX_UPLOAD_SIZE = 10 * 1024 * 1024; // 10MB

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export interface WorkspaceUpload {
  /** Resolves true once the file is on disk; false on a size/HTTP error (see `error`). */
  upload: (file: File) => Promise<boolean>;
  uploading: boolean;
  error: string | null;
  clearError: () => void;
}

export function useWorkspaceUpload(projectId: string): WorkspaceUpload {
  const t = useT();
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const upload = useCallback(
    async (file: File): Promise<boolean> => {
      if (file.size > MAX_UPLOAD_SIZE) {
        setError(t('fileExplorer.tooLarge', { size: formatSize(file.size) }));
        return false;
      }
      setUploading(true);
      setError(null);
      try {
        const formData = new FormData();
        formData.append('file', file);
        const baseUrl = isRelayMode ? window.location.origin : BASE_URL;
        const url = `${baseUrl}/api/v2/projects/${encodeURIComponent(projectId)}/files/upload`;
        const headers: Record<string, string> = {};
        if (isRelayMode) {
          try {
            const token = localStorage.getItem('relay_jwt');
            if (token) headers['Authorization'] = `Bearer ${token}`;
          } catch {
            // ignore
          }
        }
        const response = await fetch(url, { method: 'POST', body: formData, headers });
        if (!response.ok) {
          const body = await response.text();
          let detail: string;
          try {
            const parsed = JSON.parse(body);
            detail = parsed.detail ?? body;
          } catch {
            detail = body;
          }
          throw new Error(detail || t('fileExplorer.uploadFailed'));
        }
        return true;
      } catch (err) {
        setError(err instanceof Error ? err.message : t('fileExplorer.uploadFailed'));
        return false;
      } finally {
        setUploading(false);
      }
    },
    // `t` is a fresh closure per render; the locale it reads is what matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [projectId],
  );

  const clearError = useCallback(() => setError(null), []);

  return { upload, uploading, error, clearError };
}
