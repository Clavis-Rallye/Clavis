/**
 * With a join code in hand, the entry step offers the guest way first.
 * Without a code there is no guest way.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router';
import EntryChoiceStep from './EntryChoiceStep';
import { WizardStateProvider } from '../wizard/WizardStateContext';
import { AuthContext } from '../auth/AuthContext';

const anonymous = {
  user: null,
  isAuthenticated: false,
  isLoading: false,
  login: vi.fn(),
  logout: vi.fn(),
  checkAuth: vi.fn(),
  needsTwoFactorSetup: () => false,
};

const Where = () => {
  const location = useLocation();
  return <div data-testid="where">{location.pathname + location.search}</div>;
};

const renderAt = (path: string) =>
  render(
    <AuthContext.Provider value={anonymous}>
    <WizardStateProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/auth-choice" element={<EntryChoiceStep />} />
          <Route path="/join/:code/auth-choice" element={<EntryChoiceStep />} />
          <Route path="*" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </WizardStateProvider>
    </AuthContext.Provider>,
  );

describe('<EntryChoiceStep>', () => {
  beforeEach(() => sessionStorage.clear());

  it('offers the guest way first on a join link and opens the profile step in guest mode', () => {
    renderAt('/join/ABC123/auth-choice');

    const buttons = screen.getAllByRole('button').map((b) => b.textContent);
    expect(buttons).toEqual(['Als Gast mitmachen', 'Neues Konto erstellen', 'Mit Konto anmelden']);

    fireEvent.click(screen.getByRole('button', { name: 'Als Gast mitmachen' }));
    expect(screen.getByTestId('where')).toHaveTextContent('/join/ABC123/profile');
    expect(screen.getByTestId('where')).not.toHaveTextContent('account=1');
  });

  it('opens the profile step in account mode from "Neues Konto erstellen"', () => {
    renderAt('/join/ABC123/auth-choice');

    fireEvent.click(screen.getByRole('button', { name: 'Neues Konto erstellen' }));
    expect(screen.getByTestId('where')).toHaveTextContent('/join/ABC123/profile?account=1');
  });

  it('offers no guest way without a join code', () => {
    renderAt('/auth-choice');

    expect(screen.queryByRole('button', { name: 'Als Gast mitmachen' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Neues Konto erstellen' }));
    expect(screen.getByTestId('where')).toHaveTextContent('/register');
  });
});
