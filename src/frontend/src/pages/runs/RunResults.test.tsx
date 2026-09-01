import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import RunResults from './RunResults';
import { AuthContext } from '../../auth/AuthContext';
import apiClient from '../../api/client';
import type { Scoreboard } from '../../api/types';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const authUser = {
  id: 'u1',
  email: 'coord@example.com',
  display_name: 'Coordinator',
  role: 'coordinator' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const endedRun = {
  id: 'run-1',
  organization_id: 'org-1',
  rallye_id: 'r1',
  name: 'Herbst-Rallye',
  state: 'ended' as const,
  join_code: 'JOIN42',
  scheduled_for: null,
  started_at: '2026-01-17T13:00:00Z',
  paused_at: null,
  ended_at: '2026-01-17T16:00:00Z',
  settings: {},
  max_teams: null,
  content_languages: ['de', 'en'],
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const scoreboardFixture = (overrides: Partial<Scoreboard> = {}): Scoreboard => ({
  run_id: 'run-1',
  is_final: true,
  scored_count: 6,
  submission_count: 6,
  entries: [
    {
      team_id: 't1',
      team_name: 'Team Alpha',
      total_points: 120,
      total_max_points: 150,
      scored_count: 2,
      last_scored_at: null,
      rank: 1,
      is_winner: true,
    },
    {
      team_id: 't2',
      team_name: 'Team Beta',
      total_points: 120,
      total_max_points: 150,
      scored_count: 2,
      last_scored_at: null,
      rank: 1,
      is_winner: true,
    },
    {
      team_id: 't3',
      team_name: 'Team Gamma',
      total_points: 90,
      total_max_points: 150,
      scored_count: 2,
      last_scored_at: null,
      rank: 3,
      is_winner: false,
    },
  ],
  ...overrides,
});

const renderResults = () =>
  render(
    <MemoryRouter initialEntries={['/runs/run-1/results']}>
      <AuthContext.Provider
        value={{
          user: authUser,
          isAuthenticated: true,
          isLoading: false,
          login: vi.fn(),
          logout: vi.fn(),
          checkAuth: vi.fn(),
          needsTwoFactorSetup: () => false,
        }}
      >
        <Routes>
          <Route path="/runs/:runId/results" element={<RunResults />} />
          <Route path="/runs/:runId" element={<div>Dashboard-Stub</div>} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('RunResults', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('final standing: run name, end date, both winners and the ranked list', async () => {
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.endsWith('/runs/run-1')) return Promise.resolve({ data: endedRun });
      if (url.includes('/scores/scoreboard')) {
        return Promise.resolve({ data: scoreboardFixture() });
      }
      return Promise.resolve({ data: {} });
    });
    const { container } = renderResults();

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Herbst-Rallye' })).toBeInTheDocument(),
    );
    // The final standing is green with the tick.
    const finalChip = screen.getByText('Endstand').closest('.MuiChip-root');
    expect(finalChip).toHaveClass('MuiChip-colorSuccess');
    expect(finalChip?.querySelector('[data-testid="CheckCircleIcon"]')).toHaveAttribute(
      'aria-hidden',
      'true',
    );
    expect(screen.getByText(/Beendet am/)).toBeInTheDocument();

    // Two teams share rank 1 — both are winners with the shared-rank list below.
    expect(screen.getByText('Gewinner')).toBeInTheDocument();
    // Winner names appear twice: once in the winner block, once in the list.
    expect(screen.getAllByText('Team Alpha').length).toBe(2);
    expect(screen.getAllByText('Team Beta').length).toBe(2);
    expect(screen.getAllByText('1.').length).toBe(2);
    expect(screen.getByText('3.')).toBeInTheDocument();
    expect(screen.getByText('Team Gamma')).toBeInTheDocument();
    expect(screen.getAllByText('120 Pkt.').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('90 Pkt.')).toBeInTheDocument();

    expect(screen.getByRole('button', { name: 'Vollbild' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Drucken' })).toBeInTheDocument();
    expect(screen.getByText('Zurück zum Lauf')).toBeInTheDocument();

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('interim standing: interim label and no winner block', async () => {
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.endsWith('/runs/run-1')) return Promise.resolve({ data: endedRun });
      if (url.includes('/scores/scoreboard')) {
        return Promise.resolve({
          data: scoreboardFixture({
            is_final: false,
            entries: scoreboardFixture().entries.map((e) => ({ ...e, is_winner: false })),
          }),
        });
      }
      return Promise.resolve({ data: {} });
    });
    renderResults();

    await waitFor(() => expect(screen.getByText('Zwischenstand')).toBeInTheDocument());
    // The interim standing is the blue chip with the
    // hourglass, not the orange of a warning.
    const interimChip = screen.getByText('Zwischenstand').closest('.MuiChip-root');
    expect(interimChip).not.toHaveClass('MuiChip-colorWarning');
    expect(interimChip?.querySelector('[data-testid="HourglassEmptyIcon"]')).toHaveAttribute(
      'aria-hidden',
      'true',
    );
    expect(screen.queryByText('Gewinner')).not.toBeInTheDocument();
  });

  it('Vollbild toggle hides and restores the navigation frame via a body class', async () => {
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.endsWith('/runs/run-1')) return Promise.resolve({ data: endedRun });
      if (url.includes('/scores/scoreboard')) {
        return Promise.resolve({ data: scoreboardFixture() });
      }
      return Promise.resolve({ data: {} });
    });
    renderResults();

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Herbst-Rallye' })).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByRole('button', { name: 'Vollbild' }));
    // The body class drives the chrome-hiding CSS (same mechanism as
    // print-hidden) and is what remains active where the Fullscreen API
    // is unavailable — jsdom here.
    expect(document.body.classList.contains('clavis-results-vollbild')).toBe(true);
    expect(screen.getByRole('button', { name: 'Vollbild beenden' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Vollbild beenden' }));
    expect(document.body.classList.contains('clavis-results-vollbild')).toBe(false);
    expect(screen.getByText('Zurück zum Lauf')).toBeInTheDocument();
  });
});
