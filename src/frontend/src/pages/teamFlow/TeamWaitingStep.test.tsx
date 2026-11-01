/**
 * The waiting step ends the wizard on the stations, with replace, once the
 * creator has named the team. It must not land on the bare participant route,
 * whose index redirect a first tap could be overtaken by.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router';

type Handlers = Record<string, (data: unknown) => void>;
const captured: { handlers: Handlers } = { handlers: {} };
const navigateMock = vi.fn();

vi.mock('react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router')>()),
  useNavigate: () => navigateMock,
}));
vi.mock('../../hooks/useWebSocket', () => ({
  useWebSocket: (opts: { handlers: Handlers }) => {
    captured.handlers = opts.handlers;
    return { status: 'open', lastError: null, send: vi.fn() };
  },
}));

import TeamWaitingStep from './TeamWaitingStep';
import { AuthContext } from '../../auth/AuthContext';
import { WizardStateProvider } from '../../wizard/WizardStateContext';

const authValue = {
  user: {
    id: 'u1',
    email: 'a@example.com',
    display_name: 'A',
    role: 'participant' as const,
    organization_id: 'o1',
    organization_slug: 'slug',
  },
  isAuthenticated: true,
  isLoading: false,
  login: vi.fn(),
  logout: vi.fn(),
  checkAuth: vi.fn(),
  needsTwoFactorSetup: vi.fn().mockReturnValue(false),
};

function renderStep() {
  return render(
    <AuthContext.Provider value={authValue}>
      <WizardStateProvider>
        <MemoryRouter initialEntries={['/join/ABC123/team/waiting']}>
          <Routes>
            <Route path="/join/:code/team/waiting" element={<TeamWaitingStep />} />
          </Routes>
        </MemoryRouter>
      </WizardStateProvider>
    </AuthContext.Provider>,
  );
}

describe('<TeamWaitingStep> wizard ending', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    sessionStorage.clear();
    sessionStorage.setItem(
      'clavis_wizard_team_state',
      JSON.stringify({ teamId: 'team-1', runId: 'run-1' }),
    );
  });

  it('ends on the stations with replace when the team gets its name', () => {
    renderStep();

    captured.handlers['team.name_updated']({ team_id: 'team-1', name: 'Rockets' });

    expect(navigateMock).toHaveBeenCalledWith('/runs/run-1/participant/stations', {
      replace: true,
    });
  });
});
