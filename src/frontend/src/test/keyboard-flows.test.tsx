import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter, MemoryRouter, Routes, Route } from 'react-router';
import Login from '../pages/Login';
import Register from '../pages/Register';
import TeamChat from '../pages/participant/TeamChat';
import AppShell from '../layouts/AppShell';
import { AuthProvider, AuthContext } from '../auth/AuthContext';
import apiClient from '../api/client';
import { useWebSocket } from '../hooks/useWebSocket';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

vi.mock('../hooks/useWebSocket', () => ({
  useWebSocket: vi.fn(),
}));

const authUser = {
  id: 'u1',
  email: 'participant@example.com',
  display_name: 'Teilnehmer',
  role: 'coordinator' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const TEAM_KEY = 'clavis_team_for_run_run-1';

describe('Keyboard navigation flows', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiClient.get).mockRejectedValue(new Error('Not authenticated'));
    vi.mocked(useWebSocket).mockReturnValue({ status: 'open', lastError: null, send: vi.fn() });
  });

  it('Login: Tab moves focus email → password → toggle → submit → forgot link → register link', async () => {
    const user = userEvent.setup();

    render(
      <BrowserRouter>
        <AuthProvider>
          <Login />
        </AuthProvider>
      </BrowserRouter>,
    );

    const emailInput = screen.getByLabelText(/e-mail/i);
    const passwordInput = screen.getByLabelText(/passwort/i);
    const passwordToggle = screen.getByRole('button', { name: /eingabe anzeigen/i });
    const forgotLink = screen.getByRole('link', { name: /passwort.*vergessen|forgot/i });
    const registerLink = screen.getByRole('link', { name: /registrieren|register/i });
    const submitButton = screen.getByRole('button', { name: /anmelden/i });

    // Tab order: the sign-in button precedes the two links in the DOM, so Tab runs
    // email → password → show/hide-password toggle → sign-in button →
    // forgot-password link → register link.
    // The clear-X icon buttons inside ClavisTextField use tabIndex={-1} and are skipped,
    // but the password show/hide toggle is intentionally keyboard-focusable (a11y).
    await user.tab();
    expect(emailInput).toHaveFocus();

    await user.tab();
    expect(passwordInput).toHaveFocus();

    await user.tab();
    expect(passwordToggle).toHaveFocus();

    await user.tab();
    expect(submitButton).toHaveFocus();

    await user.tab();
    expect(forgotLink).toHaveFocus();

    await user.tab();
    expect(registerLink).toHaveFocus();
  });

  it('Login: Enter on submit button triggers POST', async () => {
    const user = userEvent.setup();

    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { status: 'success', user_id: 'u1', email: 'user@test.com', display_name: 'User', role: 'coordinator', organization_id: 'org-1', message: 'ok' },
    });
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { id: 'u1', email: 'user@test.com', display_name: 'User', role: 'coordinator', organization_id: 'org-1', organization_slug: 'test-org' },
    });

    render(
      <BrowserRouter>
        <AuthProvider>
          <Login />
        </AuthProvider>
      </BrowserRouter>,
    );

    await user.type(screen.getByLabelText(/e-mail/i), 'user@test.com');
    await user.type(screen.getByLabelText(/passwort/i), 'Password1!');

    // The submit button is the MUI Button that Login.tsx renders as the last
    // control of the form inside OnboardingShell.
    // JSDOM does not reliably trigger implicit form submission on Enter:
    // focusing the button and pressing Enter on it directly is the
    // deterministic way to trigger keyboard submit.
    const submitButton = screen.getByRole('button', { name: /anmelden/i });
    submitButton.focus();
    await user.keyboard('{Enter}');

    await waitFor(() => {
      expect(apiClient.post).toHaveBeenCalledWith('/api/auth/login', expect.objectContaining({
        email: 'user@test.com',
      }));
    });
  });

  it('Register: Tab through all fields in visual order', async () => {
    const user = userEvent.setup();

    render(
      <BrowserRouter>
        <AuthProvider>
          <Register />
        </AuthProvider>
      </BrowserRouter>,
    );

    const orgName = screen.getByLabelText('Name der Organisation');
    const orgSlug = screen.getByLabelText('Kurzname der Organisation');
    const email = screen.getByLabelText('E-Mail');
    const displayName = screen.getByLabelText('Anzeigename');
    const password = screen.getByLabelText('Passwort');
    const confirmPassword = screen.getByLabelText('Passwort bestätigen');

    await user.tab();
    expect(orgName).toHaveFocus();

    await user.tab();
    expect(orgSlug).toHaveFocus();

    await user.tab();
    expect(email).toHaveFocus();

    await user.tab();
    expect(displayName).toHaveFocus();

    await user.tab();
    expect(password).toHaveFocus();

    // The password field carries a show/hide toggle (matching Login) — a
    // focusable affordance that sits between the two password inputs.
    await user.tab();
    expect(screen.getAllByRole('button', { name: /eingabe anzeigen/i })[0]).toHaveFocus();

    await user.tab();
    expect(confirmPassword).toHaveFocus();
  });

  it('TeamChat: Tab to input, type message, Tab to send, Enter posts the message', async () => {
    const user = userEvent.setup();

    sessionStorage.setItem(TEAM_KEY, 'team-1');
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [], total: 0, skip: 0, limit: 100 },
    });
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: {
        id: 'm1',
        organization_id: 'org-1',
        team_id: 'team-1',
        author_id: 'u1',
        content: 'Hallo Welt',
        is_system_message: false,
        created_at: '2026-01-01T10:00:00Z',
      },
    });

    render(
      <MemoryRouter initialEntries={['/runs/run-1/participant/chat']}>
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
            <Route path="/runs/:runId/participant/chat" element={<TeamChat />} />
          </Routes>
        </AuthContext.Provider>
      </MemoryRouter>,
    );

    await waitFor(() => screen.getByPlaceholderText(/Nachricht schreiben/i));

    const input = screen.getByPlaceholderText(/Nachricht schreiben/i);
    input.focus();
    await user.type(input, 'Hallo Welt');

    // Tab to send button and press Enter
    await user.tab();
    const sendButton = screen.getByRole('button', { name: /senden/i });
    expect(sendButton).toHaveFocus();

    await user.keyboard('{Enter}');

    await waitFor(() => {
      expect(apiClient.post).toHaveBeenCalledWith(
        '/api/orgs/test-org/runs/run-1/teams/team-1/chat/',
        { content: 'Hallo Welt' },
      );
    });
  });

  it('AppShell nav: skip link is the first focusable element', async () => {
    const user = userEvent.setup();

    vi.mocked(apiClient.get).mockResolvedValue({
      data: { id: 'u1', email: 'coord@example.com', display_name: 'Coordinator', role: 'coordinator', organization_id: 'org-1', organization_slug: 'test-org' },
    });

    render(
      <MemoryRouter initialEntries={['/dashboard']}>
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
            <Route element={<AppShell />}>
              <Route path="/dashboard" element={<div id="main-content">Dashboard</div>} />
              <Route path="/runs" element={<div>Runs</div>} />
            </Route>
          </Routes>
        </AuthContext.Provider>
      </MemoryRouter>,
    );

    // First tab from the document body hits the skip link
    await user.tab();
    const focused = document.activeElement;
    expect(focused?.tagName).toBe('A');
    expect(focused).toHaveAttribute('href', '#main-content');
  });
});
