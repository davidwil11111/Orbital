// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SafeModeSwitch from './SafeModeSwitch';

afterEach(() => cleanup());

const TOOLTIP =
  "On: the agent's commands can only touch this project's folder and folders you grant, and use an allowlist for the network; it asks before going further. Off: the agent has the same access you have in your terminal.";

describe('SafeModeSwitch (spec 117 D2)', () => {
  it('is a switch that reports the next value', () => {
    const onChange = vi.fn();
    render(<SafeModeSwitch checked={false} onChange={onChange} />);
    const sw = screen.getByRole('switch', { name: 'Safe mode' });
    expect(sw.getAttribute('aria-checked')).toBe('false');
    fireEvent.click(sw);
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it('reflects checked and respects disabled', () => {
    const onChange = vi.fn();
    render(<SafeModeSwitch checked onChange={onChange} disabled />);
    const sw = screen.getByRole('switch', { name: 'Safe mode' });
    expect(sw.getAttribute('aria-checked')).toBe('true');
    fireEvent.click(sw);
    expect(onChange).not.toHaveBeenCalled();
  });

  it('hover shows the copy, the ⓘ pins it for touch', () => {
    render(<SafeModeSwitch checked={false} onChange={vi.fn()} />);
    expect(screen.queryByText(TOOLTIP)).toBeNull();
    const row = screen.getByTestId('safe-mode-row');
    fireEvent.mouseEnter(row);
    expect(screen.getByText(TOOLTIP)).toBeTruthy();
    fireEvent.mouseLeave(row);
    expect(screen.queryByText(TOOLTIP)).toBeNull();

    const info = screen.getByRole('button', { name: 'About Safe mode' });
    fireEvent.click(info);
    expect(info.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText(TOOLTIP)).toBeTruthy();
    fireEvent.click(info);
    expect(screen.queryByText(TOOLTIP)).toBeNull();
  });
});
