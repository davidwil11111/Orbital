// Orbital — An operating system for AI agents
// Copyright (C) 2026 Orbital Contributors
// SPDX-License-Identifier: GPL-3.0-or-later

// @vitest-environment jsdom

import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import ProviderCombobox from './ProviderCombobox';

afterEach(() => cleanup());

const OPTIONS = [
  { key: 'deepseek', label: 'DeepSeek' },
  { key: 'moonshot', label: 'Moonshot (Kimi)' },
  { key: 'openai', label: 'OpenAI' },
  { key: 'openrouter', label: 'OpenRouter' },
];
const CUSTOM = { key: '__custom__', label: 'Custom / Self-Hosted' };

function setup(value = 'deepseek') {
  const onChange = vi.fn();
  render(<ProviderCombobox options={OPTIONS} custom={CUSTOM} value={value} onChange={onChange} label="Provider" />);
  const input = screen.getByRole('combobox') as HTMLInputElement;
  return { onChange, input };
}

const optionLabels = () => screen.queryAllByRole('option').map((o) => o.textContent);

describe('ProviderCombobox', () => {
  it('shows the selected provider and keeps the listbox closed until focused', () => {
    const { input } = setup('openai');
    expect(input.value).toBe('OpenAI');
    expect(input.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('filters by display name or key as you type; Custom is always offered', () => {
    const { input } = setup();
    fireEvent.focus(input);
    expect(optionLabels()).toEqual(['DeepSeek', 'Moonshot (Kimi)', 'OpenAI', 'OpenRouter', 'Custom / Self-Hosted']);
    fireEvent.change(input, { target: { value: 'open' } });
    expect(optionLabels()).toEqual(['OpenAI', 'OpenRouter', 'Custom / Self-Hosted']);
    fireEvent.change(input, { target: { value: 'kimi' } });
    expect(optionLabels()).toEqual(['Moonshot (Kimi)', 'Custom / Self-Hosted']);
    // by key
    fireEvent.change(input, { target: { value: 'moonsh' } });
    expect(optionLabels()).toEqual(['Moonshot (Kimi)', 'Custom / Self-Hosted']);
  });

  it('shows the empty-state line when nothing matches, with Custom still selectable', () => {
    const { input, onChange } = setup();
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'zzz' } });
    expect(screen.getByTestId('provider-combobox-empty').textContent).toBe(
      'No provider matches. Pick Custom / Self-Hosted to type an endpoint.',
    );
    expect(optionLabels()).toEqual(['Custom / Self-Hosted']);
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('__custom__');
  });

  it('ArrowDown / ArrowUp move the active option (aria-activedescendant) and Enter selects it', () => {
    const { input, onChange } = setup();
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'open' } });
    // first match is active
    const active = () => document.getElementById(input.getAttribute('aria-activedescendant') ?? '')?.textContent;
    expect(active()).toBe('OpenAI');
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    expect(active()).toBe('OpenRouter');
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    expect(active()).toBe('Custom / Self-Hosted');
    fireEvent.keyDown(input, { key: 'ArrowDown' }); // stays on the last
    expect(active()).toBe('Custom / Self-Hosted');
    fireEvent.keyDown(input, { key: 'ArrowUp' });
    expect(active()).toBe('OpenRouter');
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('openrouter');
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('Escape closes and restores the selected name', () => {
    const { input, onChange } = setup('deepseek');
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'op' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(input.value).toBe('DeepSeek');
    expect(onChange).not.toHaveBeenCalled();
  });

  it('a click on an option selects it', () => {
    const { input, onChange } = setup();
    fireEvent.focus(input);
    fireEvent.mouseDown(screen.getByRole('option', { name: 'OpenAI' }));
    expect(onChange).toHaveBeenCalledWith('openai');
  });

  it('marks the current value aria-selected', () => {
    const { input } = setup('openai');
    fireEvent.focus(input);
    expect(screen.getByRole('option', { name: 'OpenAI' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('option', { name: 'DeepSeek' }).getAttribute('aria-selected')).toBe('false');
  });
});
