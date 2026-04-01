import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter } from 'react-router';
import { axe } from 'vitest-axe';
import Profile from './Profile';
import { AuthProvider } from '../auth/AuthContext';
import apiClient from '../api/client';

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

const renderPage = () =>
  render(
    <BrowserRouter>
      <AuthProvider>
        <Profile />
      </AuthProvider>
    </BrowserRouter>,
  );

const baseProfile = {
  id: 'user-1',
  email: 'admin@example.com',
  display_name: 'Admin',
  role: 'org_admin' as const,
  organization_id: 'org-1',
  is_active: true,
  totp_enabled: false,
  created_at: '2025-01-01T00:00:00Z',
  last_login: '2025-06-15T10:30:00Z',
};

describe('Profile', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    const mockGet = vi.mocked(apiClient.get);
    mockGet
      .mockResolvedValueOnce({ data: { ...baseProfile } })
      .mockResolvedValueOnce({ data: { ...baseProfile } });
  });

  it('loads and displays profile data', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByDisplayValue('Admin')).toBeInTheDocument();
    });
    expect(screen.getByDisplayValue('admin@example.com')).toBeInTheDocument();
    expect(screen.getByText('Verwaltung')).toBeInTheDocument();
    expect(screen.queryByText('org_admin')).not.toBeInTheDocument();
  });

  it('saves display_name changes', async () => {
    const mockPut = vi.mocked(apiClient.put);
    mockPut.mockResolvedValueOnce({
      data: { ...baseProfile, display_name: 'Admin User', message: 'ok' },
    });

    renderPage();
    const user = userEvent.setup();

    const nameInput = await screen.findByDisplayValue('Admin');
    await user.clear(nameInput);
    await user.type(nameInput, 'Admin User');
    await user.click(screen.getByRole('button', { name: /änderungen speichern/i }));

    await waitFor(() => {
      expect(mockPut).toHaveBeenCalledWith('/api/auth/profile', { display_name: 'Admin User' });
    });
  });

  it('submits password change with current and new password', async () => {
    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockResolvedValueOnce({ data: { message: 'Password changed' } });

    renderPage();
    const user = userEvent.setup();

    await screen.findByDisplayValue('Admin');

    await user.type(screen.getByLabelText('Aktuelles Passwort *'), 'OldPassword1!');
    await user.type(screen.getByLabelText('Neues Passwort *'), 'NewSecure123!');
    await user.type(screen.getByLabelText('Neues Passwort bestätigen *'), 'NewSecure123!');
    await user.click(screen.getByRole('button', { name: /passwort ändern/i }));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith('/api/auth/password-change', {
        old_password: 'OldPassword1!',
        new_password: 'NewSecure123!',
      });
    });
  });

  it('shows Enable 2FA button when totp_enabled is false', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /2fa aktivieren/i })).toBeInTheDocument();
    });
  });

  it('shows Disable 2FA form when totp_enabled is true', async () => {
    const mockGet = vi.mocked(apiClient.get);
    mockGet.mockReset();
    mockGet
      .mockResolvedValueOnce({ data: { ...baseProfile, totp_enabled: true } })
      .mockResolvedValueOnce({ data: { ...baseProfile, totp_enabled: true } });

    renderPage();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /2fa deaktivieren/i })).toBeInTheDocument();
    });
  });

  it('downloads a JSON export when the export button is clicked', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: new Blob(['{}'], { type: 'application/json' }),
    });
    const createObjectURL = vi.fn(() => 'blob:mock');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL });

    renderPage();
    await waitFor(() => expect(screen.getByDisplayValue('Admin')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /als json exportieren|export as json/i }));

    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenCalledWith('/api/me/export', { responseType: 'blob' }),
    );
    expect(createObjectURL).toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it('has no axe accessibility violations on initial render', async () => {
    const { container } = renderPage();
    await waitFor(() => expect(screen.getByDisplayValue('Admin')).toBeInTheDocument());
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
