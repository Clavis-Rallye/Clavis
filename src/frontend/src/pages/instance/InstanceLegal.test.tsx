import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import { ThemeProvider } from '@mui/material/styles';
import InstanceLegal from './InstanceLegal';
import theme from '../../theme';
import apiClient, { ApiError } from '../../api/client';
import { AuthContext } from '../../auth/AuthContext';
import type { InstanceLegalResponse } from '../../api/types';
import { legalTemplate } from '../../utils/legalTemplates';
vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

// The map services the legal answer carries unless a test says otherwise:
// the FOSSGIS tiles and the place index, the defaults.
const defaultMap = {
  map_tile_url: 'https://tile.openstreetmap.de/{z}/{x}/{y}.png',
  address_search_sources: ['local'] as ('local' | 'nominatim' | 'photon')[],
};

const meResponse = { id: 'admin-1', email: 'operator@example.com', totp_confirmed: true };

const emptyLegalResponse: InstanceLegalResponse = {
  legal_imprint: null,
  legal_privacy: null,
  legal_accessibility: null,
  updated_at: null,
  backup_retention_days: 30,
  backup_retention_source: 'default',
  retention_days: { LK_A: 90, LK_B: 90, LK_C: 365, LK_H: 365 },
  technical_log_days: 90,
  audit_log_days: 365,
  abandoned_run_days: 60,
  result_link_days: 90,
  ...defaultMap,
};

const filledLegalResponse: InstanceLegalResponse = {
  legal_imprint: '<p>Impressum-Text</p>',
  legal_privacy: '<p>Datenschutz-Text</p>',
  legal_accessibility: '<p>Barrierefreiheit-Text</p>',
  updated_at: '2026-03-01T10:00:00Z',
  backup_retention_days: 30,
  backup_retention_source: 'default',
  retention_days: { LK_A: 90, LK_B: 90, LK_C: 365, LK_H: 365 },
  technical_log_days: 90,
  audit_log_days: 365,
  abandoned_run_days: 60,
  result_link_days: 90,
  ...defaultMap,
};

const renderPage = () =>
  render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={['/instance/legal']}>
        <Routes>
          <Route path="/instance/legal" element={<InstanceLegal />} />
          <Route path="/instance/login" element={<div>Operator login</div>} />
          <Route path="/login" element={<div>Regular login</div>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );

// An operator who also has an organization session open in the same
// browser: the editors must still hide the image tool.
const orgUser = {
  id: 'u0',
  email: 'admin@example.com',
  display_name: 'Admin',
  role: 'org_admin' as const,
  organization_id: 'org-1',
  organization_slug: 'stadt',
};

const renderPageWithOrgSession = () =>
  render(
    <ThemeProvider theme={theme}>
      <AuthContext.Provider
        value={{
          user: orgUser,
          isAuthenticated: true,
          isLoading: false,
          login: vi.fn(),
          logout: vi.fn(),
          checkAuth: vi.fn(),
          needsTwoFactorSetup: () => false,
        }}
      >
        <MemoryRouter initialEntries={['/instance/legal']}>
          <Routes>
            <Route path="/instance/legal" element={<InstanceLegal />} />
            <Route path="/instance/login" element={<div>Operator login</div>} />
            <Route path="/login" element={<div>Regular login</div>} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    </ThemeProvider>,
  );

const mockAuthenticated = (legal: InstanceLegalResponse = emptyLegalResponse) => {
  vi.mocked(apiClient.get).mockImplementation((url: string) => {
    if (url === '/api/instance/auth/me') return Promise.resolve({ data: meResponse });
    if (url === '/api/instance/legal') return Promise.resolve({ data: legal });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
};

describe('InstanceLegal', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('loads the three legal texts into the rich-text editors', async () => {
    mockAuthenticated(filledLegalResponse);
    renderPage();

    const imprint = await screen.findByRole('textbox', { name: 'Impressum' });
    expect(imprint).toHaveTextContent('Impressum-Text');
    expect(screen.getByRole('textbox', { name: 'Datenschutz' })).toHaveTextContent('Datenschutz-Text');
    expect(screen.getByRole('textbox', { name: 'Barrierefreiheit' })).toHaveTextContent(
      'Barrierefreiheit-Text',
    );
  });

  // The editors carry no organization media store (routers/instance.py: no
  // media upload route exists for instance-level texts) — the image tool
  // must not appear.
  it('hides the image tool on every editor', async () => {
    mockAuthenticated(filledLegalResponse);
    renderPage();
    await screen.findByRole('textbox', { name: 'Impressum' });

    expect(screen.queryByRole('button', { name: 'Bild einfügen' })).not.toBeInTheDocument();
  });

  it('hides the image tool on every editor even with an organization session open', async () => {
    mockAuthenticated(filledLegalResponse);
    renderPageWithOrgSession();
    await screen.findByRole('textbox', { name: 'Impressum' });

    expect(screen.queryByRole('button', { name: 'Bild einfügen' })).not.toBeInTheDocument();
  });

  it('saves the backup period the operator enters, and the template states it', async () => {
    mockAuthenticated(emptyLegalResponse);
    vi.mocked(apiClient.put).mockResolvedValueOnce({
      data: { ...emptyLegalResponse, backup_retention_days: 365, backup_retention_source: 'instance' },
    });
    renderPage();
    const field = await screen.findByRole('spinbutton', { name: 'Aufbewahrung der Datensicherungen (Tage)' });
    expect(field).toHaveValue(30);
    expect(screen.getByText(/Noch nicht angegeben/)).toBeInTheDocument();

    await userEvent.clear(field);
    await userEvent.type(field, '365');
    await userEvent.click(screen.getByRole('button', { name: 'Speichern' }));

    await waitFor(() =>
      expect(apiClient.put).toHaveBeenCalledWith('/api/instance/legal', {
        backup_retention_days: 365,
      }),
    );
    expect(await screen.findByText(/Von dir angegeben/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Datenschutz' }));
    const privacy = screen.getByRole('textbox', { name: 'Datenschutz' });
    await waitFor(() => expect(privacy).toHaveTextContent('Datensicherungen: 365 Tage.'));
    expect(privacy).toHaveTextContent('Gastkonten: 90 Tage nach dem Ende des Laufs');
  });

  it('does not send the backup period while the field still shows the loaded value', async () => {
    mockAuthenticated(emptyLegalResponse);
    vi.mocked(apiClient.put).mockResolvedValueOnce({ data: emptyLegalResponse });
    renderPage();
    const field = await screen.findByRole('spinbutton', { name: 'Aufbewahrung der Datensicherungen (Tage)' });
    expect(field).toHaveValue(30);

    await userEvent.click(screen.getByRole('button', { name: 'Speichern' }));

    await waitFor(() => expect(apiClient.put).toHaveBeenCalledWith('/api/instance/legal', {}));
    expect(await screen.findByText('Die Rechtstexte wurden gespeichert.')).toBeInTheDocument();
    expect(screen.getByText(/Noch nicht angegeben/)).toBeInTheDocument();
  });

  it('sends null for an empty backup field, and the page then says nothing is stated', async () => {
    mockAuthenticated({ ...emptyLegalResponse, backup_retention_source: 'instance' });
    vi.mocked(apiClient.put).mockResolvedValueOnce({ data: emptyLegalResponse });
    renderPage();
    const field = await screen.findByRole('spinbutton', { name: 'Aufbewahrung der Datensicherungen (Tage)' });
    expect(screen.getByText(/Von dir angegeben/)).toBeInTheDocument();

    await userEvent.clear(field);
    await userEvent.click(screen.getByRole('button', { name: 'Speichern' }));

    await waitFor(() =>
      expect(apiClient.put).toHaveBeenCalledWith('/api/instance/legal', {
        backup_retention_days: null,
      }),
    );
    expect(await screen.findByText(/Noch nicht angegeben/)).toBeInTheDocument();
  });

  it('states the log periods of the installation in the inserted template', async () => {
    mockAuthenticated({ ...emptyLegalResponse, technical_log_days: 120, audit_log_days: 730 });
    renderPage();
    await screen.findByRole('textbox', { name: 'Impressum' });

    await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Datenschutz' }));
    const privacy = screen.getByRole('textbox', { name: 'Datenschutz' });
    await waitFor(() => expect(privacy).toHaveTextContent('Technische Protokolle: höchstens 120 Tage.'));
    expect(privacy).toHaveTextContent('Sicherheitsprotokoll: 730 Tage.');
  });

  it('states the tile server and the address search of the installation in the inserted template', async () => {
    mockAuthenticated({ ...emptyLegalResponse, technical_log_days: 120, audit_log_days: 730 });
    renderPage();
    await screen.findByRole('textbox', { name: 'Impressum' });

    await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Datenschutz' }));
    const privacy = screen.getByRole('textbox', { name: 'Datenschutz' });
    await waitFor(() => expect(privacy).toHaveTextContent('FOSSGIS e.V.'));
    expect(privacy).toHaveTextContent('mit Sitz in Deutschland');
    expect(privacy).toHaveTextContent('Diese Suche läuft über unseren Server und fragt keinen anderen Dienst.');
    expect(privacy).not.toHaveTextContent('Fastly');
    expect(privacy).not.toHaveTextContent('Nominatim');
  });

  it('takes the map values from the legal answer, never from /api/geocode/config', async () => {
    // /api/geocode/config refuses an instance-only operator session with 403,
    // so the page must not ask it.
    mockAuthenticated();
    renderPage();
    await screen.findByRole('textbox', { name: 'Impressum' });

    expect(apiClient.get).not.toHaveBeenCalledWith('/api/geocode/config', expect.anything());
  });

  it('states OSMF, Fastly and Nominatim when the answer names tile.openstreetmap.org and nominatim', async () => {
    mockAuthenticated({
      ...emptyLegalResponse,
      map_tile_url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
      address_search_sources: ['local', 'nominatim'],
    });
    renderPage();
    await screen.findByRole('textbox', { name: 'Impressum' });

    await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Datenschutz' }));
    const privacy = screen.getByRole('textbox', { name: 'Datenschutz' });
    await waitFor(() => expect(privacy).toHaveTextContent('OpenStreetMap Foundation'));
    expect(privacy).toHaveTextContent('Fastly');
    expect(privacy).toHaveTextContent('fragt er zusätzlich den Dienst Nominatim.');
    expect(privacy).not.toHaveTextContent('FOSSGIS');
  });

  it('saving with nothing changed sends an empty body', async () => {
    mockAuthenticated(emptyLegalResponse);
    vi.mocked(apiClient.put).mockResolvedValueOnce({ data: emptyLegalResponse });
    renderPage();
    await screen.findByRole('textbox', { name: 'Impressum' });

    await userEvent.click(screen.getByRole('button', { name: 'Speichern' }));

    await waitFor(() =>
      expect(apiClient.put).toHaveBeenCalledWith('/api/instance/legal', {}),
    );
    await waitFor(() =>
      expect(screen.getByText('Die Rechtstexte wurden gespeichert.')).toBeInTheDocument(),
    );
  });

  it('redirects to /instance/login on a 401 from the session probe', async () => {
    vi.mocked(apiClient.get).mockRejectedValueOnce(new ApiError(401, 'Not authenticated'));
    renderPage();

    await waitFor(() => expect(screen.getByText('Operator login')).toBeInTheDocument());
  });

  it('shows the no-operator-access message and a link to the regular login on a 403', async () => {
    vi.mocked(apiClient.get).mockRejectedValueOnce(
      new ApiError(403, 'Organization token not valid for instance endpoints'),
    );
    renderPage();

    await waitFor(() => expect(screen.getByText('Kein Betreiber-Zugriff')).toBeInTheDocument());
    expect(screen.getByRole('link', { name: /zur üblichen anmeldung/i })).toHaveAttribute(
      'href',
      '/login',
    );
  });

  it('renders the navigation with links to the other operator pages', async () => {
    mockAuthenticated();
    renderPage();
    await screen.findByRole('textbox', { name: 'Impressum' });

    const nav = screen.getByRole('navigation', { name: 'Betreiber-Navigation' });
    expect(within(nav).getByRole('link', { name: 'Organisationen' })).toBeInTheDocument();
    expect(within(nav).getByRole('link', { name: 'Protokoll' })).toBeInTheDocument();
    expect(within(nav).getByRole('link', { name: 'Konto' })).toBeInTheDocument();
  });

  describe('templates', () => {
    it('offers one template button per legal text', async () => {
      mockAuthenticated();
      renderPage();
      await screen.findByRole('textbox', { name: 'Impressum' });

      expect(screen.getByRole('button', { name: 'Vorlage einfügen: Impressum' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Vorlage einfügen: Datenschutz' })).toBeInTheDocument();
      expect(
        screen.getByRole('button', { name: 'Vorlage einfügen: Barrierefreiheit' }),
      ).toBeInTheDocument();
    });

    it('inserts the template into an empty field without a question and without saving', async () => {
      mockAuthenticated(emptyLegalResponse);
      renderPage();
      const imprint = await screen.findByRole('textbox', { name: 'Impressum' });

      await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Impressum' }));

      await waitFor(() => expect(imprint).toHaveTextContent('Verantwortlich für den Inhalt'));
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(screen.getByRole('textbox', { name: 'Datenschutz' })).toHaveTextContent('');
      expect(apiClient.put).not.toHaveBeenCalled();
    });

    it('saves the inserted template only through the save button', async () => {
      mockAuthenticated(emptyLegalResponse);
      vi.mocked(apiClient.put).mockResolvedValueOnce({
        data: { ...emptyLegalResponse, legal_privacy: legalTemplate('privacy') },
      });
      renderPage();
      await screen.findByRole('textbox', { name: 'Impressum' });

      await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Datenschutz' }));
      await userEvent.click(screen.getByRole('button', { name: 'Speichern' }));

      await waitFor(() =>
        expect(apiClient.put).toHaveBeenCalledWith('/api/instance/legal', {
          // The template states the installation's periods. The backup period
          // stays a placeholder in the inserted text, because the operator had
          // not stated it when the template went in.
          legal_privacy: legalTemplate('privacy', {
            retentionDays: emptyLegalResponse.retention_days,
            backupDays: 30,
            backupDaysConfirmed: false,
            technicalLogDays: emptyLegalResponse.technical_log_days,
            auditLogDays: emptyLegalResponse.audit_log_days,
            abandonedRunDays: emptyLegalResponse.abandoned_run_days,
            resultLinkDays: emptyLegalResponse.result_link_days,
            map: {
              tileUrl: defaultMap.map_tile_url,
              sources: [...defaultMap.address_search_sources],
            },
          }),
        }),
      );
    });

    it('asks before it replaces existing text, and keeps the text on cancel', async () => {
      mockAuthenticated(filledLegalResponse);
      renderPage();
      const imprint = await screen.findByRole('textbox', { name: 'Impressum' });

      await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Impressum' }));

      const dialog = await screen.findByRole('dialog', { name: 'Text ersetzen?' });
      expect(dialog).toHaveTextContent('Das Feld „Impressum“ enthält schon Text.');
      expect(imprint).toHaveTextContent('Impressum-Text');

      await userEvent.click(within(dialog).getByRole('button', { name: 'Abbrechen' }));

      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      expect(imprint).toHaveTextContent('Impressum-Text');
    });

    it('replaces existing text after the confirmation', async () => {
      mockAuthenticated(filledLegalResponse);
      renderPage();
      const imprint = await screen.findByRole('textbox', { name: 'Impressum' });

      await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Impressum' }));
      const dialog = await screen.findByRole('dialog', { name: 'Text ersetzen?' });
      await userEvent.click(within(dialog).getByRole('button', { name: 'Ersetzen' }));

      await waitFor(() => expect(imprint).toHaveTextContent('Verantwortlich für den Inhalt'));
      expect(imprint).not.toHaveTextContent('Impressum-Text');
      expect(apiClient.put).not.toHaveBeenCalled();
    });

    it('shows no placeholder warning for a finished text', async () => {
      mockAuthenticated(filledLegalResponse);
      renderPage();
      await screen.findByRole('textbox', { name: 'Impressum' });

      expect(screen.queryByText(/enthält noch Platzhalter/)).not.toBeInTheDocument();
    });

    it('warns about the placeholders of an inserted template', async () => {
      mockAuthenticated(emptyLegalResponse);
      renderPage();
      await screen.findByRole('textbox', { name: 'Impressum' });

      await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Impressum' }));

      const warning = await screen.findByText(/^Dieser Text enthält noch Platzhalter: /);
      expect(warning).toHaveTextContent('[NAME DER ORGANISATION], ');
      expect(warning).toHaveTextContent('[E-MAIL-ADRESSE]');
    });

    it('warns about placeholders in a text that is already saved', async () => {
      mockAuthenticated({
        ...filledLegalResponse,
        legal_accessibility: '<p>Diese Erklärung wurde am [DATUM DER ERSTELLUNG] erstellt.</p>',
      });
      renderPage();
      await screen.findByRole('textbox', { name: 'Impressum' });

      expect(
        screen.getByText('Dieser Text enthält noch Platzhalter: [DATUM DER ERSTELLUNG]'),
      ).toBeInTheDocument();
    });
  });

  describe('stored texts with h1, pre and code', () => {
    const legacyPrivacy = '<h1>Datenschutz</h1><pre><code>Zeile eins\nZeile zwei</code></pre><p>Ruf <code>ls</code> auf.</p>';
    const legacyLegal: InstanceLegalResponse = {
      ...filledLegalResponse,
      legal_privacy: legacyPrivacy,
    };

    it('opens the heading as a heading, before any save', async () => {
      mockAuthenticated(legacyLegal);
      renderPage();

      const privacy = await screen.findByRole('textbox', { name: 'Datenschutz' });
      await waitFor(() => expect(within(privacy).getByRole('heading', { level: 2 })).toHaveTextContent('Datenschutz'));
      expect(apiClient.put).not.toHaveBeenCalled();
    });

    it('sends only the changed text and leaves the stored one out', async () => {
      const stored = { ...legacyLegal, legal_imprint: null };
      mockAuthenticated(stored);
      vi.mocked(apiClient.put).mockResolvedValueOnce({ data: stored });
      renderPage();
      await screen.findByRole('textbox', { name: 'Impressum' });

      await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Impressum' }));
      await userEvent.click(screen.getByRole('button', { name: 'Speichern' }));

      await waitFor(() => expect(apiClient.put).toHaveBeenCalledTimes(1));
      const body = vi.mocked(apiClient.put).mock.calls[0][1] as Record<string, unknown>;
      expect(Object.keys(body)).toEqual(['legal_imprint']);
      expect(body.legal_imprint).toEqual(legalTemplate('imprint'));
    });

    it('sends a template that was inserted into the stored text', async () => {
      mockAuthenticated(legacyLegal);
      vi.mocked(apiClient.put).mockResolvedValueOnce({ data: legacyLegal });
      renderPage();
      await screen.findByRole('textbox', { name: 'Impressum' });

      await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Datenschutz' }));
      const dialog = await screen.findByRole('dialog', { name: 'Text ersetzen?' });
      await userEvent.click(within(dialog).getByRole('button', { name: 'Ersetzen' }));
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      await userEvent.click(screen.getByRole('button', { name: 'Speichern' }));

      await waitFor(() => expect(apiClient.put).toHaveBeenCalledTimes(1));
      const body = vi.mocked(apiClient.put).mock.calls[0][1] as Record<string, unknown>;
      expect(Object.keys(body)).toEqual(['legal_privacy']);
    });
  });

  it('has no axe accessibility violations once signed in', async () => {
    mockAuthenticated(filledLegalResponse);
    const { container } = renderPage();
    await screen.findByRole('textbox', { name: 'Impressum' });

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
