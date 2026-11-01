import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import TwoFactorVerify from './TwoFactorVerify';
import { AuthContext } from '../auth/AuthContext';
import { ApiError } from '../api/client';
import { verify2faCode } from '../api/auth';

vi.mock('../api/auth', () => ({ verify2faCode: vi.fn() }));

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={['/2fa-verify']}>
      <AuthContext.Provider
        value={{
          user: null,
          isAuthenticated: false,
          isLoading: false,
          login: vi.fn(),
          logout: vi.fn(),
          checkAuth: vi.fn(),
          needsTwoFactorSetup: () => false,
        }}
      >
        <TwoFactorVerify />
      </AuthContext.Provider>
    </MemoryRouter>,
  );

/**
 * The second sign-in step words its errors in German and never shows the API's
 * English text, such as "Invalid TOTP code".
 */
describe('<TwoFactorVerify> errors', () => {
  beforeEach(() => {
    vi.mocked(verify2faCode).mockReset();
    sessionStorage.setItem('clavis_2fa_pending_email', 'admin@example.com');
  });

  it('words a wrong code in German', async () => {
    vi.mocked(verify2faCode).mockRejectedValueOnce(new ApiError(401, 'Invalid TOTP code'));
    renderPage();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText(/6-stelliger Code/i), '123456');
    await user.click(screen.getByRole('button', { name: 'Verifizieren' }));

    expect(await screen.findByText(/Der Code ist falsch oder abgelaufen/)).toBeInTheDocument();
    expect(screen.queryByText('Invalid TOTP code')).not.toBeInTheDocument();
  });

  it('words an archived organization like the sign-in', async () => {
    vi.mocked(verify2faCode).mockRejectedValueOnce(
      new ApiError(403, 'This organization is archived.', 'ORG_ARCHIVED'),
    );
    renderPage();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText(/6-stelliger Code/i), '123456');
    await user.click(screen.getByRole('button', { name: 'Verifizieren' }));

    expect(await screen.findByText(/Diese Organisation ist archiviert/)).toBeInTheDocument();
  });
});
