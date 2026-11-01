import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Outlet, Route, Routes, useOutletContext } from 'react-router';
import TeamTab from './TeamTab';
import type { Team } from '../api/types';

const makeTeam = (over: Partial<Team> = {}): Team => ({
  id: 'team-1',
  organization_id: 'org-1',
  run_id: 'run-1',
  name: 'Die Fugger',
  color: null,
  member_count: 1,
  share_code: 'ABC123',
  coordinator_chat_open: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  ...over,
});

/** The team tab under a stand-in for the run shell, which hands down team and run. */
const renderTab = (
  subTab: 'leaderboard' | 'chat' | 'info',
  team: Team | null = makeTeam(),
  runState = 'active',
) =>
  render(
    <MemoryRouter initialEntries={[`/runs/run-1/participant/team/${subTab}`]}>
      <Routes>
        <Route
          path="/runs/:runId/participant"
          element={<Outlet context={{ team, run: { run_id: 'run-1', state: runState } }} />}
        >
          <Route path="team" element={<TeamTab />}>
            <Route path="leaderboard" element={<div>Rangliste-Inhalt</div>} />
            <Route path="chat" element={<div>Chat-Inhalt</div>} />
            <Route path="info" element={<div>Team-Inhalt</div>} />
          </Route>
        </Route>
      </Routes>
    </MemoryRouter>,
  );

/** A page of the tab that reports the membership answer it was handed. */
const MembershipProbe = () => {
  const { membership } = useOutletContext<{ membership?: string }>();
  return <div>{`membership:${membership ?? 'none-given'}`}</div>;
};

describe('TeamTab — hands the answer of the shell on', () => {
  it('forwards the membership answer of the shell to the page of the tab', () => {
    render(
      <MemoryRouter initialEntries={['/runs/run-1/participant/team/info']}>
        <Routes>
          <Route
            path="/runs/:runId/participant"
            element={<Outlet context={{ team: null, run: null, membership: 'unknown' }} />}
          >
            <Route path="team" element={<TeamTab />}>
              <Route path="info" element={<MembershipProbe />} />
            </Route>
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText('membership:unknown')).toBeInTheDocument();
  });
});

describe('TeamTab — the team code and the chat column', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  // A founder who has not shared the code yet sees the large card on the other
  // tabs. On the chat it would take the room the input needs.
  it('folds the team code to one line on the chat tab, also for a fresh team', () => {
    renderTab('chat');

    const line = screen.getByTestId('team-code-line');
    expect(line).toHaveTextContent('Team-Code');
    expect(line).toHaveTextContent('ABC123');
    expect(line).toHaveStyle({ flexWrap: 'nowrap' });
    // The card's introduction and its second button belong to the large card.
    expect(screen.queryByText('Dein Team-Code zum Teilen:')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Code teilen' })).not.toBeInTheDocument();
  });

  it('keeps the code one tap away on the chat tab: one button copies it', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    renderTab('chat');

    const copy = screen.getByRole('button', { name: 'Code kopieren' });
    expect(copy).toHaveStyle({ minHeight: '44px', minWidth: '44px' });
    await user.click(copy);

    expect(writeText).toHaveBeenCalledWith('ABC123');
    expect(await screen.findByText('Code kopiert!')).toBeInTheDocument();
  });

  it('keeps the large card for a fresh team on the tabs "Rangliste" and "Team"', () => {
    const { unmount } = renderTab('leaderboard');
    expect(screen.getByText('Dein Team-Code zum Teilen:')).toBeInTheDocument();
    expect(screen.queryByTestId('team-code-line')).not.toBeInTheDocument();
    unmount();

    renderTab('info');
    expect(screen.getByText('Dein Team-Code zum Teilen:')).toBeInTheDocument();
  });

  it('keeps the worded copy button on the one line of the other tabs', () => {
    renderTab('leaderboard', makeTeam({ member_count: 3 }));

    const line = screen.getByTestId('team-code-line');
    expect(line).toHaveStyle({ flexWrap: 'wrap' });
    expect(screen.getByRole('button', { name: 'Code kopieren' })).toHaveTextContent('Code kopieren');
  });

  it('shows no code after the end of the run, on the chat tab too', () => {
    renderTab('chat', makeTeam(), 'ended');

    expect(screen.queryByText('ABC123')).not.toBeInTheDocument();
  });

  // The tab is one link of the flex column from the header to the bottom
  // navigation: the panel takes what the code line and the tabs leave.
  it('is a flex column on the chat tab whose panel takes the rest and may shrink', () => {
    renderTab('chat');

    const panel = screen.getByRole('tabpanel');
    expect(panel).toHaveTextContent('Chat-Inhalt');
    expect(panel).toHaveStyle({ flexGrow: '1', minHeight: '0', display: 'flex', flexDirection: 'column' });
    expect(panel.parentElement).toHaveStyle({
      flexGrow: '1',
      minHeight: '0',
      display: 'flex',
      flexDirection: 'column',
    });
    // What stands above the panel keeps its own height.
    expect(screen.getByTestId('team-code-line')).toHaveStyle({ flexShrink: '0' });
    expect(screen.getByRole('tablist').closest('.MuiTabs-root')).toHaveStyle({ flexShrink: '0' });
  });

  it('keeps the plain block flow on the other tabs', () => {
    renderTab('leaderboard');

    const panel = screen.getByRole('tabpanel');
    expect(panel).not.toHaveStyle({ display: 'flex' });
    expect(panel.parentElement).not.toHaveStyle({ display: 'flex' });
  });
});
