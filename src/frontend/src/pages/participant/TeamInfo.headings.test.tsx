import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Outlet, Route, Routes } from 'react-router';
import TeamInfo from './TeamInfo';
import { AuthContext } from '../../auth/AuthContext';
import type { Team } from '../../api/types';

const authUser = {
  id: 'u1',
  email: 'participant@example.com',
  display_name: 'Teilnehmer',
  role: 'participant' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const baseTeam: Team = {
  id: 'team-1',
  organization_id: 'org-1',
  run_id: 'run-1',
  name: 'Die Fugger',
  color: null,
  member_count: 2,
  share_code: 'ABC123',
  coordinator_chat_open: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

// TeamInfo renders inside TeamTab's Outlet, which hands the team down live, in
// the same context shape the leaderboard test uses.
const renderInfo = (team: Team) =>
  render(
    <MemoryRouter initialEntries={['/runs/run-1/participant/team/info']}>
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
          <Route
            path="/runs/:runId/participant"
            element={<Outlet context={{ team, run: null }} />}
          >
            <Route path="team/info" element={<TeamInfo />} />
          </Route>
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('TeamInfo inside the team tab', () => {
  // The team name is the page's one h1 and keeps its h5 look. The members title
  // below stays an h2, so no level jumps.
  it('has exactly one h1, the team name in its h5 look', () => {
    const team = {
      ...baseTeam,
      members: [{ id: 'm1', display_name: 'Anna', is_guest: false }],
    };
    renderInfo(team as Team);

    const h1 = screen.getByRole('heading', { level: 1, name: 'Die Fugger' });
    expect(h1).toHaveClass('MuiTypography-h5');
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    expect(screen.getByRole('heading', { level: 2, name: 'Mitglieder' })).toBeInTheDocument();
  });

  it('lists the members with their display names', () => {
    const team = {
      ...baseTeam,
      members: [
        { id: 'm1', display_name: 'Anna', is_guest: false },
        { id: 'm2', display_name: 'Gast-7edf', is_guest: true },
      ],
    };
    renderInfo(team as Team);

    expect(screen.getByText('Anna')).toBeInTheDocument();
    expect(screen.getByText('Gast-7edf')).toBeInTheDocument();
  });
});
