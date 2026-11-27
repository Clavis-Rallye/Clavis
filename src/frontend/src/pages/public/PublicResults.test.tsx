import { afterEach, describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { axe } from 'vitest-axe';
import PublicResults from './PublicResults';
import { lookupPublicResults } from '../../services/publicResults';
import i18n from '../../i18n';
import {
  DEFAULT_INSTANCE_CONFIG,
  __resetInstanceConfigForTests,
} from '../../config/instanceConfig';
import type { PublicResults as PublicResultsData } from '../../services/publicResults';
import {
  __resetOperatorNoticeForTests,
  type OperatorNotice as OperatorNoticeData,
} from '../../config/operatorNotice';

// The page renders from the lookup alone: mocking at the service
// boundary keeps this file about the page, the service mapping has its own.
vi.mock('../../services/publicResults', () => ({
  lookupPublicResults: vi.fn(),
}));

// The Endstand chip's responsive sx is asserted via its props: jsdom does not
// evaluate emotion's breakpoint media queries, so the applied style is not
// observable there (same props-exposing approach as the analytics chart
// mocks). Mocking the barrel — a subpath mock does not intercept the page's
// root '@mui/material' import.
vi.mock('@mui/material', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@mui/material')>();
  return {
    ...actual,
    // Only `sx` is serialised: the chip carries an icon element, and a
    // React element does not survive JSON.stringify.
    Chip: (props: { sx?: unknown; icon?: React.ReactNode; label?: React.ReactNode }) => (
      <div data-testid="final-chip" data-props={JSON.stringify({ sx: props.sx })}>
        {props.icon}
        {String(props.label)}
      </div>
    ),
  };
});

const mockLookup = lookupPublicResults as unknown as ReturnType<typeof vi.fn>;

const snapshot: PublicResultsData = {
  run_name: 'Herbst-Rallye',
  ended_at: '2026-01-17T16:00:00Z',
  language: 'de',
  entries: [
    { team_name: 'Team Alpha', points: 120, rank: 1, is_winner: true },
    { team_name: 'Team Beta', points: 120, rank: 1, is_winner: true },
    { team_name: 'Team Gamma', points: 90, rank: 3, is_winner: false },
  ],
};

const renderPage = () =>
  render(
    <MemoryRouter>
      <PublicResults />
    </MemoryRouter>,
  );

// The operator notice stands directly above the legal links, outside main
// (spec 095, C9).
const operatorNotice: OperatorNoticeData = {
  version: 1,
  logos: [{ src: 'programme.svg', alt: { de: 'Logo des Programms' }, height: 48 }],
  text: { de: 'Dieses Angebot wird gefoerdert.' },
  links: [{ label: 'example.org/programme', url: 'https://example.org/programme' }],
};

function expectNoticeAboveLegalLinks() {
  const region = screen.getByRole('region', { name: 'Hinweis des Betreibers' });
  const nav = screen.getByRole('navigation', { name: 'Rechtliches' });
  // Same parent, the notice right before the row of links.
  expect(region.nextElementSibling).toBe(nav);
  expect(nav.compareDocumentPosition(region) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  expect(screen.getByRole('main')).not.toContainElement(region);
  return region;
}

describe('PublicResults', () => {
  beforeEach(() => {
    mockLookup.mockReset();
    __resetInstanceConfigForTests();
  });

  // The logo row (with the language switch) is a header landmark, not part of
  // the main card, so no text stands outside a landmark. The page keeps one
  // main.
  it('wraps the logo row in a header landmark, with one main on the page', async () => {
    window.history.pushState({}, '', '/results#landmark-slug');
    mockLookup.mockResolvedValueOnce({ kind: 'ok', data: snapshot });
    renderPage();

    const header = await screen.findByRole('banner');
    expect(header).toHaveTextContent('Clavis');
    expect(screen.getAllByRole('main')).toHaveLength(1);
  });

  // The wordmark shows the instance brand, like LegalShell.
  it('shows the configured brand in the wordmark', async () => {
    window.history.pushState({}, '', '/results#brand-slug');
    __resetInstanceConfigForTests({
      ...DEFAULT_INSTANCE_CONFIG,
      brand_name: 'Stadtrallye Musterstadt',
    });
    mockLookup.mockResolvedValueOnce({ kind: 'ok', data: snapshot });
    renderPage();

    expect(await screen.findByText('Stadtrallye Musterstadt')).toBeInTheDocument();
  });

  it('valid link: run name, end date, Endstand, winners and the ranked list', async () => {
    window.history.pushState({}, '', '/results#valid-slug');
    mockLookup.mockResolvedValueOnce({ kind: 'ok', data: snapshot });
    const { container } = renderPage();

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Herbst-Rallye' })).toBeInTheDocument(),
    );
    expect(mockLookup).toHaveBeenCalledWith('valid-slug', expect.any(AbortSignal));
    expect(screen.getByText('Endstand')).toBeInTheDocument();
    expect(screen.getByText(/Beendet am/)).toBeInTheDocument();
    // The page language decides the date, never the browser's
    // en-US order ("11/18/2026").
    expect(screen.getByText(/Beendet am/).textContent).not.toMatch(/\d{1,2}\/\d{1,2}\/\d{4}/);
    expect(screen.getByText(/Beendet am/).textContent).toMatch(/\d{4}/);

    // The header stack is a column on phones: the chip keeps its own width
    // there and does not stretch across the card.
    const chipProps = JSON.parse(screen.getByTestId('final-chip').getAttribute('data-props')!);
    expect(chipProps.sx).toEqual({ alignSelf: { xs: 'flex-start' } });
    // The final standing carries the tick, hidden from
    // assistive technology because the label says "Endstand".
    const finalSign = screen
      .getByTestId('final-chip')
      .querySelector('[data-testid="CheckCircleIcon"]');
    expect(finalSign).toHaveAttribute('aria-hidden', 'true');

    // Two teams share rank 1 — both are winners with the shared-rank list below.
    expect(screen.getByText('Gewinner')).toBeInTheDocument();
    expect(screen.getAllByText('Team Alpha').length).toBe(2);
    expect(screen.getAllByText('Team Beta').length).toBe(2);
    expect(screen.getAllByText('1.').length).toBe(2);
    expect(screen.getByText('3.')).toBeInTheDocument();
    expect(screen.getByText('Team Gamma')).toBeInTheDocument();
    expect(screen.getAllByText('120 Pkt.').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('90 Pkt.')).toBeInTheDocument();

    // The language switch is the visitor's only other control — no app
    // navigation, no participant or organization data.
    expect(screen.getByRole('button', { name: 'DE' })).toBeInTheDocument();
    expect(screen.queryByText('Stadt Augsburg')).not.toBeInTheDocument();

    // The heading sits in main,
    // so the route focus lands on it, and the legal pages are one tap away.
    expect(screen.getByRole('main')).toContainElement(
      screen.getByRole('heading', { name: 'Herbst-Rallye' }),
    );
    expect(screen.getByRole('navigation', { name: 'Rechtliches' })).toBeInTheDocument();

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('opens in the snapshot language: an English run shows the English labels', async () => {
    window.history.pushState({}, '', '/results#english-slug');
    mockLookup.mockResolvedValueOnce({
      kind: 'ok',
      data: { ...snapshot, language: 'en' },
    });
    renderPage();

    await waitFor(() => expect(i18n.resolvedLanguage).toBe('en'));
    await waitFor(() => expect(screen.getByText('Final standing')).toBeInTheDocument());
    expect(screen.getByText(/Ended on/)).toBeInTheDocument();
    expect(screen.getByText('Winners')).toBeInTheDocument();
  });

  it('expired link: the neutral message', async () => {
    window.history.pushState({}, '', '/results#expired-slug');
    mockLookup.mockResolvedValueOnce({ kind: 'expired' });
    renderPage();

    await waitFor(() =>
      expect(
        screen.getByText('Dieser Ergebnis-Link ist nicht mehr gültig.'),
      ).toBeInTheDocument(),
    );
  });

  it('revoked link: the same neutral message', async () => {
    window.history.pushState({}, '', '/results#revoked-slug');
    mockLookup.mockResolvedValueOnce({ kind: 'revoked' });
    renderPage();

    await waitFor(() =>
      expect(
        screen.getByText('Dieser Ergebnis-Link ist nicht mehr gültig.'),
      ).toBeInTheDocument(),
    );
  });

  it('unknown slug: the not-found message', async () => {
    window.history.pushState({}, '', '/results#unknown-slug');
    mockLookup.mockResolvedValueOnce({ kind: 'not_found' });
    renderPage();

    await waitFor(() =>
      expect(
        screen.getByText('Dieser Ergebnis-Link ist nicht gültig oder existiert nicht.'),
      ).toBeInTheDocument(),
    );
  });

  it('no slug in the fragment: the not-found message without a lookup', async () => {
    window.history.pushState({}, '', '/results');
    renderPage();

    await waitFor(() =>
      expect(
        screen.getByText('Dieser Ergebnis-Link ist nicht gültig oder existiert nicht.'),
      ).toBeInTheDocument(),
    );
    expect(mockLookup).not.toHaveBeenCalled();
  });

  it('transient failure: load error with a retry that asks again', async () => {
    window.history.pushState({}, '', '/results#flaky-slug');
    mockLookup.mockResolvedValueOnce({ kind: 'error' });
    renderPage();

    const retry = await screen.findByRole('button', { name: 'Erneut versuchen' });
    expect(screen.getByText('Das Ergebnis konnte nicht geladen werden.')).toBeInTheDocument();

    mockLookup.mockResolvedValueOnce({ kind: 'ok', data: snapshot });
    await userEvent.click(retry);
    await screen.findByRole('heading', { name: 'Herbst-Rallye' });
    expect(retry).not.toBeInTheDocument();
  });

  it('renders an unnamed team as Team <n> from i18n', async () => {
    window.history.pushState({}, '', '/results#unnamed-slug');
    mockLookup.mockResolvedValueOnce({
      kind: 'ok',
      data: {
        ...snapshot,
        entries: [
          { team_name: 'Team Alpha', points: 120, rank: 1, is_winner: true },
          { team_name: null, points: 90, rank: 2, is_winner: false },
        ],
      },
    });
    renderPage();

    await waitFor(() => expect(screen.getByText('Team 2')).toBeInTheDocument());
  });
});

describe('PublicResults operator notice', () => {
  beforeEach(() => {
    mockLookup.mockReset();
    __resetInstanceConfigForTests();
  });

  afterEach(() => {
    __resetOperatorNoticeForTests(null);
  });

  it('shows the notice above the legal links, outside main', async () => {
    window.history.pushState({}, '', '/results#notice-slug');
    mockLookup.mockResolvedValueOnce({ kind: 'ok', data: snapshot });
    __resetOperatorNoticeForTests(operatorNotice);
    renderPage();

    await screen.findByRole('heading', { name: 'Herbst-Rallye' });
    expectNoticeAboveLegalLinks();
  });

  it('shows no notice region without a notice', async () => {
    window.history.pushState({}, '', '/results#notice-slug');
    mockLookup.mockResolvedValueOnce({ kind: 'ok', data: snapshot });
    renderPage();

    await screen.findByRole('heading', { name: 'Herbst-Rallye' });
    expect(screen.queryByRole('region', { name: 'Hinweis des Betreibers' })).toBeNull();
  });
});
