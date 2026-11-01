/**
 * "Abmelden" asks a guest first, because a guest has
 * no way back into the team. An account user is signed out at once.
 */
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import ParticipantProfileSheet from './ParticipantProfileSheet';
import { AuthContext } from '../auth/AuthContext';

const renderSheet = (role: 'guest' | 'participant') => {
  const logout = vi.fn().mockResolvedValue(undefined);
  const onClose = vi.fn();
  render(
    <MemoryRouter>
      <AuthContext.Provider
        value={{
          user: {
            id: 'u1',
            email: role === 'guest' ? null : 'p@example.com',
            display_name: 'Ada',
            role,
            organization_id: 'org-1',
            organization_slug: 'test-org',
          } as never,
          isAuthenticated: true,
          isLoading: false,
          login: vi.fn(),
          logout,
          checkAuth: vi.fn(),
          needsTwoFactorSetup: () => false,
        }}
      >
        <ParticipantProfileSheet open onClose={onClose} />
      </AuthContext.Provider>
    </MemoryRouter>,
  );
  return { logout, onClose };
};

describe('ParticipantProfileSheet sign-out', () => {
  it('asks a guest first and does not sign out', async () => {
    const { logout } = renderSheet('guest');

    fireEvent.click(screen.getByRole('button', { name: 'Abmelden' }));

    const dialog = await screen.findByRole('dialog', { name: 'Als Gast abmelden?' });
    expect(dialog).toHaveTextContent('an diesen Browser gebunden');
    expect(logout).not.toHaveBeenCalled();
  });

  it('cancel keeps the guest signed in', async () => {
    const { logout, onClose } = renderSheet('guest');

    fireEvent.click(screen.getByRole('button', { name: 'Abmelden' }));
    const dialog = await screen.findByRole('dialog', { name: 'Als Gast abmelden?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Abbrechen' }));

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Als Gast abmelden?' })).toBeNull());
    expect(logout).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('confirm signs the guest out', async () => {
    const { logout } = renderSheet('guest');

    fireEvent.click(screen.getByRole('button', { name: 'Abmelden' }));
    const dialog = await screen.findByRole('dialog', { name: 'Als Gast abmelden?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Abmelden' }));

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
  });

  it('signs an account user out at once, without a dialog', async () => {
    const { logout } = renderSheet('participant');

    fireEvent.click(screen.getByRole('button', { name: 'Abmelden' }));

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('dialog', { name: 'Als Gast abmelden?' })).toBeNull();
  });
});

// The sheet is a dialog and takes its name from its heading.
describe('ParticipantProfileSheet name', () => {
  it('is a dialog named by its visible heading', () => {
    renderSheet('participant');

    const heading = screen.getByRole('heading', { level: 2 });
    const dialog = screen.getByRole('dialog', { name: heading.textContent ?? '' });
    expect(heading.textContent).not.toBe('');
    expect(dialog).toHaveAttribute('aria-labelledby', heading.id);
    expect(dialog).toContainElement(heading);
  });
});
