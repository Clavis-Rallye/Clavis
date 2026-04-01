import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BrowserRouter, MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import TwoFactorSetup from './TwoFactorSetup';
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
        <TwoFactorSetup />
      </AuthProvider>
    </BrowserRouter>,
  );

const setupPayload = {
  secret: 'JBSWY3DPEHPK3PXP',
  provisioning_uri: 'otpauth://totp/Clavis:user@example.com?secret=JBSWY3DPEHPK3PXP&issuer=Clavis',
  recovery_codes: ['ABC12345', 'DEF67890', 'GHI23456', 'JKL78901'],
  message: 'ok',
};

describe('TwoFactorSetup', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    const mockGet = vi.mocked(apiClient.get);
    mockGet.mockRejectedValue(new Error('Not authenticated'));
  });

  it('renders QR code and manual secret after setup call', async () => {
    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockResolvedValueOnce({ data: setupPayload });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/JBSWY3DPEHPK3PXP/)).toBeInTheDocument();
    });
    expect(document.querySelector('svg')).toBeInTheDocument();
  });

  it('verify button stays disabled until 6 digits are entered', async () => {
    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockResolvedValueOnce({ data: setupPayload });

    renderPage();
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByText(/JBSWY3DPEHPK3PXP/)).toBeInTheDocument();
    });

    const verifyButton = screen.getByRole('button', { name: /verifizieren und aktivieren/i });
    expect(verifyButton).toBeDisabled();

    await user.type(screen.getByLabelText(/6-stelliger Verifizierungscode/i), '12345');
    expect(verifyButton).toBeDisabled();

    await user.type(screen.getByLabelText(/6-stelliger Verifizierungscode/i), '6');
    expect(verifyButton).toBeEnabled();
  });

  it('shows recovery codes after successful verification', async () => {
    const mockPost = vi.mocked(apiClient.post);
    mockPost
      .mockResolvedValueOnce({ data: setupPayload })
      .mockResolvedValueOnce({
        data: { message: 'ok' },
      });

    renderPage();
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByText(/JBSWY3DPEHPK3PXP/)).toBeInTheDocument();
    });

    await user.type(screen.getByLabelText(/6-stelliger Verifizierungscode/i), '123456');
    await user.click(screen.getByRole('button', { name: /verifizieren und aktivieren/i }));

    await waitFor(() => {
      setupPayload.recovery_codes.forEach((code) => {
        expect(screen.getByText(code)).toBeInTheDocument();
      });
    });

    const finishButton = screen.getByRole('button', { name: /fertig/i });
    expect(finishButton).toBeDisabled();

    await user.click(screen.getByRole('checkbox'));
    expect(finishButton).toBeEnabled();
  });

  it('uses Bearer setup token from sessionStorage', async () => {
    sessionStorage.setItem('clavis_2fa_setup_token', 'setup-abc');
    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockResolvedValueOnce({ data: setupPayload });

    renderPage();

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        '/api/auth/2fa/totp/setup',
        undefined,
        { headers: { Authorization: 'Bearer setup-abc' } },
      );
    });
  });

  // A join-code carrier who lands here mid-registration must return to the join
  // flow, not the dashboard, once 2FA setup is done.
  it('honours state.from and returns there instead of /dashboard', async () => {
    const mockPost = vi.mocked(apiClient.post);
    mockPost
      .mockResolvedValueOnce({ data: setupPayload })
      .mockResolvedValueOnce({ data: { message: 'ok' } });

    render(
      <MemoryRouter
        initialEntries={[
          { pathname: '/2fa-setup', state: { from: '/join/ABC123/team/find' } },
        ]}
      >
        <AuthProvider>
          <Routes>
            <Route path="/2fa-setup" element={<TwoFactorSetup />} />
            <Route
              path="/join/:code/team/find"
              element={<div>landed-on-find-team</div>}
            />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );
    const user = userEvent.setup();

    await waitFor(() => {
      expect(screen.getByText(/JBSWY3DPEHPK3PXP/)).toBeInTheDocument();
    });

    await user.type(screen.getByLabelText(/6-stelliger Verifizierungscode/i), '123456');
    await user.click(screen.getByRole('button', { name: /verifizieren und aktivieren/i }));

    await waitFor(() => {
      setupPayload.recovery_codes.forEach((code) => {
        expect(screen.getByText(code)).toBeInTheDocument();
      });
    });

    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: /fertig/i }));

    await waitFor(() => {
      expect(screen.getByText('landed-on-find-team')).toBeInTheDocument();
    });
  });

  it('has no axe accessibility violations on initial render', async () => {
    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockResolvedValueOnce({ data: setupPayload });

    const { container } = renderPage();
    await waitFor(() => expect(screen.getByText(/JBSWY3DPEHPK3PXP/)).toBeInTheDocument());
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
