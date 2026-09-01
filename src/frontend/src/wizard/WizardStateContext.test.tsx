/**
 * The join code (runCode) and the path it arrived on
 * (pathKind) must survive a reload: a participant who reloads mid-flow, or
 * who bounces through 2FA setup (a full remount of the wizard provider),
 * must land back in the join flow instead of falling back to /dashboard.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { WizardStateProvider } from './WizardStateContext';
import { useWizardState } from './useWizardState';
import { AuthContext } from '../auth/AuthContext';

const authValue = {
  user: null,
  isAuthenticated: false,
  isLoading: false,
  login: async () => ({}),
  logout: async () => {},
  checkAuth: async () => {},
  needsTwoFactorSetup: () => false,
};

const Probe = () => {
  const wizard = useWizardState();
  return (
    <div>
      <span data-testid="run-code">{wizard.runCode ?? ''}</span>
      <span data-testid="path-kind">{wizard.pathKind}</span>
      <button onClick={() => wizard.setRunCode('ABC123')}>set-run-code</button>
      <button onClick={() => wizard.setPathKind('with-code')}>
        set-path-kind
      </button>
    </div>
  );
};

const tree = () => (
  <AuthContext.Provider value={authValue}>
    <WizardStateProvider>
      <Probe />
    </WizardStateProvider>
  </AuthContext.Provider>
);

describe('<WizardStateProvider> runCode + pathKind persistence', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('restores runCode and pathKind after a remount', async () => {
    const user = userEvent.setup();
    const view = render(tree());

    await user.click(screen.getByRole('button', { name: 'set-run-code' }));
    await user.click(screen.getByRole('button', { name: 'set-path-kind' }));

    expect(screen.getByTestId('run-code')).toHaveTextContent('ABC123');
    expect(screen.getByTestId('path-kind')).toHaveTextContent('with-code');

    // Simulates the 2FA hop / a reload — a fresh provider instance reads
    // sessionStorage on mount instead of carrying React state over.
    view.unmount();
    render(tree());

    expect(screen.getByTestId('run-code')).toHaveTextContent('ABC123');
    expect(screen.getByTestId('path-kind')).toHaveTextContent('with-code');
  });

  it('starts clean when sessionStorage carries no join code', () => {
    render(tree());

    expect(screen.getByTestId('run-code')).toHaveTextContent('');
    expect(screen.getByTestId('path-kind')).toHaveTextContent('no-code');
  });
});
