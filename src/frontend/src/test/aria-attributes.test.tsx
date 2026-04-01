import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter, MemoryRouter, Routes, Route } from 'react-router';
import Register from '../pages/Register';
import ConfirmDialog from '../components/ConfirmDialog';
import TeamChat from '../pages/participant/TeamChat';
import FeedView from '../pages/participant/FeedView';
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
  email: 'coord@example.com',
  display_name: 'Coordinator',
  role: 'coordinator' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const TEAM_KEY = 'clavis_team_for_run_run-1';

describe('ARIA attribute tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiClient.get).mockRejectedValue(new Error('Not authenticated'));
    vi.mocked(useWebSocket).mockReturnValue({ status: 'open', lastError: null, send: vi.fn() });
  });

  it('Register: required TextFields carry the required attribute', () => {
    render(
      <BrowserRouter>
        <AuthProvider>
          <Register />
        </AuthProvider>
      </BrowserRouter>,
    );

    const requiredInputs = [
      screen.getByLabelText('Name der Organisation'),
      screen.getByLabelText('Kurzname der Organisation'),
      screen.getByLabelText('E-Mail'),
      screen.getByLabelText('Anzeigename'),
      screen.getByLabelText('Passwort'),
      screen.getByLabelText('Passwort bestätigen'),
    ];

    for (const input of requiredInputs) {
      expect(input).toBeRequired();
    }
  });

  it('Register: password TextField gets aria-invalid after weak-password submission', async () => {
    const user = userEvent.setup();

    render(
      <BrowserRouter>
        <AuthProvider>
          <Register />
        </AuthProvider>
      </BrowserRouter>,
    );

    await user.type(screen.getByLabelText('Name der Organisation'), 'Org');
    await user.type(screen.getByLabelText('Kurzname der Organisation'), 'org-slug');
    await user.type(screen.getByLabelText('E-Mail'), 'admin@example.com');
    await user.type(screen.getByLabelText('Anzeigename'), 'Admin');
    await user.type(screen.getByLabelText('Passwort'), 'weak');
    await user.type(screen.getByLabelText('Passwort bestätigen'), 'weak');
    await user.click(screen.getByRole('button', { name: /konto erstellen/i }));

    await waitFor(() => {
      expect(screen.getByText(/3 von 4 erfüllen/i)).toBeInTheDocument();
    });

    const passwordInput = screen.getByLabelText('Passwort');
    expect(passwordInput).toHaveAttribute('aria-invalid', 'true');
  });

  it('Register: password helper text has role="alert" after error', async () => {
    const user = userEvent.setup();

    render(
      <BrowserRouter>
        <AuthProvider>
          <Register />
        </AuthProvider>
      </BrowserRouter>,
    );

    await user.type(screen.getByLabelText('Passwort'), 'weak');
    await user.type(screen.getByLabelText('Passwort bestätigen'), 'weak');
    await user.click(screen.getByRole('button', { name: /konto erstellen/i }));

    await waitFor(() => {
      expect(screen.getByText(/3 von 4 erfüllen/i)).toBeInTheDocument();
    });

    const alert = screen.getByText(/3 von 4 erfüllen/i);
    expect(alert).toHaveAttribute('role', 'alert');
  });

  it('ConfirmDialog: dialog has aria-labelledby and aria-describedby', () => {
    render(
      <ConfirmDialog
        open={true}
        title="Löschen bestätigen"
        message="Soll der Eintrag wirklich gelöscht werden?"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-labelledby', 'confirm-dialog-title');
    expect(dialog).toHaveAttribute('aria-describedby', 'confirm-dialog-description');
  });

  it('ConfirmDialog: confirm button receives autoFocus when dialog opens', () => {
    render(
      <ConfirmDialog
        open={true}
        title="Löschen bestätigen"
        message="Soll der Eintrag wirklich gelöscht werden?"
        confirmLabel="Bestätigen"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    const confirmButton = screen.getByRole('button', { name: 'Bestätigen' });
    expect(document.activeElement).toBe(confirmButton);
  });

  it('TeamChat: message list has role="log" and aria-live="polite"', async () => {
    sessionStorage.setItem(TEAM_KEY, 'team-1');
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [], total: 0, skip: 0, limit: 100 },
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

    const messageList = screen.getByRole('log');
    expect(messageList).toHaveAttribute('aria-live', 'polite');
  });

  it('FeedView: feed container has aria-live="polite"', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [], total: 0, skip: 0, limit: 100 },
    });

    render(
      <MemoryRouter initialEntries={['/runs/run-1/participant/feed']}>
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
            <Route path="/runs/:runId/participant/feed" element={<FeedView />} />
          </Routes>
        </AuthContext.Provider>
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByText('Noch keine Mitteilungen')).toBeInTheDocument(),
    );

    const liveRegion = document.querySelector('[aria-live="polite"]');
    expect(liveRegion).not.toBeNull();
  });

  it('AppShell: first focusable element is skip link pointing to #main-content', async () => {
    const user = userEvent.setup();

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
              <Route path="/dashboard" element={<div>Dashboard Content</div>} />
            </Route>
          </Routes>
        </AuthContext.Provider>
      </MemoryRouter>,
    );

    await user.tab();
    const focused = document.activeElement;
    expect(focused?.tagName).toBe('A');
    expect(focused).toHaveAttribute('href', '#main-content');
  });
});
