import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import { OrgBootstrapRoute } from './Register';
import { AuthProvider } from '../auth/AuthContext';
import apiClient from '../api/client';
import {
  DEFAULT_INSTANCE_CONFIG,
  __resetInstanceConfigForTests,
} from '../config/instanceConfig';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    default: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
    },
  };
});

const LoginProbe = () => <div>login-page-probe</div>;
const TwoFaSetupProbe = () => <div>2fa-setup-probe</div>;
const DashboardProbe = () => <div>dashboard-probe</div>;

// The route as App.tsx mounts it. The bootstrap gate wraps the form, so the
// suite covers both the open and the closed instance.
const renderRegister = () =>
  render(
    <MemoryRouter initialEntries={['/register/organization']}>
      <Routes>
        <Route
          path="/register/organization"
          element={
            <AuthProvider>
              <OrgBootstrapRoute />
            </AuthProvider>
          }
        />
        <Route path="/login" element={<LoginProbe />} />
        <Route path="/2fa-setup" element={<TwoFaSetupProbe />} />
        <Route path="/dashboard" element={<DashboardProbe />} />
      </Routes>
    </MemoryRouter>,
  );

// Fill the bootstrap form with valid values and submit it.
const submitValidForm = async () => {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText('Name der Organisation'), 'Org');
  await user.type(screen.getByLabelText('Kurzname der Organisation'), 'org-slug');
  await user.type(screen.getByLabelText('E-Mail'), 'admin@example.com');
  await user.type(screen.getByLabelText('Anzeigename'), 'Admin');
  await user.type(screen.getByLabelText('Passwort'), 'SecurePassword123!');
  await user.type(screen.getByLabelText(/passwort bestätigen/i), 'SecurePassword123!');
  await user.click(screen.getByRole('button', { name: /konto erstellen/i }));
};

describe('Register', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    // The bootstrap form renders only while the instance config says the bootstrap
    // is open (no organization yet, or the operator opted into open signups).
    __resetInstanceConfigForTests({ ...DEFAULT_INSTANCE_CONFIG, bootstrap_open: true });
    const mockGet = vi.mocked(apiClient.get);
    mockGet.mockRejectedValue(new Error('Not authenticated'));
  });

  it('renders all required fields', () => {
    renderRegister();
    expect(screen.getByLabelText('Name der Organisation')).toBeInTheDocument();
    expect(screen.getByLabelText('Kurzname der Organisation')).toBeInTheDocument();
    expect(screen.getByLabelText('E-Mail')).toBeInTheDocument();
    expect(screen.getByLabelText('Anzeigename')).toBeInTheDocument();
    expect(screen.getByLabelText('Passwort')).toBeInTheDocument();
    expect(screen.getByLabelText('Passwort bestätigen')).toBeInTheDocument();
  });

  it('redirects to /login when the org bootstrap is closed', () => {
    __resetInstanceConfigForTests({ ...DEFAULT_INSTANCE_CONFIG, bootstrap_open: false });
    renderRegister();

    // The login page instead of a dead form — the route behaves as if it
    // did not exist once an organization is on the instance.
    expect(screen.getByText('login-page-probe')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /konto erstellen/i }),
    ).not.toBeInTheDocument();
    expect(vi.mocked(apiClient.post)).not.toHaveBeenCalled();
  });

  it('rejects weak passwords', async () => {
    renderRegister();
    const user = userEvent.setup();

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
    expect(vi.mocked(apiClient.post)).not.toHaveBeenCalled();
  });

  it('rejects mismatched passwords', async () => {
    renderRegister();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Name der Organisation'), 'Org');
    await user.type(screen.getByLabelText('Kurzname der Organisation'), 'org-slug');
    await user.type(screen.getByLabelText('E-Mail'), 'admin@example.com');
    await user.type(screen.getByLabelText('Anzeigename'), 'Admin');
    await user.type(screen.getByLabelText('Passwort'), 'SecurePassword123!');
    await user.type(screen.getByLabelText('Passwort bestätigen'), 'Different123!');
    await user.click(screen.getByRole('button', { name: /konto erstellen/i }));

    await waitFor(() => {
      expect(screen.getByText(/stimmen nicht überein/i)).toBeInTheDocument();
    });
    expect(vi.mocked(apiClient.post)).not.toHaveBeenCalled();
  });

  it('submits valid registration', async () => {
    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockResolvedValueOnce({
      data: {
        user_id: '1',
        organization_id: 'org-1',
        email: 'admin@example.com',
        display_name: 'Admin',
        role: 'org_admin',
        message: 'ok',
      },
    });

    renderRegister();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Name der Organisation'), 'Org');
    await user.type(screen.getByLabelText('Kurzname der Organisation'), 'org-slug');
    await user.type(screen.getByLabelText('E-Mail'), 'admin@example.com');
    await user.type(screen.getByLabelText('Anzeigename'), 'Admin');
    await user.type(screen.getByLabelText('Passwort'), 'SecurePassword123!');
    await user.type(screen.getByLabelText(/passwort bestätigen/i), 'SecurePassword123!');
    await user.click(screen.getByRole('button', { name: /konto erstellen/i }));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith('/api/auth/register', {
        organization_name: 'Org',
        organization_slug: 'org-slug',
        email: 'admin@example.com',
        display_name: 'Admin',
        password: 'SecurePassword123!',
      });
    });
  });

  it('stores the setup token and goes to /2fa-setup when the answer requires 2FA', async () => {
    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockResolvedValueOnce({
      data: {
        user_id: '1',
        organization_id: 'org-1',
        email: 'admin@example.com',
        display_name: 'Admin',
        role: 'org_admin',
        requires_2fa: true,
        setup_token: 'setup-token-xyz',
        message: 'ok',
      },
    });

    renderRegister();
    // The AuthProvider runs one checkAuth on mount; clear it so the asserts
    // below see only calls made by the submit handler.
    vi.mocked(apiClient.get).mockClear();
    await submitValidForm();

    // The restricted token lands where TwoFactorSetup reads it.
    expect(sessionStorage.getItem('clavis_2fa_setup_token')).toBe('setup-token-xyz');
    // Straight to enrollment — not the dashboard.
    expect(screen.getByText('2fa-setup-probe')).toBeInTheDocument();
    expect(screen.queryByText('dashboard-probe')).not.toBeInTheDocument();
    // No session check first: /auth/me would refuse the restricted token.
    expect(vi.mocked(apiClient.get)).not.toHaveBeenCalled();
  });

  it('calls checkAuth and goes to /dashboard when no 2FA is required', async () => {
    const mockPost = vi.mocked(apiClient.post);
    const mockGet = vi.mocked(apiClient.get);
    mockPost.mockResolvedValueOnce({
      data: {
        user_id: '1',
        organization_id: 'org-1',
        email: 'admin@example.com',
        display_name: 'Admin',
        role: 'org_admin',
        requires_2fa: false,
        message: 'ok',
      },
    });
    mockGet.mockResolvedValueOnce({ data: { id: '1', email: 'admin@example.com' } });

    renderRegister();
    vi.mocked(apiClient.get).mockClear();
    await submitValidForm();
    // The submit handler runs the session check before navigating.
    await waitFor(() => {
      expect(screen.getByText('dashboard-probe')).toBeInTheDocument();
    });
    expect(mockGet).toHaveBeenCalledWith('/api/auth/me');
    expect(sessionStorage.getItem('clavis_2fa_setup_token')).toBeNull();
    expect(screen.queryByText('2fa-setup-probe')).not.toBeInTheDocument();
  });

  it('has no axe accessibility violations on initial render', async () => {
    const { container } = renderRegister();
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
