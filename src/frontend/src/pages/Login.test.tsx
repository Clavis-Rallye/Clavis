import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter } from 'react-router';
import { axe } from 'vitest-axe';
import Login from './Login';
import { AuthProvider } from '../auth/AuthContext';
import apiClient, { ApiError } from '../api/client';

// Mock the API client's transport only. ApiError stays real, because the
// sign-in words its message from the error code.
vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  };
});

const renderLogin = () => {
  return render(
    <BrowserRouter>
      <AuthProvider>
        <Login />
      </AuthProvider>
    </BrowserRouter>
  );
};

describe('Login Component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders login form with email and password fields', () => {
    renderLogin();

    expect(screen.getByLabelText(/e-mail/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/passwort/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /anmelden/i })).toBeInTheDocument();
  });

  // A new browser profile can change the language here.
  it('offers the language switch', () => {
    renderLogin();
    const group = screen.getByRole('group', { name: 'Sprache wechseln' });
    expect(within(group).getByRole('button', { name: 'DE' })).toBeInTheDocument();
    expect(within(group).getByRole('button', { name: 'EN' })).toBeInTheDocument();
  });

  it('validates empty fields', async () => {
    renderLogin();
    const user = userEvent.setup();

    const submitButton = screen.getByRole('button', { name: /anmelden/i });
    await user.click(submitButton);

    // HTML5 validation should prevent submission with required fields empty
    const emailInput = screen.getByLabelText(/e-mail/i) as HTMLInputElement;
    const passwordInput = screen.getByLabelText(/passwort/i) as HTMLInputElement;

    expect(emailInput.validity.valid).toBe(false);
    expect(passwordInput.validity.valid).toBe(false);
  });

  it('submits login request with email and password', async () => {
    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockResolvedValueOnce({
      data: {
        user: {
          id: '123',
          email: 'test@example.com',
          display_name: 'Test User',
          role: 'participant',
          organization_id: 'org-123',
        },
      },
    });

    const mockGet = vi.mocked(apiClient.get);
    mockGet.mockResolvedValueOnce({
      data: {
        id: '123',
        email: 'test@example.com',
        display_name: 'Test User',
        role: 'participant',
        organization_id: 'org-123',
      },
    });

    renderLogin();
    const user = userEvent.setup();

    const emailInput = screen.getByLabelText(/e-mail/i);
    const passwordInput = screen.getByLabelText(/passwort/i);
    const submitButton = screen.getByRole('button', { name: /anmelden/i });

    await user.type(emailInput, 'test@example.com');
    await user.type(passwordInput, 'SecurePassword123!');
    await user.click(submitButton);

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith('/api/auth/login', {
        email: 'test@example.com',
        password: 'SecurePassword123!',
      });
    });
  });

  it('displays error message on login failure', async () => {
    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockRejectedValueOnce(
      new ApiError(401, 'Invalid credentials', 'INVALID_CREDENTIALS'),
    );

    const mockGet = vi.mocked(apiClient.get);
    mockGet.mockRejectedValueOnce(new Error('Not authenticated'));

    renderLogin();
    const user = userEvent.setup();

    const emailInput = screen.getByLabelText(/e-mail/i);
    const passwordInput = screen.getByLabelText(/passwort/i);
    const submitButton = screen.getByRole('button', { name: /anmelden/i });

    await user.type(emailInput, 'test@example.com');
    await user.type(passwordInput, 'WrongPassword');
    await user.click(submitButton);

    await waitFor(() => {
      // German, never the API's English detail.
      expect(screen.getByText('E-Mail-Adresse oder Passwort ist falsch.')).toBeInTheDocument();
    });
    expect(screen.queryByText('Invalid credentials')).not.toBeInTheDocument();
    await waitFor(() => {
    });
  });

  it('stores email and redirects to /2fa-verify when 2FA is required', async () => {
    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockResolvedValueOnce({
      data: {
        status: '2fa_required',
      },
    });
    const mockGet = vi.mocked(apiClient.get);
    mockGet.mockRejectedValueOnce(new Error('Not authenticated'));

    sessionStorage.clear();

    renderLogin();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText(/e-mail/i), 'test@example.com');
    await user.type(screen.getByLabelText(/passwort/i), 'SecurePassword123!');
    await user.click(screen.getByRole('button', { name: /anmelden/i }));

    await waitFor(() => {
      expect(sessionStorage.getItem('clavis_2fa_pending_email')).toBe('test@example.com');
    });
    expect(window.location.pathname).toBe('/2fa-verify');
  });

  it('stores setup token and redirects to /2fa-setup for mandatory 2FA enrollment', async () => {
    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockResolvedValueOnce({
      data: {
        status: '2fa_setup_required',
        setup_token: 'setup-token-xyz',
      },
    });
    const mockGet = vi.mocked(apiClient.get);
    mockGet.mockRejectedValueOnce(new Error('Not authenticated'));

    sessionStorage.clear();

    renderLogin();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText(/e-mail/i), 'admin@example.com');
    await user.type(screen.getByLabelText(/passwort/i), 'SecurePassword123!');
    await user.click(screen.getByRole('button', { name: /anmelden/i }));

    await waitFor(() => {
      expect(sessionStorage.getItem('clavis_2fa_setup_token')).toBe('setup-token-xyz');
    });
    expect(window.location.pathname).toBe('/2fa-setup');
  });

  it('disables form fields while loading', async () => {
    const mockPost = vi.mocked(apiClient.post);
    // Never resolve to keep loading state
    mockPost.mockImplementation(() => new Promise(() => {}));

    const mockGet = vi.mocked(apiClient.get);
    mockGet.mockRejectedValueOnce(new Error('Not authenticated'));

    renderLogin();
    const user = userEvent.setup();

    const emailInput = screen.getByLabelText(/e-mail/i) as HTMLInputElement;
    const passwordInput = screen.getByLabelText(/passwort/i) as HTMLInputElement;
    const submitButton = screen.getByRole('button', { name: /anmelden/i }) as HTMLButtonElement;

    await user.type(emailInput, 'test@example.com');
    await user.type(passwordInput, 'SecurePassword123!');
    await user.click(submitButton);

    await waitFor(() => {
      expect(emailInput.disabled).toBe(true);
      expect(passwordInput.disabled).toBe(true);
      expect(submitButton.disabled).toBe(true);
    });
  });

  // The API client's interceptor lands a session ended by a password change on
  // /login?reason=session_revoked. The page explains what happened instead of
  // silently asking for credentials again.
  it('shows the session-revoked notice when reason=session_revoked', () => {
    window.history.pushState({}, '', '/login?reason=session_revoked');
    renderLogin();

    expect(
      screen.getByText('Deine Sitzung wurde aus Sicherheitsgründen beendet. Bitte melde dich neu an.'),
    ).toBeInTheDocument();
  });

  // A member whose organization was archived is sent here with a reason and reads why.
  it('shows the archived-organization notice when reason=org_archived', () => {
    window.history.pushState({}, '', '/login?reason=org_archived');
    renderLogin();

    expect(
      screen.getByText('Diese Organisation ist archiviert. Eine Anmeldung ist nicht mehr möglich.'),
    ).toBeInTheDocument();
  });

  // An admin deactivated the account.
  it('shows the deactivated-account notice when reason=account_disabled', () => {
    window.history.pushState({}, '', '/login?reason=account_disabled');
    renderLogin();

    expect(
      screen.getByText('Dein Konto ist deaktiviert. Wende dich an die Verwaltung deiner Organisation.'),
    ).toBeInTheDocument();
  });

  it('shows no notice without the reason parameter', () => {
    window.history.pushState({}, '', '/login');
    renderLogin();

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('has no axe accessibility violations on initial render', async () => {
    const mockGet = vi.mocked(apiClient.get);
    mockGet.mockRejectedValueOnce(new Error('Not authenticated'));

    const { container } = renderLogin();
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  // Tab from the password field reaches the sign-in button before either link,
  // matching the DOM order. The show-password toggle sits inside the password field.
  it('tabs from the password field through sign-in, forgot-password, then register', async () => {
    renderLogin();
    const user = userEvent.setup();

    const passwordInput = screen.getByLabelText(/passwort/i);
    const showPasswordToggle = screen.getByRole('button', { name: /eingabe anzeigen/i });
    const submitButton = screen.getByRole('button', { name: /anmelden/i });
    const forgotPasswordLink = screen.getByRole('link', { name: /passwort vergessen/i });
    const registerLink = screen.getByRole('link', { name: /registrieren/i });

    passwordInput.focus();
    expect(document.activeElement).toBe(passwordInput);

    await user.tab();
    expect(document.activeElement).toBe(showPasswordToggle);

    await user.tab();
    expect(document.activeElement).toBe(submitButton);

    await user.tab();
    expect(document.activeElement).toBe(forgotPasswordLink);

    await user.tab();
    expect(document.activeElement).toBe(registerLink);
  });

  // A person who just created the account from an invitation is not "back".
  // The page greets a new person and already holds the address.
  describe('arriving from an accepted invitation', () => {
    const arrive = (state: unknown) => {
      // BrowserRouter keeps the navigation state under `usr`.
      window.history.pushState({ usr: state, key: 'invite' }, '', '/login');
      renderLogin();
    };

    it('greets a new person and fills the address of the invitation', () => {
      arrive({ newAccount: true, email: 'neue.kollegin@augsburg.example' });

      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Willkommen bei Clavis!');
      expect(
        screen.getByText('Dein Konto ist eingerichtet. Melde dich jetzt mit deinem neuen Passwort an.'),
      ).toBeInTheDocument();
      expect(screen.getByLabelText(/e-mail/i)).toHaveValue('neue.kollegin@augsburg.example');
      expect(window.location.href).not.toContain('augsburg');
    });

    it('greets a returning person with an empty address without the mark', () => {
      arrive(null);

      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Willkommen zurück bei Clavis!');
      expect(screen.getByLabelText(/e-mail/i)).toHaveValue('');
    });

    it('ignores an address that comes without the mark', () => {
      arrive({ email: 'fremd@example.com' });

      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Willkommen zurück bei Clavis!');
      expect(screen.getByLabelText(/e-mail/i)).toHaveValue('');
    });
  });
});
