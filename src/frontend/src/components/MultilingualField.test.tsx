import { describe, it, expect, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'vitest-axe';
import MultilingualField from './MultilingualField';
import '../i18n';

describe('MultilingualField', () => {
  it('renders label and starts on DE tab by default', () => {
    render(
      <MultilingualField value={{ de: 'Hallo', en: 'Hello' }} onChange={vi.fn()} label="Titel" />,
    );
    expect(screen.getByText('Titel')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Hallo')).toBeVisible();
  });

  it('switches to EN tab when clicked and shows EN value', async () => {
    render(
      <MultilingualField value={{ de: 'Hallo', en: 'Hello' }} onChange={vi.fn()} label="Titel" />,
    );
    await userEvent.click(screen.getByRole('tab', { name: 'Englisch' }));
    expect(screen.getByDisplayValue('Hello')).toBeVisible();
  });

  it('calls onChange with merged object when DE field edited', async () => {
    const onChange = vi.fn();
    render(
      <MultilingualField value={{ de: 'Alt', en: 'Old' }} onChange={onChange} label="Titel" />,
    );
    const input = screen.getByDisplayValue('Alt');
    await userEvent.type(input, 'X');
    // userEvent.type fires one change per char; assert last call merges correctly
    const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    expect(lastCall).toEqual({ de: 'AltX', en: 'Old' });
  });

  it('does not mark an empty required DE field as error on first render', () => {
    render(
      <MultilingualField value={{ de: '', en: 'Hello' }} onChange={vi.fn()} label="Titel" required />,
    );
    const input = screen.getByDisplayValue('');
    expect(input).toHaveAttribute('required');
    expect(input).toHaveAttribute('aria-invalid', 'false');
  });

  it('marks the DE field as error once it has been touched (blurred)', async () => {
    render(
      <MultilingualField value={{ de: '', en: 'Hello' }} onChange={vi.fn()} label="Titel" required />,
    );
    const input = screen.getByDisplayValue('');
    await userEvent.click(input);
    await userEvent.tab();
    expect(input).toHaveAttribute('aria-invalid', 'true');
  });

  it('marks the DE field as error immediately when showErrors is set', () => {
    render(
      <MultilingualField
        value={{ de: '', en: 'Hello' }}
        onChange={vi.fn()}
        label="Titel"
        required
        showErrors
      />,
    );
    expect(screen.getByDisplayValue('')).toHaveAttribute('aria-invalid', 'true');
  });

  it('never marks an optional field as error, even empty on the DE tab', () => {
    render(<MultilingualField value={{ de: '', en: '' }} onChange={vi.fn()} label="Titel" />);
    expect(screen.getByDisplayValue('')).toHaveAttribute('aria-invalid', 'false');
  });

  it('shows a tooltip on the missing-translation mark once revealed', async () => {
    render(
      <MultilingualField
        value={{ de: '', en: 'Hello' }}
        onChange={vi.fn()}
        label="Titel"
        required
        showErrors
      />,
    );
    await userEvent.hover(screen.getByText('Deutsch'));
    expect(await screen.findByText('Fehlt')).toBeInTheDocument();
  });

  it('renders the rich-text editor per locale when richText is set', async () => {
    const onChange = vi.fn();
    render(
      <MultilingualField
        value={{ de: '<p>Hallo</p>', en: '' }}
        onChange={onChange}
        label="Beschreibung"
        richText
        required
      />,
    );
    const editor = await screen.findByRole('textbox', { name: 'Beschreibung (Deutsch)' });
    expect(editor).toHaveAttribute('contenteditable', 'true');
    expect(editor.textContent).toBe('Hallo');
    expect(screen.getByRole('button', { name: 'Fett' })).toBeInTheDocument();
    expect(screen.queryByText('Pflichtfeld: bitte auf Deutsch ausfüllen.')).not.toBeInTheDocument();
  });

  it('flags an empty required rich text on the German tab once touched', async () => {
    render(
      <MultilingualField value={{ de: '<p></p>', en: '<p>Hello</p>' }} onChange={vi.fn()} label="Beschreibung" richText required />,
    );
    const editor = await screen.findByRole('textbox', { name: 'Beschreibung (Deutsch)' });
    expect(screen.queryByText('Pflichtfeld: bitte auf Deutsch ausfüllen.')).not.toBeInTheDocument();
    // A real click into TipTap needs layout APIs jsdom lacks (elementFromPoint) —
    // focus/blur directly, the same contract userEvent's click+tab would drive.
    act(() => editor.focus());
    act(() => editor.blur());
    expect(screen.getByText('Pflichtfeld: bitte auf Deutsch ausfüllen.')).toBeInTheDocument();
  });

  it('flags an empty required rich text immediately when showErrors is set', async () => {
    render(
      <MultilingualField
        value={{ de: '<p></p>', en: '<p>Hello</p>' }}
        onChange={vi.fn()}
        label="Beschreibung"
        richText
        required
        showErrors
      />,
    );
    await screen.findByRole('textbox', { name: 'Beschreibung (Deutsch)' });
    expect(screen.getByText('Pflichtfeld: bitte auf Deutsch ausfüllen.')).toBeInTheDocument();
  });

  it('renders as textarea when multiline prop is true', () => {
    render(
      <MultilingualField
        value={{ de: 'Zeile 1', en: '' }}
        onChange={vi.fn()}
        label="Beschreibung"
        multiline
      />,
    );
    const input = screen.getByDisplayValue('Zeile 1');
    expect(input.tagName).toBe('TEXTAREA');
  });

  it('shows optional hint on EN field by default', async () => {
    render(
      <MultilingualField value={{ de: 'a', en: 'b' }} onChange={vi.fn()} label="L" />,
    );
    await userEvent.click(screen.getByRole('tab', { name: 'Englisch' }));
    expect(screen.getByText('Optional')).toBeInTheDocument();
  });

  it('uses custom helperText when provided (overriding optional hint)', async () => {
    render(
      <MultilingualField
        value={{ de: 'a', en: 'b' }}
        onChange={vi.fn()}
        label="L"
        helperText="Max 100 characters"
      />,
    );
    await userEvent.click(screen.getByRole('tab', { name: 'Englisch' }));
    expect(screen.getByText('Max 100 characters')).toBeInTheDocument();
    expect(screen.queryByText('Optional')).not.toBeInTheDocument();
  });

  it('has no axe accessibility violations on initial render', async () => {
    const { container } = render(
      <MultilingualField value={{ de: 'Hallo', en: 'Hello' }} onChange={vi.fn()} label="Titel" />,
    );
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
