import { afterEach, describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { ThemeProvider } from '@mui/material/styles';
import theme from '../theme';
import OnboardingShell from './OnboardingShell';
import OrganizationPicker from '../pages/OrganizationPicker';
import { WizardStateProvider } from '../wizard/WizardStateContext';
import { AuthContext } from '../auth/AuthContext';
import {
  __resetOperatorNoticeForTests,
  type OperatorNotice as OperatorNoticeData,
} from '../config/operatorNotice';

vi.mock('../services/publicOrganizations', () => ({ listAll: vi.fn().mockResolvedValue([]) }));

const wrap = (ui: React.ReactElement) => (
  <MemoryRouter>
    <ThemeProvider theme={theme}>{ui}</ThemeProvider>
  </MemoryRouter>
);

// The operator notice stands directly above the legal links, outside main
// (spec 095, C9).
const operatorNotice: OperatorNoticeData = {
  version: 1,
  logos: [{ src: 'programme.svg', alt: { de: 'Logo des Programms' }, height: 48 }],
  text: { de: 'Dieses Angebot wird gefoerdert.' },
  links: [{ label: 'example.org/programme', url: 'https://example.org/programme' }],
};

function expectNoticeAboveLegalLinks() {
  const region = screen.getByRole('region', { name: 'Hinweis des Betreibers' });
  const nav = screen.getByRole('navigation', { name: 'Rechtliches' });
  // Same parent, the notice right before the row of links.
  expect(region.nextElementSibling).toBe(nav);
  expect(nav.compareDocumentPosition(region) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  expect(screen.getByRole('main')).not.toContainElement(region);
  return region;
}

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

describe('<OnboardingShell> operator notice', () => {
  afterEach(() => {
    __resetOperatorNoticeForTests(null);
  });

  it('shows the notice in the footer, after the progress bar and above the legal links', () => {
    __resetOperatorNoticeForTests(operatorNotice);
    render(
      wrap(
        <OnboardingShell progress={<div data-testid="progress-slot">bar</div>}>
          <h1>Anmelden</h1>
        </OnboardingShell>,
      ),
    );
    const region = expectNoticeAboveLegalLinks();
    expect(screen.getByRole('contentinfo')).toContainElement(region);
    expect(
      screen.getByTestId('progress-slot').compareDocumentPosition(region) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it('shows no notice region without a notice', () => {
    render(
      wrap(
        <OnboardingShell>
          <h1>Anmelden</h1>
        </OnboardingShell>,
      ),
    );
    expect(screen.queryByRole('region', { name: 'Hinweis des Betreibers' })).toBeNull();
  });

  // C10: with fillViewport a tall notice makes main smaller. jsdom cannot
  // measure, so this pins the structure that keeps main shrinkable and the
  // page's one scrolling child scrolling.
  it('keeps main shrinkable under a tall notice with fillViewport', () => {
    __resetOperatorNoticeForTests({
      ...operatorNotice,
      logos: [
        { src: 'programme.svg', alt: { de: 'Logo des Programms' }, height: 48 },
        { src: 'partner.svg', alt: { de: 'Logo des Partners' }, height: 48 },
      ],
      text: { de: 'x'.repeat(200) },
    });
    render(
      wrap(
        <OnboardingShell fillViewport progress={<div data-testid="progress-slot">bar</div>}>
          <h1>Organisation</h1>
          <div data-testid="scroller" style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
            <div style={{ height: 3000 }}>tall list</div>
          </div>
          <button type="button">Weiter</button>
        </OnboardingShell>,
      ),
    );
    const footer = screen.getByRole('contentinfo');
    const region = screen.getByRole('region', { name: 'Hinweis des Betreibers' });
    expect(footer).toContainElement(region);
    const main = screen.getByRole('main');
    expect(main).not.toContainElement(region);
    const mainStyle = getComputedStyle(main);
    expect(mainStyle.minHeight).toBe('0px');
    expect(mainStyle.overflow).toBe('hidden');
    // The footer never grows past its cap, so main keeps its share of the
    // viewport, and the footer scrolls its own content when the notice is tall.
    const footerStyle = getComputedStyle(footer);
    expect(footerStyle.flexShrink).toBe('0');
    expect(footerStyle.maxHeight).toBe('50%');
    expect(footerStyle.overflowY).toBe('auto');
    expect(main).toContainElement(screen.getByTestId('scroller'));
    expect(main).toContainElement(screen.getByRole('button', { name: 'Weiter' }));
  });
});
