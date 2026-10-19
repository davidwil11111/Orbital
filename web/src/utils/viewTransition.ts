// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

import { flushSync } from 'react-dom';

type DocumentWithViewTransition = Document & {
  startViewTransition?: (update: () => void) => unknown;
};

/**
 * Run a route change inside a same-document View Transition, so elements that
 * carry the same `view-transition-name` on both sides morph into each other
 * instead of cutting. Used for project chat <-> project settings, where the
 * project header folds into the settings toolbar (see `.vt-*` in index.css).
 *
 * `flushSync` is required: the browser snapshots the new DOM as soon as the
 * callback returns, and a React state update left to batch would still show
 * the old screen in that snapshot.
 *
 * Falls back to a plain update — today's instant switch — where the API is
 * missing (older WebKit) or the user asked for reduced motion.
 */
export function withViewTransition(update: () => void): void {
  const doc = document as DocumentWithViewTransition;
  const reduceMotion =
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (typeof doc.startViewTransition !== 'function' || reduceMotion) {
    update();
    return;
  }
  doc.startViewTransition(() => flushSync(update));
}
