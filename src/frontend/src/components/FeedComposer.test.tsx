import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'vitest-axe';
import FeedComposer from './FeedComposer';
import '../i18n';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const KINDS = [
  {
    word: 'Mitteilung',
    hint: 'Allgemeine Mitteilung an alle Teams oder ein einzelnes Team.',
  },
  {
    word: 'Countdown',
    hint: 'Zeigt den Teams eine mitlaufende Restzeit bis zu einem Zeitpunkt.',
  },
  {
    word: 'Alarm',
    hint: 'Dringende Meldung, wird bei den Teams rot hervorgehoben.',
  },
];

describe('FeedComposer', () => {
  // The tooltip's help sentence must not be the accessible name of each kind
  // button, or the visible word is never read.
  it.each(KINDS)(
    'names the kind button "$word" by its visible word and gives the help sentence as its description',
    ({ word, hint }) => {
      render(<FeedComposer orgSlug="test-org" runId="run-1" />);

      const button = screen.getByRole('button', { name: word });
      expect(button).toHaveAccessibleName(word);
      expect(button).toHaveAccessibleDescription(hint);
      expect(button).not.toHaveAttribute('aria-label');
      expect(button).not.toHaveAttribute('title');
    },
  );

  it('keeps name and description while the tooltip is open, and the tooltip stays for the eye', async () => {
    render(<FeedComposer orgSlug="test-org" runId="run-1" />);

    const button = screen.getByRole('button', { name: 'Countdown' });
    await userEvent.hover(button);

    const tooltip = await screen.findByRole('tooltip');
    expect(tooltip).toHaveTextContent(KINDS[1].hint);
    expect(button).toHaveAccessibleName('Countdown');
    expect(button).toHaveAccessibleDescription(KINDS[1].hint);
  });

  it('has no label-content-name-mismatch on the kind buttons', async () => {
    const { container } = render(<FeedComposer orgSlug="test-org" runId="run-1" />);

    const results = await axe(container, {
      rules: { 'label-content-name-mismatch': { enabled: true } },
    });
    expect(results).toHaveNoViolations();
  });
});
