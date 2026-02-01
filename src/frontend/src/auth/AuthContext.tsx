import React, { createContext, useState, useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import apiClient, { ApiError, isUnderInstance } from '../api/client';
import i18n from '../i18n';
import { loginErrorMessage } from './loginErrors';
import { clearApiCache } from '../utils/apiCache';
import type { LoginResponse } from '../api/types';

interface User {
  id: string;
  // Null for a guest, as in the backend's CurrentUserResponse.
  email: string | null;
  display_name: string;
  role: 'guest' | 'participant' | 'coordinator' | 'creator' | 'org_admin';
  organization_id: string;
  organization_slug: string;
}

type LoginStatus = 'success' | '2fa_required' | '2fa_setup_required';

interface LoginResult {
  status?: LoginStatus;
  setupToken?: string;
  error?: string;
}

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (email: string, password: string, totpCode?: string) => Promise<LoginResult>;
  logout: () => Promise<void>;
  checkAuth: () => Promise<void>;
  needsTwoFactorSetup: () => boolean;
}

const SETUP_TOKEN_KEY = 'clavis_2fa_setup_token';

// eslint-disable-next-line react-refresh/only-export-components
export const AuthContext = createContext<AuthContextType | undefined>(undefined);

interface AuthProviderProps {
  children: ReactNode;
}

export const AuthProvider: React.FC<AuthProviderProps> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  // Who the context held at the last probe: undefined before the first one,
  // null for nobody, else the user id. checkAuth compares against it to see
  // a change of account.
  const lastUserId = useRef<string | null | undefined>(undefined);

  const checkAuth = async () => {
    try {
      const response = await apiClient.get('/api/auth/me');
      // Another account than before, or a user after a state without one,
      // must not inherit the /api/ cache of the previous session. The second
      // factor steps end in checkAuth, not in login, so a sign-in can finish
      // here. Clear the cache before the new user is exposed. The first probe
      // and the same user on a reload keep it: a participant who reloads
      // offline still sees stations and tasks.
      if (lastUserId.current !== undefined && lastUserId.current !== response.data.id) {
        await clearApiCache();
      }
      lastUserId.current = response.data.id;
      setUser(response.data);
    } catch (error) {
      // A 401 says the session is gone, so its cached /api/ answers go with
      // it. A failure without an answer only means offline. Then the cache
      // keeps serving stations and tasks.
      if (error instanceof ApiError && error.status === 401) {
        await clearApiCache();
        lastUserId.current = null;
      } else if (lastUserId.current === undefined) {
        // The first probe got no answer: nobody is known to be signed in, so
        // a user who appears later has signed in. After a probe that knew a
        // user, an offline failure keeps that user as the one to compare to.
        lastUserId.current = null;
      }
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    // The operator console has its own session and never an organization
    // one. Asking for the organization user there would answer 403 on every
    // console page and would fill the browser console with an error.
    if (isUnderInstance(window.location.pathname)) {
      setIsLoading(false);
      return;
    }
    checkAuth();
  }, []);

  const login = async (email: string, password: string, totpCode?: string): Promise<LoginResult> => {
    try {
      const payload: { email: string; password: string; totp_code?: string } = {
        email,
        password,
      };

      if (totpCode) {
        payload.totp_code = totpCode;
      }

      const { data } = await apiClient.post<LoginResponse>('/api/auth/login', payload);

      if (data.status === '2fa_required') {
        return { status: '2fa_required' };
      }

      if (data.status === '2fa_setup_required') {
        if (data.setup_token) {
          sessionStorage.setItem(SETUP_TOKEN_KEY, data.setup_token);
        }
        return { status: '2fa_setup_required', setupToken: data.setup_token };
      }

      // The full user, `organization_slug` included, comes from /auth/me only.
      if (data.status === 'success') {
        // A new session begins, possibly as another account. The /api/
        // cache of the previous one must not survive it.
        await clearApiCache();
        await checkAuth();
      }
      return { status: 'success' };
    } catch (error) {
      // Never the API's English text: participants read this on a phone.
      return { error: loginErrorMessage(error, i18n.t, i18n.language) };
    }
  };

  const logout = async () => {
    try {
      await apiClient.post('/api/auth/logout');
    } catch (error) {
      console.error('Logout error:', error);
    } finally {
      sessionStorage.removeItem(SETUP_TOKEN_KEY);
      // The session's cached /api/ answers end with it, also when the
      // server call itself failed.
      await clearApiCache();
      setUser(null);
    }
  };

  const needsTwoFactorSetup = () => sessionStorage.getItem(SETUP_TOKEN_KEY) !== null;

  const value: AuthContextType = {
    user,
    isAuthenticated: !!user,
    isLoading,
    login,
    logout,
    checkAuth,
    needsTwoFactorSetup,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};
