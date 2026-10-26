// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

import { api } from '../config';

/** The client-side events the daemon's allowlist accepts (spec 138). */
export type TelemetryEvent =
  | 'wizard_step_reached'
  | 'agent_located'
  | 'leftoff_shown'
  | 'leftoff_opened'
  | 'agent_switch_after_open'
  | 'projects_unchecked';

/** Fire-and-forget: counters, enums and booleans only — never paths, names
 *  or free text. A failure (offline, older daemon) is dropped silently. */
export function sendTelemetry(
  event: TelemetryEvent,
  fields: Record<string, string | number | boolean> = {},
): void {
  try {
    void Promise.resolve(
      api('/api/v2/telemetry/event', {
        method: 'POST',
        body: JSON.stringify({ event, fields }),
      }),
    ).catch(() => {});
  } catch {
    /* a mocked or missing api must never break the caller */
  }
}
