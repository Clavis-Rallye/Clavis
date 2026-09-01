import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import LegalShell from './LegalShell';
import { AuthContext } from '../../auth/AuthContext';
import { WizardStateProvider } from '../../wizard/WizardStateContext';
import { fetchLegalText, type PublicLegalText } from '../../services/publicLegal';
import { __resetInstanceConfigForTests } from '../../config/instanceConfig';
import deLegal from '../../i18n/locales/de/legal.json';
import enLegal from '../../i18n/locales/en/legal.json';

vi.mock('../../services/publicLegal', () => ({ fetchLegalText: vi.fn() }));

const mockFetchLegalText = vi.mocked(fetchLegalText);

type AuthUser = {
  id: string;
  email: string | null;
  display_name: string;
  role: 'guest' | 'participant' | 'coordinator' | 'creator' | 'org_admin';
  organization_id: string;
  organization_slug: string;
};

const authedUser: AuthUser = {
  id: 'u1',
  email: 'participant@example.com',
  display_name: 'Participant',
  role: 'participant',
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const renderShell = (user: AuthUser | null, isLoading: boolean) =>
  render(
    <MemoryRouter>
      <AuthContext.Provider
        value={{
          user,
          isAuthenticated: !!user,
          isLoading,
          login: vi.fn(),
          logout: vi.fn(),
          checkAuth: vi.fn(),
          needsTwoFactorSetup: () => false,
        }}
      >
        <WizardStateProvider>
          <LegalShell title="Datenschutz" slug="privacy">
            <p>Bundled placeholder copy</p>
          </LegalShell>
        </WizardStateProvider>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('LegalShell', () => {
  beforeEach(() => {
    mockFetchLegalText.mockReset();
    __resetInstanceConfigForTests();
    sessionStorage.clear();
  });

  afterEach(() => {
    sessionStorage.clear();
  });

  // The logo row is a header landmark, not part of the main card, so no text
  // stands outside a landmark. The page keeps one main.
  it('puts the logo row into a header landmark, with one main on the page', () => {
    renderShell(null, true);

    const header = screen.getByRole('banner');
    expect(header).toHaveTextContent('Clavis');
    expect(screen.getAllByRole('main')).toHaveLength(1);
  });

  it('shows no placeholder notice while auth is still resolving, and does not fetch yet', () => {
    renderShell(null, true);

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(mockFetchLegalText).not.toHaveBeenCalled();
  });

  // A signed-in user's organization is the highest-priority context.
  it('never shows the placeholder notice for a signed-in user whose org has its own text', async () => {
    let resolveFetch!: (value: PublicLegalText) => void;
    const pending = new Promise<PublicLegalText>((resolve) => {
      resolveFetch = resolve;
    });
    mockFetchLegalText.mockReturnValueOnce(pending);

    renderShell(authedUser, false);

    // Lookup hasn't resolved yet — keep current behaviour, no notice.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    await waitFor(() =>
      expect(mockFetchLegalText).toHaveBeenCalledWith('privacy', 'test-org'),
    );

    resolveFetch({ kind: 'privacy', source: 'organization', text: 'Eigener Datenschutztext.' });

    expect(await screen.findByText('Eigener Datenschutztext.')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  // The join flow's wizard state is the second-priority context, used
  // when no user is signed in.
  it('uses the join flow wizard state org over the instance single_org_slug', async () => {
    sessionStorage.setItem('clavis_wizard_org_slug', 'wizard-org');
    __resetInstanceConfigForTests({
      brand_name: 'Stadtrallye',
      bootstrap_open: false,
      single_org_slug: 'instance-org',
    });
    mockFetchLegalText.mockResolvedValueOnce({
      kind: 'privacy',
      source: 'organization',
      text: 'Wizard-Org-Text.',
    });

    renderShell(null, false);

    await waitFor(() =>
      expect(mockFetchLegalText).toHaveBeenCalledWith('privacy', 'wizard-org'),
    );
    expect(await screen.findByText('Wizard-Org-Text.')).toBeInTheDocument();
  });

  // The instance's single organization is the last-resort context, used
  // when neither a signed-in user nor the wizard state name one.
  it('falls back to the instance single_org_slug when no user or wizard org is known', async () => {
    __resetInstanceConfigForTests({
      brand_name: 'Stadtrallye',
      bootstrap_open: false,
      single_org_slug: 'instance-org',
    });
    mockFetchLegalText.mockResolvedValueOnce({
      kind: 'privacy',
      source: 'organization',
      text: 'Single-Org-Text.',
    });

    renderShell(null, false);

    await waitFor(() =>
      expect(mockFetchLegalText).toHaveBeenCalledWith('privacy', 'instance-org'),
    );
    expect(await screen.findByText('Single-Org-Text.')).toBeInTheDocument();
  });

  // With no organization context at all, the instance's own text wins
  // over the bundled placeholder copy, and shows no notice.
  it('shows the instance text when no organization context is known at all', async () => {
    mockFetchLegalText.mockResolvedValueOnce({
      kind: 'privacy',
      source: 'instance',
      text: 'Instanzweiter Datenschutztext.',
    });

    renderShell(null, false);

    await waitFor(() => expect(mockFetchLegalText).toHaveBeenCalledWith('privacy', null));
    expect(await screen.findByText('Instanzweiter Datenschutztext.')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  // The placeholder notice shows only once the lookup settles and finds
  // neither an organization's nor the instance's own text.
  it('shows the placeholder notice once the lookup resolves with source "none"', async () => {
    mockFetchLegalText.mockResolvedValueOnce({ kind: 'privacy', source: 'none', text: null });
    renderShell(null, false);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /den endgültigen Text noch nicht veröffentlicht/,
    );
    expect(screen.getByText('Bundled placeholder copy')).toBeInTheDocument();
  });

  // A failed request must not read as "nothing published": it shows the
  // bundled copy with its own notice, never the placeholder notice.
  it('shows the load-failed notice, not the placeholder notice, when the request fails', async () => {
    mockFetchLegalText.mockResolvedValueOnce(null);
    renderShell(null, false);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /konnte gerade nicht geladen werden/,
    );
    expect(screen.getByText('Bundled placeholder copy')).toBeInTheDocument();
    expect(screen.queryByText(/den endgültigen Text noch nicht veröffentlicht/)).not.toBeInTheDocument();
  });

  it('neither locale file invents an address', () => {
    expect(JSON.stringify(deLegal)).not.toMatch(/\.example/);
    expect(JSON.stringify(enLegal)).not.toMatch(/\.example/);
  });

  // The bundled fallback is neutral: a second organization must not show
  // Augsburg's imprint, and the law is the DDG.
  it('keeps the bundled fallback free of one organization and of the old TMG', () => {
    for (const text of [JSON.stringify(deLegal), JSON.stringify(enLegal)]) {
      expect(text).not.toMatch(/Augsburg|Maximilian|DIWA/);
      expect(text).not.toMatch(/TMG/);
      expect(text).toMatch(/§ 5 DDG/);
    }
  });

  // The wordmark reads the brand from the instance config
  // (default "Clavis" until the boot config resolved).
  it('shows the brand wordmark', async () => {
    mockFetchLegalText.mockResolvedValueOnce({ kind: 'privacy', source: 'none', text: null });
    renderShell(null, false);

    expect(await screen.findByText('Clavis')).toBeInTheDocument();
  });

  // The heading sits in main, so the route focus can land on it, and the page
  // links its two siblings.
  it('puts the heading into main and links the legal pages', async () => {
    mockFetchLegalText.mockResolvedValueOnce({ kind: 'privacy', source: 'none', text: null });
    renderShell(null, false);

    const heading = await screen.findByRole('heading', { level: 1, name: 'Datenschutz' });
    expect(screen.getByRole('main')).toContainElement(heading);
    expect(screen.getByRole('navigation', { name: 'Rechtliches' })).toBeInTheDocument();
  });

  // The participant navigation is for players.
  describe('participant navigation', () => {
    const nav = () => screen.queryByRole('button', { name: /^(Rallye|Team|Mitteilungen)/ });

    beforeEach(() => {
      mockFetchLegalText.mockResolvedValue({ kind: 'impressum', source: 'none', text: null } as never);
      localStorage.clear();
    });

    it('is absent for a visitor without a session', () => {
      renderShell(null, false);
      expect(nav()).toBeNull();
    });

    it('is absent for staff, also with a remembered run', () => {
      localStorage.setItem('clavis_last_run_id', 'run-1');
      renderShell({ ...authedUser, role: 'org_admin' } as AuthUser, false);
      expect(nav()).toBeNull();
    });

    it('is absent for a guest who is in no run', () => {
      renderShell({ ...authedUser, role: 'guest' } as AuthUser, false);
      expect(nav()).toBeNull();
    });

    it('shows for a guest with a run to go back to', () => {
      localStorage.setItem('clavis_last_run_id', 'run-1');
      renderShell({ ...authedUser, role: 'guest' } as AuthUser, false);
      expect(screen.getByRole('button', { name: 'Rallye' })).toBeEnabled();
    });
  });
});
