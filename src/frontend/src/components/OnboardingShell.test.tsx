import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { ThemeProvider } from '@mui/material/styles';
import theme from '../theme';
import OnboardingShell from './OnboardingShell';
import OrganizationPicker from '../pages/OrganizationPicker';
import { WizardStateProvider } from '../wizard/WizardStateContext';
import { AuthContext } from '../auth/AuthContext';

vi.mock('../services/publicOrganizations', () => ({ listAll: vi.fn().mockResolvedValue([]) }));

const wrap = (ui: React.ReactElement) => (
  <MemoryRouter>
    <ThemeProvider theme={theme}>{ui}</ThemeProvider>
  </MemoryRouter>
);

describe('<OnboardingShell>', () => {
  it('renders children inside the main landmark', () => {
    render(
      wrap(
        <OnboardingShell>
          <p data-testid="content">hello</p>
        </OnboardingShell>,
      ),
    );
    const main = screen.getByRole('main');
    expect(main).toContainElement(screen.getByTestId('content'));
  });

  it('omits the progress slot when none is provided', () => {
    render(
      wrap(
        <OnboardingShell>
          <p>body</p>
        </OnboardingShell>,
      ),
    );
    expect(screen.queryByTestId('progress-slot')).toBeNull();
  });

  it('renders the progress slot when provided', () => {
    render(
      wrap(
        <OnboardingShell
          progress={<div data-testid="progress-slot">bar</div>}
        >
          <p>body</p>
        </OnboardingShell>,
      ),
    );
    expect(screen.getByTestId('progress-slot')).toBeInTheDocument();
  });

  // Every entry page links imprint, privacy and the accessibility statement,
  // outside main so the route focus still lands on the page heading.
  it('links the three legal pages outside the main landmark', () => {
    render(
      wrap(
        <OnboardingShell>
          <h1>Anmelden</h1>
        </OnboardingShell>,
      ),
    );
    const nav = screen.getByRole('navigation', { name: 'Rechtliches' });
    expect(screen.getByRole('main')).not.toContainElement(nav);
    expect(within(nav).getByRole('link', { name: 'Impressum' })).toHaveAttribute('href', '/legal/impressum');
    expect(within(nav).getByRole('link', { name: 'Datenschutz' })).toHaveAttribute('href', '/legal/privacy');
    expect(within(nav).getByRole('link', { name: 'Barrierefreiheit' })).toHaveAttribute(
      'href',
      '/legal/accessibility',
    );
  });

  // Checked with one real entry page: the brand mark
  // comes from the page's children and stays inside main, while the progress
  // bar (and the legal links) stand outside it, in a footer landmark,
  // so no content of the shell sits outside every landmark.
  it('wraps what stands outside main in a footer landmark (real page)', () => {
    render(
      wrap(
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
          <WizardStateProvider>
            <OrganizationPicker onSelect={() => {}} />
          </WizardStateProvider>
        </AuthContext.Provider>,
      ),
    );
    const footer = screen.getByRole('contentinfo');
    expect(footer).toContainElement(screen.getByRole('progressbar'));
    expect(footer).toContainElement(screen.getByRole('navigation', { name: 'Rechtliches' }));
    expect(screen.getByRole('main')).not.toContainElement(screen.getByRole('progressbar'));
  });
});
