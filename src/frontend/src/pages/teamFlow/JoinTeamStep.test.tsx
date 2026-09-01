/**
 * Join-by-code failures map `ApiError.errorCode` first.
 *
 * guest.py's join-by-code path (`mode: 'join'`) answers a full team with
 * `error_code: TEAM_FULL` next to its German-only detail. Matching the detail's
 * prose alone would let an English rephrasing of the same refusal fall through
 * to the generic "try again". This covers the errorCode-first path with a detail
 * that does NOT match the prose check, so only the code can make it pass.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';

vi.mock('../../api/guest', () => ({ joinAsGuest: vi.fn() }));
vi.mock('../../components/ClavisCircularProgress', () => ({
  default: () => <div data-testid="loading-indicator" />,
}));
// The camera never starts in jsdom. The stub fails the way a browser does,
// with the error the test sets.
const scannerFailure = { error: new Error('NotAllowedError: Permission denied') };
// Set to a text to make the stub decode it instead of failing.
const scannerResult: { text: string | null } = { text: null };
vi.mock('../../components/qr/QrScanner', async () => {
  const { useEffect } = await import('react');
  const QrScanner = ({
    onError,
    onDecode,
  }: {
    onError?: (e: Error) => void;
    onDecode?: (text: string) => void;
  }) => {
    useEffect(() => {
      if (scannerResult.text !== null) onDecode?.(scannerResult.text);
      else onError?.(scannerFailure.error);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return null;
  };
  return { default: QrScanner, QrScanner };
});

import JoinTeamStep from './JoinTeamStep';
import { AuthContext } from '../../auth/AuthContext';
import { WizardStateProvider } from '../../wizard/WizardStateContext';
import { ApiError } from '../../api/client';
import { joinAsGuest } from '../../api/guest';

const mockJoinAsGuest = vi.mocked(joinAsGuest);

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
        <MemoryRouter initialEntries={['/join/ABC123/team/join']}>
          <Routes>
            <Route path="/join/:code/team/join" element={<JoinTeamStep />} />
          </Routes>
        </MemoryRouter>
      </WizardStateProvider>
    </AuthContext.Provider>,
  );
}

describe('<JoinTeamStep> coded error mapping', () => {
  beforeEach(() => {
    mockJoinAsGuest.mockReset();
    sessionStorage.clear();
  });

  it('shows the team-limit ("full") text for a coded answer whose detail does not match the old prose', async () => {
    // Neither "voll" nor "full" appears here: only error_code carries the reason.
    // A prose-only check would fall through to the generic "Beitritt fehlgeschlagen"
    // message.
    mockJoinAsGuest.mockRejectedValue(
      new ApiError(422, 'No more capacity in this team.', 'TEAM_FULL'),
    );

    renderStep();

    const input = await screen.findByLabelText('Team-Code');
    await userEvent.type(input, 'SHARE01');
    await userEvent.click(screen.getByRole('button', { name: 'Team beitreten' }));

    expect(await screen.findByText('Dieses Team ist voll.')).toBeInTheDocument();
    expect(screen.queryByText('Beitritt fehlgeschlagen. Bitte versuche es erneut.')).not.toBeInTheDocument();
  });

  it('maps ALREADY_IN_OTHER_TEAM by code even when the detail is unrecognised', async () => {
    mockJoinAsGuest.mockRejectedValue(
      new ApiError(422, 'Some future rephrasing.', 'ALREADY_IN_OTHER_TEAM'),
    );

    renderStep();

    const input = await screen.findByLabelText('Team-Code');
    await userEvent.type(input, 'SHARE01');
    await userEvent.click(screen.getByRole('button', { name: 'Team beitreten' }));

    expect(
      await screen.findByText(
        'Du bist in diesem Lauf schon in einem Team. Für einen Wechsel wende dich bitte an die Spielleitung. Verlasse dein Team nicht selbst: Als Gast kommst du sonst nicht mehr in den Lauf zurück.',
      ),
    ).toBeInTheDocument();
  });

  it('shows the paused text for a RUN_PAUSED coded answer', async () => {
    mockJoinAsGuest.mockRejectedValue(new ApiError(422, 'Run is currently paused.', 'RUN_PAUSED'));

    renderStep();

    const input = await screen.findByLabelText('Team-Code');
    await userEvent.type(input, 'SHARE01');
    await userEvent.click(screen.getByRole('button', { name: 'Team beitreten' }));

    expect(
      await screen.findByText(
        'Der Lauf ist pausiert. Bitte die Spielleitung, ihn fortzusetzen, und versuche es dann erneut.',
      ),
    ).toBeInTheDocument();
  });

  it('shows the ended text for a RUN_ENDED coded answer', async () => {
    mockJoinAsGuest.mockRejectedValue(new ApiError(422, 'Run has already ended.', 'RUN_ENDED'));

    renderStep();

    const input = await screen.findByLabelText('Team-Code');
    await userEvent.type(input, 'SHARE01');
    await userEvent.click(screen.getByRole('button', { name: 'Team beitreten' }));

    expect(
      await screen.findByText('Dieser Lauf ist bereits beendet, du kannst keinem Team mehr beitreten.'),
    ).toBeInTheDocument();
  });
});

// The join button must show a busy state on a slow connection, but only once
// the wait outlasts a short delay, so a fast join never flashes a spinner.
describe('<JoinTeamStep> busy indicator', () => {
  let user: ReturnType<typeof userEvent.setup>;

  beforeEach(() => {
    mockJoinAsGuest.mockReset();
    sessionStorage.clear();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    user = userEvent.setup({ delay: null });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows the normal label and no spinner before the delay while joining', async () => {
    mockJoinAsGuest.mockReturnValue(new Promise(() => {}));

    renderStep();

    const input = await screen.findByLabelText('Team-Code');
    await user.type(input, 'SHARE01');
    await user.click(screen.getByRole('button', { name: 'Team beitreten' }));

    await act(async () => {
      vi.advanceTimersByTime(300);
    });

    expect(screen.getByRole('button', { name: 'Team beitreten' })).toBeInTheDocument();
    expect(screen.queryByTestId('loading-indicator')).not.toBeInTheDocument();
  });

  it('shows a busy label and spinner after the delay while joining', async () => {
    mockJoinAsGuest.mockReturnValue(new Promise(() => {}));

    renderStep();

    const input = await screen.findByLabelText('Team-Code');
    await user.type(input, 'SHARE01');
    await user.click(screen.getByRole('button', { name: 'Team beitreten' }));

    await act(async () => {
      vi.advanceTimersByTime(400);
    });

    expect(screen.getByRole('button', { name: 'Tritt bei…' })).toBeInTheDocument();
    expect(screen.getByTestId('loading-indicator')).toBeInTheDocument();
  });
});

/**
 * Three refusals must not invite a retry that can never work: a denied camera,
 * the ban after leaving or removal, and the run of an archived organization.
 */
describe('<JoinTeamStep> permanent refusals', () => {
  beforeEach(() => {
    mockJoinAsGuest.mockReset();
    sessionStorage.clear();
  });

  it.each([
    [new Error('NotAllowedError: Permission denied'), 'Kamera-Zugriff verweigert. Bitte gib den Code manuell ein.'],
    [new Error('NotFoundError: Requested device not found'), 'Die Kamera lässt sich gerade nicht starten. Bitte gib den Code manuell ein.'],
  ])('a scanner that fails with %s says it is the camera', async (error, text) => {
    scannerFailure.error = error;
    renderStep();

    await userEvent.click(await screen.findByRole('button', { name: 'QR-Code scannen' }));

    expect(await screen.findByText(text)).toBeInTheDocument();
    expect(screen.queryByText('Beitritt fehlgeschlagen. Bitte versuche es erneut.')).not.toBeInTheDocument();
  });

  it.each([
    [new ApiError(403, 'Du hast diesen Run verlassen und kannst ihm nicht erneut beitreten.', 'RUN_DEPARTED'), /kannst ihm nicht erneut beitreten\. Bitte wende dich an die Spielleitung/],
    [new ApiError(403, 'This organization is archived.', 'ORG_ARCHIVED'), /Organisation archiviert wurde/],
  ])('names a permanent refusal (%s) instead of inviting a retry', async (error, text) => {
    mockJoinAsGuest.mockRejectedValue(error);
    renderStep();

    await userEvent.type(await screen.findByLabelText('Team-Code'), 'SHARE01');
    await userEvent.click(screen.getByRole('button', { name: 'Team beitreten' }));

    expect(await screen.findByText(text)).toBeInTheDocument();
    expect(screen.queryByText('Beitritt fehlgeschlagen. Bitte versuche es erneut.')).not.toBeInTheDocument();
  });

  // A QR code that is not a team code is refused in words, nothing is sent, and
  // the long text does not land in the code field.
  it('refuses a scanned QR code that is not a team code', async () => {
    scannerResult.text = 'https://www.augsburg.de/rathaus/goldener-saal';
    try {
      renderStep();
      await userEvent.click(await screen.findByRole('button', { name: 'QR-Code scannen' }));

      expect(await screen.findByText(/Das ist kein Team-Code/)).toBeInTheDocument();
      expect(mockJoinAsGuest).not.toHaveBeenCalled();
      expect(screen.getByLabelText(/Team-Code/)).toHaveValue('');
    } finally {
      scannerResult.text = null;
    }
  });
});
