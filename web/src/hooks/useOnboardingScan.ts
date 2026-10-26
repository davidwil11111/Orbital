// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../config';
import type { HistoryStatus, ImportableResponse } from '../utils/onboarding';

export interface OnboardingScan {
  history: HistoryStatus | null;
  importable: ImportableResponse | null;
  /** The importable list could not be fetched at all. */
  error: boolean;
  /** No more updates are coming: the scan is ready, polling gave up, or the
   *  fetch failed. Before this, an empty list may still fill in. */
  settled: boolean;
  /** Fetch again from scratch (the error line's Try again). */
  retry: () => void;
}

interface Options {
  pollMs?: number;
  maxMs?: number;
}

/**
 * The agent-history scan behind the wizard's Agents and Projects steps and the
 * import dialog. GET /onboarding/history first — the call that starts the
 * daemon's background scan — then the importable list, whose candidates are
 * there at once while the groups arrive when the scan is ready. Polls both
 * while `status == "scanning"` (every 1.5 s, at most 20 s).
 */
export function useOnboardingScan({ pollMs = 1500, maxMs = 20000 }: Options = {}): OnboardingScan {
  const [history, setHistory] = useState<HistoryStatus | null>(null);
  const [importable, setImportable] = useState<ImportableResponse | null>(null);
  const [error, setError] = useState(false);
  const [settled, setSettled] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const optsRef = useRef({ pollMs, maxMs });

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const started = Date.now();
    const { pollMs: every, maxMs: limit } = optsRef.current;

    const tick = async () => {
      const h = await api<HistoryStatus>('/api/v2/onboarding/history').catch(() => null);
      if (cancelled) return;
      if (h && typeof h === 'object' && 'by_agent' in h) setHistory(h);
      let data: ImportableResponse | null = null;
      try {
        data = await api<ImportableResponse>('/api/v2/onboarding/importable-projects');
      } catch {
        if (cancelled) return;
        setError(true);
        setSettled(true);
        return;
      }
      if (cancelled) return;
      const body: ImportableResponse = {
        status: data?.status,
        candidates: Array.isArray(data?.candidates) ? data.candidates : [],
        groups: Array.isArray(data?.groups) ? data.groups : [],
      };
      setImportable(body);
      if (body.status === 'scanning' && Date.now() - started < limit) {
        timer = setTimeout(() => { void tick(); }, every);
      } else {
        setSettled(true);
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [attempt]);

  const retry = useCallback(() => {
    setError(false);
    setSettled(false);
    setAttempt((a) => a + 1);
  }, []);

  return { history, importable, error, settled, retry };
}
