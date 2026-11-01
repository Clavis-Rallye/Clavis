import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React from 'react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router';
import { ThemeProvider } from '@mui/material/styles';
import InstanceLogin from './InstanceLogin';
import { useInstanceAuth } from './useInstanceAuth';
import { instanceMe, instanceLogout } from '../../api/instance';
import { ApiError } from '../../api/client';
import theme from '../../theme';

vi.mock('../../api/instance', () => ({
  instanceMe: vi.fn(),
  instanceLogin: vi.fn(),
  instanceTotpConfirm: vi.fn(),
  instanceLogout: vi.fn(),
}));

const me = { id: 'admin-1', email: 'operator@example.com', totp_confirmed: true };

// A console page in miniature. It mounts the hook as the operator pages do, so
// the session probe runs on mount, and it exposes signOut.
function ConsoleProbe() {
  const { signOut } = useInstanceAuth();
  return (
    <button type="button" onClick={() => void signOut()}>
      Abmelden
    </button>
  );
}

function LocationDisplay() {
  const loc = useLocation();
  return <div data-testid="location">{loc.pathname + loc.search}</div>;
}

// The operator console's route tree under React.StrictMode, so the mount effect
// and its session probe run twice.
const renderConsole = (initialEntry = '/instance') =>
  render(
    <React.StrictMode>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={[initialEntry]}>
          <LocationDisplay />
          <Routes>
            <Route path="/instance" element={<ConsoleProbe />} />
            <Route path="/instance/login" element={<InstanceLogin />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </React.StrictMode>,
  );

// jsdom has no Cache Storage API. The stubbed `caches` global lets the tests see
// the delete of the worker's /api/ cache.
function stubCaches(): ReturnType<typeof vi.fn> {
  const deleteCache = vi.fn().mockResolvedValue(true);
  vi.stubGlobal('caches', { delete: deleteCache });
  return deleteCache;
}

describe('useInstanceAuth', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.unstubAllGlobals();
    sessionStorage.removeItem('clavis_instance_signed_in');
  });

  // The tab knew an operator session. Under React.StrictMode the probe runs twice
  // and both answer 401. The superseded first probe answers last, after the login
  // page has mounted. If it still acted, it would find the flag cleared and
  // navigate to /instance/login without the reason, and the notice would be lost.
  it('keeps reason=session_expired when the stale probe answers after the current one', async () => {
    sessionStorage.setItem('clavis_instance_signed_in', '1');
    let rejectFirst!: (err: ApiError) => void;
    const firstProbe = new Promise<never>((_, reject) => {
      rejectFirst = reject;
    });
    vi.mocked(instanceMe)
      .mockReturnValueOnce(firstProbe) // StrictMode's first probe, superseded, answers last
      .mockRejectedValueOnce(new ApiError(401, 'Session expired')); // the current probe, which acts

    renderConsole();

    // The current probe navigates with the reason, and the login shows the notice.
    expect(
      await screen.findByText('Deine Sitzung ist abgelaufen. Bitte melde dich neu an.'),
    ).toBeInTheDocument();

    // Only now does the stale probe answer.
    rejectFirst(new ApiError(401, 'Session expired'));
    await act(async () => {
      await firstProbe.catch(() => undefined);
    });

    expect(screen.getByTestId('location')).toHaveTextContent(/^\/instance\/login\?reason=session_expired$/);
    expect(screen.getByText('Deine Sitzung ist abgelaufen. Bitte melde dich neu an.')).toBeInTheDocument();
  });

  // A first visit has no earlier session in this tab, so no reason and no notice.
  it('navigates to the bare login on a first visit', async () => {
    vi.mocked(instanceMe).mockRejectedValue(new ApiError(401, 'No session'));

    renderConsole();

    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent(/^\/instance\/login$/),
    );
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  // signOut clears the tab's signed-in flag, so a later session end counts as a
  // first visit again. The login it lands on shows no expired notice.
  it('clears the signed-in flag on signOut and lands on the login without a notice', async () => {
    vi.mocked(instanceMe).mockResolvedValue(me);
    vi.mocked(instanceLogout).mockResolvedValue({ message: 'Signed out' });

    renderConsole();

    // A successful probe marks the tab as signed in again.
    await waitFor(() => expect(sessionStorage.getItem('clavis_instance_signed_in')).toBe('1'));

    await userEvent.click(screen.getByRole('button', { name: 'Abmelden' }));

    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent(/^\/instance\/login$/),
    );
    expect(sessionStorage.getItem('clavis_instance_signed_in')).toBeNull();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  // Sign-out, a sign-in and the end of a session clear the service worker's
  // /api/ cache.

  it('clears the api-cache when the probe confirms a session', async () => {
    const deleteCache = stubCaches();
    vi.mocked(instanceMe).mockResolvedValue(me);

    renderConsole();

    // The login lands here: InstanceLogin navigates to /instance after a
    // successful sign-in and the hook probes the new session on mount.
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent(/^\/instance$/),
    );
    await waitFor(() => expect(deleteCache).toHaveBeenCalledWith('api-cache'));
  });

  it('clears the api-cache on signOut', async () => {
    const deleteCache = stubCaches();
    vi.mocked(instanceMe).mockResolvedValue(me);
    vi.mocked(instanceLogout).mockResolvedValue({ message: 'Signed out' });

    renderConsole();

    await waitFor(() => expect(sessionStorage.getItem('clavis_instance_signed_in')).toBe('1'));

    await userEvent.click(screen.getByRole('button', { name: 'Abmelden' }));

    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent(/^\/instance\/login$/),
    );
    expect(deleteCache).toHaveBeenCalledWith('api-cache');
  });

  it('clears the api-cache when the probe finds the session gone', async () => {
    const deleteCache = stubCaches();
    sessionStorage.setItem('clavis_instance_signed_in', '1');
    vi.mocked(instanceMe).mockRejectedValue(new ApiError(401, 'Session expired'));

    renderConsole();

    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent(/^\/instance\/login\?reason=session_expired$/),
    );
    await waitFor(() => expect(deleteCache).toHaveBeenCalledWith('api-cache'));
  });
});
