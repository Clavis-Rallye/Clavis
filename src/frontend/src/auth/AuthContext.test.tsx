import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { axe } from 'vitest-axe';
import { AuthProvider, AuthContext } from './AuthContext';
import apiClient, { ApiError } from '../api/client';
import { useContext } from 'react';

vi.mock('../api/client');

const TestComponent = () => {
  const auth = useContext(AuthContext);

  if (!auth) {
    return <div>No auth context</div>;
  }

  return (
    <div>
      <div data-testid="isAuthenticated">{auth.isAuthenticated.toString()}</div>
      <div data-testid="isLoading">{auth.isLoading.toString()}</div>
      {auth.user && (
        <>
          <div data-testid="user-email">{auth.user.email}</div>
          <div data-testid="user-role">{auth.user.role}</div>
        </>
      )}
      <button onClick={() => auth.login('test@example.com', 'password')}>Login</button>
      <button onClick={() => auth.logout()}>Logout</button>
      <button onClick={() => auth.checkAuth()}>Check</button>
    </div>
  );
};

// jsdom has no Cache Storage API: the stub stands in for the browser's
// `caches` global so the tests can see the worker's /api/ cache being
// deleted.
function stubCaches(): ReturnType<typeof vi.fn> {
  const deleteCache = vi.fn().mockResolvedValue(true);
  vi.stubGlobal('caches', { delete: deleteCache });
  return deleteCache;
}

// An ApiError with a status, the way the response interceptor hands it to
// the context. The automocked class ignores constructor arguments, so the
// status is set on the instance.
function apiErrorWithStatus(status: number): ApiError {
  const error = new ApiError(0, '');
  (error as { status: number }).status = status;
  return error;
}

describe('AuthContext', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it('provides auth state to children', async () => {
    const mockGet = vi.mocked(apiClient.get);
    mockGet.mockRejectedValueOnce(new Error('Not authenticated'));

    render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('isAuthenticated')).toHaveTextContent('false');
      expect(screen.getByTestId('isLoading')).toHaveTextContent('false');
    });
  });

  it('checks authentication on mount', async () => {
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

    render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith('/api/auth/me');
      expect(screen.getByTestId('isAuthenticated')).toHaveTextContent('true');
      expect(screen.getByTestId('user-email')).toHaveTextContent('test@example.com');
      expect(screen.getByTestId('user-role')).toHaveTextContent('participant');
    });
  });

  it('updates state on successful login', async () => {
    const mockGet = vi.mocked(apiClient.get);
    mockGet.mockRejectedValueOnce(new Error('Not authenticated'));
    // After login success, AuthContext calls checkAuth() → second /api/auth/me
    mockGet.mockResolvedValueOnce({
      data: {
        id: '123',
        email: 'test@example.com',
        display_name: 'Test User',
        role: 'participant',
        organization_id: 'org-123',
        organization_slug: 'test-org',
      },
    });

    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockResolvedValueOnce({
      data: {
        status: 'success',
        user_id: '123',
        email: 'test@example.com',
        display_name: 'Test User',
        role: 'participant',
        organization_id: 'org-123',
        message: 'Login successful',
      },
    });

    render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('isLoading')).toHaveTextContent('false');
    });

    const loginButton = screen.getByText('Login');
    loginButton.click();

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith('/api/auth/login', {
        email: 'test@example.com',
        password: 'password',
      });
      expect(screen.getByTestId('isAuthenticated')).toHaveTextContent('true');
      expect(screen.getByTestId('user-email')).toHaveTextContent('test@example.com');
    });
  });

  it('handles login failure', async () => {
    const mockGet = vi.mocked(apiClient.get);
    mockGet.mockRejectedValueOnce(new Error('Not authenticated'));

    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockRejectedValueOnce(new Error('Invalid credentials'));

    render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('isLoading')).toHaveTextContent('false');
    });

    const loginButton = screen.getByText('Login');
    loginButton.click();

    await waitFor(() => {
      expect(screen.getByTestId('isAuthenticated')).toHaveTextContent('false');
    });
  });

  it('returns 2FA required status', async () => {
    const mockGet = vi.mocked(apiClient.get);
    mockGet.mockRejectedValueOnce(new Error('Not authenticated'));

    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockResolvedValueOnce({
      data: {
        status: '2fa_required',
      },
    });

    render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith('/api/auth/me');
    });

    const loginButton = screen.getByText('Login');
    loginButton.click();

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith('/api/auth/login', {
        email: 'test@example.com',
        password: 'password',
      });
    });
  });

  it('clears user on logout', async () => {
    const mockGet = vi.mocked(apiClient.get);
    mockGet.mockResolvedValue({
      data: {
        id: '123',
        email: 'test@example.com',
        display_name: 'Test User',
        role: 'participant',
        organization_id: 'org-123',
      },
    });

    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockResolvedValue({});

    render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('isAuthenticated')).toHaveTextContent('true');
    }, { timeout: 3000 });

    const logoutButton = screen.getByText('Logout');
    logoutButton.click();

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith('/api/auth/logout');
      expect(screen.getByTestId('isAuthenticated')).toHaveTextContent('false');
    }, { timeout: 3000 });
  });

  it('includes TOTP code in login request when provided', async () => {
    const mockGet = vi.mocked(apiClient.get);
    mockGet.mockRejectedValueOnce(new Error('Not authenticated'));

    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockResolvedValueOnce({
      data: {
        status: 'success',
        user_id: '123',
        email: 'test@example.com',
        display_name: 'Test User',
        role: 'participant',
        organization_id: 'org-123',
        message: 'Login successful',
      },
    });

    const TestComponentWithTOTP = () => {
      const auth = useContext(AuthContext);

      return (
        <div>
          <button onClick={() => auth!.login('test@example.com', 'password', '123456')}>
            Login with TOTP
          </button>
        </div>
      );
    };

    render(
      <AuthProvider>
        <TestComponentWithTOTP />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith('/api/auth/me');
    });

    const loginButton = screen.getByText('Login with TOTP');
    loginButton.click();

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith('/api/auth/login', {
        email: 'test@example.com',
        password: 'password',
        totp_code: '123456',
      });
    });
  });

  it('stores setup_token in sessionStorage when 2fa_setup_required', async () => {
    const mockGet = vi.mocked(apiClient.get);
    mockGet.mockRejectedValueOnce(new Error('Not authenticated'));

    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockResolvedValueOnce({
      data: { status: '2fa_setup_required', setup_token: 'setup-xyz', message: '' },
    });

    sessionStorage.clear();

    const TestWithCheck = () => {
      const auth = useContext(AuthContext);
      return (
        <div>
          <div data-testid="needs-setup">{auth!.needsTwoFactorSetup().toString()}</div>
          <button onClick={async () => {
            await auth!.login('admin@example.com', 'password');
          }}>Login</button>
        </div>
      );
    };

    render(
      <AuthProvider>
        <TestWithCheck />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('needs-setup')).toHaveTextContent('false');
    });

    screen.getByText('Login').click();

    await waitFor(() => {
      expect(sessionStorage.getItem('clavis_2fa_setup_token')).toBe('setup-xyz');
    });
  });

  it('clears setup_token on logout', async () => {
    sessionStorage.setItem('clavis_2fa_setup_token', 'leftover');

    const mockGet = vi.mocked(apiClient.get);
    mockGet.mockResolvedValue({
      data: {
        id: '1',
        email: 'x@example.com',
        display_name: 'X',
        role: 'org_admin',
        organization_id: 'org-1',
      },
    });

    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockResolvedValue({});

    render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('isAuthenticated')).toHaveTextContent('true');
    }, { timeout: 3000 });

    screen.getByText('Logout').click();

    await waitFor(() => {
      expect(sessionStorage.getItem('clavis_2fa_setup_token')).toBeNull();
    });
  });

  it('has no axe accessibility violations on initial render', async () => {
    const mockGet = vi.mocked(apiClient.get);
    mockGet.mockRejectedValueOnce(new Error('Not authenticated'));

    const { container } = render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>
    );
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('handles logout errors gracefully', async () => {
    const mockGet = vi.mocked(apiClient.get);
    mockGet.mockResolvedValue({
      data: {
        id: '123',
        email: 'test@example.com',
        display_name: 'Test User',
        role: 'participant',
        organization_id: 'org-123',
      },
    });

    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockRejectedValue(new Error('Logout failed'));

    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('isAuthenticated')).toHaveTextContent('true');
    }, { timeout: 3000 });

    const logoutButton = screen.getByText('Logout');
    logoutButton.click();

    await waitFor(() => {
      expect(screen.getByTestId('isAuthenticated')).toHaveTextContent('false');
      expect(consoleErrorSpy).toHaveBeenCalled();
    }, { timeout: 3000 });

    consoleErrorSpy.mockRestore();
  });

  // Logout, a login as another account and the end of a session clear the
  // service worker's /api/ cache. A failure without an answer only means offline
  // and keeps it.
  it('clears the api-cache on logout', async () => {
    const deleteCache = stubCaches();
    const mockGet = vi.mocked(apiClient.get);
    mockGet.mockResolvedValue({
      data: {
        id: '123',
        email: 'test@example.com',
        display_name: 'Test User',
        role: 'participant',
        organization_id: 'org-123',
      },
    });

    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockResolvedValue({});

    render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('isAuthenticated')).toHaveTextContent('true');
    }, { timeout: 3000 });

    screen.getByText('Logout').click();

    await waitFor(() => {
      expect(deleteCache).toHaveBeenCalledWith('api-cache');
    });
  });

  it('clears the api-cache when a login as another account succeeds', async () => {
    const deleteCache = stubCaches();
    const mockGet = vi.mocked(apiClient.get);
    // The mount probe: nobody is signed in yet.
    mockGet.mockRejectedValueOnce(new Error('Not authenticated'));
    // The /auth/me fetch after the login: the new account.
    mockGet.mockResolvedValueOnce({
      data: {
        id: '456',
        email: 'other@example.com',
        display_name: 'Other User',
        role: 'org_admin',
        organization_id: 'org-123',
        organization_slug: 'test-org',
      },
    });

    const mockPost = vi.mocked(apiClient.post);
    mockPost.mockResolvedValueOnce({
      data: {
        status: 'success',
        user_id: '456',
        email: 'other@example.com',
        display_name: 'Other User',
        role: 'org_admin',
        organization_id: 'org-123',
        message: 'Login successful',
      },
    });

    render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('isLoading')).toHaveTextContent('false');
    });

    screen.getByText('Login').click();

    await waitFor(() => {
      expect(deleteCache).toHaveBeenCalledWith('api-cache');
      expect(screen.getByTestId('user-email')).toHaveTextContent('other@example.com');
    });
  });

  it('clears the api-cache when the session has ended (401 on the auth probe)', async () => {
    const deleteCache = stubCaches();
    vi.mocked(apiClient.get).mockRejectedValueOnce(apiErrorWithStatus(401));

    render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('isAuthenticated')).toHaveTextContent('false');
      expect(deleteCache).toHaveBeenCalledWith('api-cache');
    });
  });

  it('keeps the api-cache when the auth probe fails without an answer (offline)', async () => {
    const deleteCache = stubCaches();
    // The interceptor reports a dead connection as status 0: offline, not
    // the end of a session. The cache keeps serving stations and tasks.
    vi.mocked(apiClient.get).mockRejectedValueOnce(apiErrorWithStatus(0));

    render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('isAuthenticated')).toHaveTextContent('false');
    });

    expect(deleteCache).not.toHaveBeenCalled();
  });
  // A sign-in with a second factor ends in TwoFactorVerify, which
  // calls checkAuth, not login(). The change of account is handled there.
  const userOf = (id: string) => ({
    data: {
      id,
      email: `${id}@example.com`,
      display_name: `User ${id}`,
      role: 'org_admin',
      organization_id: 'org-123',
      organization_slug: 'test-org',
    },
  });

  it('clears the api-cache before another account is exposed by checkAuth (second factor)', async () => {
    const deleteCache = stubCaches();
    const order: string[] = [];
    deleteCache.mockImplementation(async () => {
      order.push('cleared');
      return true;
    });
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(userOf('A'))
      .mockResolvedValueOnce(userOf('B'));

    render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>
    );
    await waitFor(() => {
      expect(screen.getByTestId('user-email')).toHaveTextContent('A@example.com');
    });
    expect(deleteCache).not.toHaveBeenCalled();

    screen.getByText('Check').click();

    await waitFor(() => {
      expect(screen.getByTestId('user-email')).toHaveTextContent('B@example.com');
    });
    expect(deleteCache).toHaveBeenCalledWith('api-cache');
    expect(order).toEqual(['cleared']);
  });

  it('clears the api-cache when a user appears through checkAuth after a state without one', async () => {
    const deleteCache = stubCaches();
    vi.mocked(apiClient.get)
      .mockRejectedValueOnce(apiErrorWithStatus(0))
      .mockResolvedValueOnce(userOf('B'));

    render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>
    );
    await waitFor(() => {
      expect(screen.getByTestId('isLoading')).toHaveTextContent('false');
    });
    expect(deleteCache).not.toHaveBeenCalled();

    screen.getByText('Check').click();

    await waitFor(() => {
      expect(screen.getByTestId('user-email')).toHaveTextContent('B@example.com');
    });
    expect(deleteCache).toHaveBeenCalledWith('api-cache');
  });

  it('keeps the api-cache when checkAuth finds the same user again (reload)', async () => {
    const deleteCache = stubCaches();
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(userOf('A'))
      .mockResolvedValueOnce(userOf('A'));

    render(
      <AuthProvider>
        <TestComponent />
      </AuthProvider>
    );
    await waitFor(() => {
      expect(screen.getByTestId('user-email')).toHaveTextContent('A@example.com');
    });

    screen.getByText('Check').click();
    await waitFor(() => {
      expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(2);
    });
    await waitFor(() => {
      expect(screen.getByTestId('user-email')).toHaveTextContent('A@example.com');
    });

    expect(deleteCache).not.toHaveBeenCalled();
  });
});
