import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router';
import ProtectedRoute from './ProtectedRoute';
import { AuthContext } from './AuthContext';
import { navigation } from '../api/client';

const renderGuarded = () =>
  render(
    <MemoryRouter initialEntries={['/dashboard']}>
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
        <Routes>
          <Route
            path="/dashboard"
            element={
              <ProtectedRoute>
                <div>Dashboard</div>
              </ProtectedRoute>
            }
          />
          <Route path="/login" element={<div>Login page</div>} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('ProtectedRoute', () => {
  afterEach(() => {
    navigation.pending = false;
  });

  it('sends a visitor without a session to the login', () => {
    renderGuarded();
    expect(screen.getByText('Login page')).toBeInTheDocument();
  });

  // The API client has already started the navigation to /login?reason=account_disabled.
  // The guard's own bare redirect sometimes won the race, so the reason must not be lost.
  it('waits for a navigation the API client already started', () => {
    navigation.pending = true;
    renderGuarded();
    expect(screen.queryByText('Login page')).not.toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });
});
