import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router';
import { axe } from 'vitest-axe';
import AcceptInvite from './AcceptInvite';
import apiClient, { ApiError } from '../api/client';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const preview = {
  email: 'neue.kollegin@augsburg.example',
  role: 'coordinator',
  organization_name: 'Stadt Augsburg',
  display_name_suggestion: 'Neue Kollegin',
  language: 'de',
  expires_at: '2026-08-03T12:00:00Z',
};

// Stands in for the sign-in page and shows what reached it, and by which way.
const LoginProbe = () => {
  const location = useLocation();
  return (
    <div>
      Anmeldung
      <span data-testid="login-state">{JSON.stringify(location.state)}</span>
      <span data-testid="login-url">{location.pathname + location.search + location.hash}</span>
    </div>
  );
};

const renderPage = (search = '?token=valid-token') =>
  render(
    <MemoryRouter initialEntries={[`/accept-invite${search}`]}>
      <Routes>
        <Route path="/accept-invite" element={<AcceptInvite />} />
        <Route path="/login" element={<LoginProbe />} />
      </Routes>
    </MemoryRouter>,
  );

describe('AcceptInvite', () => {
  beforeEach(() => vi.resetAllMocks());

  it('shows the organisation, address and role from the invitation', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: preview });

    renderPage();

    await waitFor(() => expect(screen.getByText(/Stadt Augsburg/)).toBeInTheDocument());
    expect(screen.getByText(/neue\.kollegin@augsburg\.example/)).toBeInTheDocument();
    // The suggested name is pre-filled but stays editable.
    expect(screen.getByDisplayValue('Neue Kollegin')).toBeInTheDocument();
  });

  it('never asks for the old password — only for a new one', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: preview });

    renderPage();

    await waitFor(() => expect(screen.getByText(/Stadt Augsburg/)).toBeInTheDocument());
    const passwordFields = screen
      .getAllByLabelText(/passwort/i, { selector: 'input' })
      .filter((el) => (el as HTMLInputElement).autocomplete === 'new-password');
    expect(passwordFields).toHaveLength(2);
  });

  it('rejects a password that fails the policy before calling the API', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: preview });

    renderPage();
    await waitFor(() => expect(screen.getByText(/Stadt Augsburg/)).toBeInTheDocument());

    const inputs = screen.getAllByLabelText(/passwort/i, { selector: 'input' });
    await userEvent.type(inputs[0]!, 'kurz');
    await userEvent.type(inputs[1]!, 'kurz');
    await userEvent.click(screen.getByRole('button', { name: /konto erstellen/i }));

    expect(vi.mocked(apiClient.post)).not.toHaveBeenCalled();
  });

  it('creates the account and moves the invitee to the sign-in page', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: preview });
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { message: 'ok', email: preview.email, role: 'coordinator' },
    });

    renderPage();
    await waitFor(() => expect(screen.getByText(/Stadt Augsburg/)).toBeInTheDocument());

    const inputs = screen.getAllByLabelText(/passwort/i, { selector: 'input' });
    await userEvent.type(inputs[0]!, 'SicheresPasswort123!');
    await userEvent.type(inputs[1]!, 'SicheresPasswort123!');
    await userEvent.click(screen.getByRole('button', { name: /konto erstellen/i }));

    await waitFor(() =>
      expect(vi.mocked(apiClient.post)).toHaveBeenCalledWith(
        '/api/invitations/valid-token/accept',
        expect.objectContaining({ display_name: 'Neue Kollegin' }),
      ),
    );
    expect(await screen.findByText(/konto erstellt/i)).toBeInTheDocument();
  });

  // The sign-in page learns that the account is new and which address it has.
  // Both travel in the navigation state, never in the URL.
  it('hands the address and the new-account mark to the sign-in page', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: preview });
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { message: 'ok', email: preview.email, role: 'coordinator' },
    });

    renderPage();
    await waitFor(() => expect(screen.getByText(/Stadt Augsburg/)).toBeInTheDocument());

    const inputs = screen.getAllByLabelText(/passwort/i, { selector: 'input' });
    await userEvent.type(inputs[0]!, 'SicheresPasswort123!');
    await userEvent.type(inputs[1]!, 'SicheresPasswort123!');
    await userEvent.click(screen.getByRole('button', { name: /konto erstellen/i }));

    const state = await screen.findByTestId('login-state', undefined, { timeout: 4000 });
    expect(JSON.parse(state.textContent ?? 'null')).toEqual({ newAccount: true, email: preview.email });
    expect(screen.getByTestId('login-url')).toHaveTextContent(/^\/login$/);
  });

  it.each([
    ['invitation_expired', /abgelaufen/i],
    ['invitation_revoked', /zurückgezogen/i],
    ['invitation_already_accepted', /bereits eingelöst/i],
    // Also a link replaced by a newer invitation mail.
    ['invitation_not_found', /gilt nicht mehr.*neuere Einladungs-E-Mail/i],
  ])('explains why a dead link is dead: %s', async (detail, expected) => {
    vi.mocked(apiClient.get).mockRejectedValueOnce(new ApiError(410, detail));

    renderPage();

    expect(await screen.findByText(expected)).toBeInTheDocument();
    // A dead link must not leave a form the invitee can fill in pointlessly.
    expect(screen.queryByRole('button', { name: /konto erstellen/i })).not.toBeInTheDocument();
  });

  // The address got an account between invitation and click.
  // The page says what to do, never the raw English.
  it('explains an address that already has an account', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: preview });
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      new ApiError(409, 'An account with this email address already exists', 'EMAIL_HAS_ACCOUNT'),
    );

    renderPage();
    await waitFor(() => expect(screen.getByText(/Stadt Augsburg/)).toBeInTheDocument());
    const inputs = screen.getAllByLabelText(/passwort/i, { selector: 'input' });
    await userEvent.type(inputs[0]!, 'SicheresPasswort123!');
    await userEvent.type(inputs[1]!, 'SicheresPasswort123!');
    await userEvent.click(screen.getByRole('button', { name: /konto erstellen/i }));

    expect(await screen.findByText(/gibt es schon ein Konto/i)).toBeInTheDocument();
    expect(screen.queryByText(/An account with this email/)).not.toBeInTheDocument();
  });

  it('handles a link with no token at all without calling the API', async () => {
    renderPage('');

    expect(await screen.findByText(/unvollständig/i)).toBeInTheDocument();
    expect(vi.mocked(apiClient.get)).not.toHaveBeenCalled();
  });

  it('has no detectable accessibility violations', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: preview });

    const { container } = renderPage();
    await waitFor(() => expect(screen.getByText(/Stadt Augsburg/)).toBeInTheDocument());

    const results = await axe(container);
    expect(results.violations).toEqual([]);
  });
});
