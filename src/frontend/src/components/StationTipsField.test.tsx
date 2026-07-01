import { createRef } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import '../i18n';
import StationTipsField, { type StationTipsFieldHandle } from './StationTipsField';
import type { StationTips } from '../api/types';

const ADD_LINK = /Link hinzufügen|Add link/i;

describe('StationTipsField', () => {
  it('renders the empty state: header field, add-link button, no link rows', () => {
    render(<StationTipsField value={null} onChange={vi.fn()} />);
    expect(screen.getByRole('button', { name: ADD_LINK })).toBeInTheDocument();
    // No link row rendered yet, so there's no URL input.
    expect(screen.queryByLabelText(/^URL$/i)).toBeNull();
  });

  it('adding a link calls onChange with the right shape', async () => {
    const onChange = vi.fn();
    render(<StationTipsField value={null} onChange={onChange} />);

    await userEvent.click(screen.getByRole('button', { name: ADD_LINK }));

    expect(onChange).toHaveBeenCalledWith({
      header: {},
      links: [{ label: {}, url: '' }],
    });
  });

  it('clearing the only header text with no links calls onChange(null)', async () => {
    const onChange = vi.fn();
    const tips: StationTips = { header: { de: 'Hallo' }, links: [] };
    render(<StationTipsField value={tips} onChange={onChange} />);

    const editor = await screen.findByRole('textbox', { name: 'Popup-Überschrift (Deutsch)' });
    expect(editor.textContent).toBe('Hallo');
    await userEvent.clear(editor);

    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it('removing the only link (with no header text) calls onChange(null)', async () => {
    const onChange = vi.fn();
    const tips: StationTips = {
      header: {},
      links: [{ label: { de: 'Karte' }, url: 'https://example.com' }],
    };
    render(<StationTipsField value={tips} onChange={onChange} />);

    await userEvent.click(screen.getByRole('button', { name: /Karte|entfernen|Remove/i }));

    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it('shows the invalid-URL helper text when a link URL does not start with http(s)', () => {
    const tips: StationTips = {
      header: {},
      links: [{ label: { de: 'Karte' }, url: 'ftp://example.com' }],
    };
    render(<StationTipsField value={tips} onChange={vi.fn()} />);

    expect(
      screen.getByText(/muss mit http:\/\/ oder https:\/\/ beginnen|must start with http/i),
    ).toBeInTheDocument();
  });

  it('does not show the invalid-URL helper text for an empty or valid URL', () => {
    const tips: StationTips = {
      header: {},
      links: [{ label: { de: 'Karte' }, url: 'https://example.com' }],
    };
    render(<StationTipsField value={tips} onChange={vi.fn()} />);

    expect(
      screen.queryByText(/muss mit http:\/\/ oder https:\/\/ beginnen|must start with http/i),
    ).toBeNull();
  });

  // The refused field is marked, described and takes the focus.
  describe('a refused save', () => {
    const noLabel: StationTips = {
      header: { de: 'Tipps' },
      links: [
        { label: { de: 'Karte' }, url: 'https://example.com' },
        { label: {}, url: 'https://example.org' },
      ],
    };
    const labelField = (index: number) =>
      screen.getByRole('textbox', { name: `Link ${index}: Beschriftung (Deutsch)` });

    it('marks the empty label and ties the error text to it', () => {
      render(<StationTipsField value={noLabel} onChange={vi.fn()} showErrors />);

      const message = screen.getByText('Jeder Link braucht eine Beschriftung.');
      expect(message.id).not.toBe('');
      expect(labelField(2)).toHaveAttribute('aria-invalid', 'true');
      expect(labelField(2).getAttribute('aria-describedby')?.split(' ')).toContain(message.id);
      expect(labelField(2)).toHaveAccessibleDescription(/Jeder Link braucht eine Beschriftung\./);
      // The link that has its label stays unmarked.
      expect(labelField(1)).not.toHaveAttribute('aria-invalid', 'true');
      expect(labelField(1).getAttribute('aria-describedby') ?? '').not.toContain(message.id);
    });

    it('does not mark the field before a save was refused', () => {
      render(<StationTipsField value={noLabel} onChange={vi.fn()} />);

      expect(screen.getByText('Jeder Link braucht eine Beschriftung.')).toBeInTheDocument();
      expect(labelField(2)).not.toHaveAttribute('aria-invalid', 'true');
    });

    it('moves the focus to the empty label', () => {
      const ref = createRef<StationTipsFieldHandle>();
      render(<StationTipsField ref={ref} value={noLabel} onChange={vi.fn()} showErrors />);

      ref.current?.focusProblem();

      expect(labelField(2)).toHaveFocus();
    });

    it('keeps the mark when the language tab of the label changes', async () => {
      render(<StationTipsField value={noLabel} onChange={vi.fn()} showErrors />);
      const row = labelField(2).closest('[data-tips-field]') as HTMLElement;
      const tabs = row.querySelectorAll('[role="tab"]');
      // German and English are the languages of the default organisation.
      expect(tabs.length).toBeGreaterThan(1);

      await userEvent.click(tabs[1]);

      const input = row.querySelector('input') as HTMLInputElement;
      await waitFor(() => expect(input).toHaveAttribute('aria-invalid', 'true'));
    });

    it('takes the mark away once the label is filled', () => {
      const { rerender } = render(<StationTipsField value={noLabel} onChange={vi.fn()} showErrors />);
      const filled: StationTips = {
        ...noLabel,
        links: [noLabel.links[0], { label: { de: 'Plan' }, url: 'https://example.org' }],
      };

      rerender(<StationTipsField value={filled} onChange={vi.fn()} showErrors />);

      expect(labelField(2)).not.toHaveAttribute('aria-invalid', 'true');
      expect(labelField(2).getAttribute('aria-describedby') ?? '').not.toContain('station-tips-problem');
    });

    it('marks the address of a link that has none', () => {
      const noUrl: StationTips = { header: { de: 'Tipps' }, links: [{ label: { de: 'Karte' }, url: '' }] };
      const ref = createRef<StationTipsFieldHandle>();
      render(<StationTipsField ref={ref} value={noUrl} onChange={vi.fn()} showErrors />);

      const url = screen.getByRole('textbox', { name: 'URL' });
      expect(url).toHaveAttribute('aria-invalid', 'true');
      expect(url).toHaveAccessibleDescription(/Jeder Link braucht eine URL/);
      ref.current?.focusProblem();
      expect(url).toHaveFocus();
    });
  });
});
