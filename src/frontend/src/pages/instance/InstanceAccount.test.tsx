import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import { ThemeProvider } from '@mui/material/styles';
import InstanceAccount from './InstanceAccount';
import theme from '../../theme';
import apiClient, { ApiError } from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const meResponse = { id: 'admin-1', email: 'operator@example.com', totp_confirmed: true };

const renderPage = () =>
  render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={['/instance/account']}>
        <Routes>
          <Route path="/instance/account" element={<InstanceAccount />} />
          <Route path="/instance/login" element={<div>Operator login</div>} />
          <Route path="/login" element={<div>Regular login</div>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );

const mockAuthenticated = () => {
  vi.mocked(apiClient.get).mockImplementation((url: string) => {
    if (url === '/api/instance/auth/me') return Promise.resolve({ data: meResponse });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
};

const fillForm = async (oldPw: string, newPw: string, confirmPw: string) => {
  await userEvent.type(screen.getByLabelText(/aktuelles passwort/i), oldPw);
  await userEvent.type(screen.getByLabelText(/^neues passwort[\s*]*$/i), newPw);
  await userEvent.type(screen.getByLabelText(/neues passwort bestätigen/i), confirmPw);
};

describe('InstanceAccount', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('renders the signed-in operator email and the password form', async () => {
    mockAuthenticated();
    renderPage();

    await waitFor(() => expect(screen.getByText('operator@example.com')).toBeInTheDocument());
    expect(screen.getByLabelText(/aktuelles passwort/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^neues passwort[\s*]*$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/neues passwort bestätigen/i)).toBeInTheDocument();
  });

  // The frame is as wide as on the organization list,
  // so the header and "Abmelden" do not move between the pages. Only the card
  // is narrow.
  it('keeps the console frame at the widest page width', async () => {
    mockAuthenticated();
    renderPage();
    const main = await screen.findByRole('main');
    expect(main.parentElement).toHaveClass('MuiContainer-maxWidthXl');
    expect(main).toHaveStyle({ maxWidth: '600px' });
  });

  // Change the own password.
  it('changes the password and shows the success message', async () => {
    mockAuthenticated();
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { message: 'Password changed successfully' } });
    renderPage();
    await waitFor(() => expect(screen.getByText('operator@example.com')).toBeInTheDocument());

    await fillForm('altesPasswort1!', 'NeuesPasswort1!', 'NeuesPasswort1!');
    await userEvent.click(screen.getByRole('button', { name: 'Passwort ändern' }));

    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith('/api/instance/auth/password', {
        old_password: 'altesPasswort1!',
        new_password: 'NeuesPasswort1!',
      }),
    );
    await waitFor(() => expect(screen.getByText('Das Passwort wurde geändert.')).toBeInTheDocument());
  });

  it('shows a field error when the new password does not meet the policy, without calling the API', async () => {
    mockAuthenticated();
    renderPage();
    await waitFor(() => expect(screen.getByText('operator@example.com')).toBeInTheDocument());

    await fillForm('altesPasswort1!', 'kurz', 'kurz');
    await userEvent.click(screen.getByRole('button', { name: 'Passwort ändern' }));

    expect(
      screen.getByText(
        'Das Passwort muss mindestens 8 Zeichen lang sein und drei der folgenden Arten enthalten: Großbuchstaben, Kleinbuchstaben, Ziffern, Sonderzeichen.',
      ),
    ).toBeInTheDocument();
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it('shows a field error when the confirmation does not match, without calling the API', async () => {
    mockAuthenticated();
    renderPage();
    await waitFor(() => expect(screen.getByText('operator@example.com')).toBeInTheDocument());

    await fillForm('altesPasswort1!', 'NeuesPasswort1!', 'AnderesPasswort1!');
    await userEvent.click(screen.getByRole('button', { name: 'Passwort ändern' }));

    expect(screen.getByText('Die Passwörter stimmen nicht überein.')).toBeInTheDocument();
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it('shows the current-password field error on a 401 from the API', async () => {
    mockAuthenticated();
    vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(401, 'Current password is incorrect'));
    renderPage();
    await waitFor(() => expect(screen.getByText('operator@example.com')).toBeInTheDocument());

    await fillForm('falschesPasswort1!', 'NeuesPasswort1!', 'NeuesPasswort1!');
    await userEvent.click(screen.getByRole('button', { name: 'Passwort ändern' }));

    await waitFor(() =>
      expect(screen.getByText('Das aktuelle Passwort ist falsch.')).toBeInTheDocument(),
    );
  });

  it('shows the reuse field error on the coded 400', async () => {
    mockAuthenticated();
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      new ApiError(400, 'New password must not match the current or the last password.', 'INSTANCE_PASSWORD_REUSE_NOT_ALLOWED'),
    );
    renderPage();
    await waitFor(() => expect(screen.getByText('operator@example.com')).toBeInTheDocument());

    await fillForm('altesPasswort1!', 'NeuesPasswort1!', 'NeuesPasswort1!');
    await userEvent.click(screen.getByRole('button', { name: 'Passwort ändern' }));

    await waitFor(() =>
      expect(
        screen.getByText('Das neue Passwort darf nicht dem aktuellen oder dem letzten Passwort entsprechen.'),
      ).toBeInTheDocument(),
    );
  });

  it('redirects to /instance/login on a 401 from the session probe', async () => {
    vi.mocked(apiClient.get).mockRejectedValueOnce(new ApiError(401, 'Not authenticated'));
    renderPage();

    await waitFor(() => expect(screen.getByText('Operator login')).toBeInTheDocument());
  });

  it('shows the no-operator-access message and a link to the regular login on a 403', async () => {
    vi.mocked(apiClient.get).mockRejectedValueOnce(
      new ApiError(403, 'Organization token not valid for instance endpoints'),
    );
    renderPage();

    await waitFor(() => expect(screen.getByText('Kein Betreiber-Zugriff')).toBeInTheDocument());
    expect(screen.getByRole('link', { name: /zur üblichen anmeldung/i })).toHaveAttribute(
      'href',
      '/login',
    );
  });

  it('renders the navigation with links to the other operator pages', async () => {
    mockAuthenticated();
    renderPage();
    await waitFor(() => expect(screen.getByText('operator@example.com')).toBeInTheDocument());

    const nav = screen.getByRole('navigation', { name: 'Betreiber-Navigation' });
    expect(within(nav).getByRole('link', { name: 'Organisationen' })).toBeInTheDocument();
    expect(within(nav).getByRole('link', { name: 'Rechtstexte' })).toBeInTheDocument();
    expect(within(nav).getByRole('link', { name: 'Protokoll' })).toBeInTheDocument();
  });

  it('has no axe accessibility violations once signed in', async () => {
    mockAuthenticated();
    const { container } = renderPage();
    await waitFor(() => expect(screen.getByText('operator@example.com')).toBeInTheDocument());

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
