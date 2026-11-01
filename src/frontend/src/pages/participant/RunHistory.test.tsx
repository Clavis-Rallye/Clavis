/**
 * The run history of a participant. A place shows only when the API sends one,
 * which it does for a run that has ended and is fully graded. Until then the
 * entry shows the run's state.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router';

vi.mock('../../api/me', () => ({ getMyRunHistory: vi.fn() }));

import RunHistory from './RunHistory';
import { getMyRunHistory } from '../../api/me';

const entry = (overrides: Record<string, unknown>) => ({
  run_id: 'run-1',
  run_name: 'Herbst-Rallye',
  rallye_title: {},
  team_id: 'team-1',
  team_name: 'TEST Konto',
  started_at: '2026-11-18T09:00:00Z',
  ended_at: '2026-11-18T11:00:00Z',
  state: 'ended',
  placement: null,
  total_teams: null,
  total_points: 0,
  ...overrides,
});

const renderHistory = (items: unknown[]) => {
  vi.mocked(getMyRunHistory).mockResolvedValue({ items } as never);
  render(
    <MemoryRouter initialEntries={['/run-history']}>
      <Routes>
        <Route path="/run-history" element={<RunHistory />} />
        <Route path="/no-runs" element={<div>join code page</div>} />
      </Routes>
    </MemoryRouter>,
  );
};

describe('RunHistory', () => {
  beforeEach(() => vi.mocked(getMyRunHistory).mockReset());

  // The page wraps its content in the one main with the id the shells' skip links
  // target, and carries the same skip link as RunShell.
  it('wraps its content in one main landmark with the skip link of the shells', async () => {
    renderHistory([entry({})]);

    const main = await screen.findByRole('main');
    expect(main).toHaveAttribute('id', 'main-content');
    expect(screen.getAllByRole('main')).toHaveLength(1);
    const skip = screen.getByRole('link', { name: 'Zum Hauptinhalt springen' });
    expect(skip).toHaveAttribute('href', '#main-content');
    expect(skip).toHaveClass('skip-link');
  });

  // The brand name is no heading: the first heading is the h1.
  it('has the h1 as its first heading, the brand name is no heading', async () => {
    renderHistory([entry({})]);

    await screen.findByRole('main');
    const headings = screen.getAllByRole('heading');
    expect(headings[0].tagName).toBe('H1');
    expect(headings[0]).toHaveTextContent('Verlauf');
  });

  it('shows the place of an ended, graded run', async () => {
    renderHistory([entry({ placement: 2, total_teams: 3, total_points: 5 })]);

    expect(await screen.findByText('Platz 2 von 3')).toBeInTheDocument();
    expect(screen.queryByText('?')).toBeNull();
  });

  it('shows the German state, and no place, for a run without a final place', async () => {
    renderHistory([entry({ run_id: 'run-2', state: 'active', ended_at: null })]);

    expect(await screen.findByText('Läuft')).toBeInTheDocument();
    expect(screen.queryByText(/Platz/)).toBeNull();
    expect(screen.queryByText('active')).toBeNull();
    expect(screen.queryByText('?')).toBeNull();
  });

  it('gives no trophy to a first place without points', async () => {
    renderHistory([entry({ placement: 1, total_teams: 2, total_points: 0 })]);

    expect(await screen.findByText('Platz 1 von 2')).toBeInTheDocument();
    expect(screen.queryByTestId('EmojiEventsIcon')).toBeNull();
  });

  it('leads to the join code field', async () => {
    renderHistory([entry({ placement: 2, total_teams: 3 })]);

    fireEvent.click(await screen.findByRole('button', { name: 'Mit Code einem Lauf beitreten' }));
    expect(await screen.findByText('join code page')).toBeInTheDocument();
  });

  // The state of a run without a place reads in words, not as the raw state word.
  it('names the state of a run without a place in words', async () => {
    renderHistory([
      entry({ run_id: 'r1', run_name: 'TEST Lauf', rallye_title: { de: 'Domviertel' }, state: 'active', ended_at: null }),
    ]);

    expect(await screen.findByText('Läuft')).toBeInTheDocument();
    expect(screen.queryByText('active')).not.toBeInTheDocument();
  });
});
