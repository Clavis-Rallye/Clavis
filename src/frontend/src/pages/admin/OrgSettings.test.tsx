import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import OrgSettings from './OrgSettings';
import { AuthContext } from '../../auth/AuthContext';
import apiClient, { ApiError } from '../../api/client';
import { legalTemplate } from '../../utils/legalTemplates';
import { getGeocodeConfig, type GeocodeConfig } from '../../utils/geocode';
import i18n from '../../i18n';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

vi.mock('../../utils/geocode', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../utils/geocode')>();
  return { ...actual, getGeocodeConfig: vi.fn() };
});

// The geocode config the pages can rely on unless a test says otherwise.
const defaultGeocodeConfig: GeocodeConfig = { typeahead: false, sources: ['local'], centre: null };

const adminUser = {
  id: 'u0',
  email: 'admin@example.com',
  display_name: 'Admin',
  role: 'org_admin' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const orgFixture = {
  id: 'org-1',
  name: 'Test Organisation',
  slug: 'test-org',
  languages: ['de'],
  settings: {},
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const retentionFixture = {
  periods: [
    { deletion_class: 'LK_A', days: 30, source: 'default', description: 'Gastkonten & Gast-Anzeigenamen' },
    { deletion_class: 'LK_B', days: 90, source: 'default', description: 'Chat-Nachrichten & Medien' },
    { deletion_class: 'LK_C', days: 365, source: 'default', description: 'Anonymisierung der Lauf-Daten' },
    { deletion_class: 'LK_H', days: 400, source: 'default', description: 'Antworttexte & Kommentare' },
  ],
  min_days: 30,
  max_days: 3650,
  backup_retention_days: 30,
  backup_retention_source: 'default',
  technical_log_days: 90,
  audit_log_days: 365,
  abandoned_run_days: 60,
  result_link_days: 90,
};

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={['/admin/settings']}>
      <AuthContext.Provider
        value={{
          user: adminUser,
          isAuthenticated: true,
          isLoading: false,
          login: vi.fn(),
          logout: vi.fn(),
          checkAuth: vi.fn(),
          needsTwoFactorSetup: () => false,
        }}
      >
        <Routes>
          <Route path="/admin/settings" element={<OrgSettings />} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('OrgSettings', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getGeocodeConfig).mockResolvedValue(defaultGeocodeConfig);
  });

  it('populates name field from getOrg response', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: orgFixture });
    renderPage();
    await waitFor(() =>
      expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument(),
    );
    expect(screen.getByDisplayValue('test-org')).toBeInTheDocument();
  });

  it('save button is disabled when name is unchanged', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: orgFixture });
    renderPage();
    await waitFor(() =>
      expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: /speichern/i })).toBeDisabled();
  });

  it('save button becomes enabled when name changes', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: orgFixture });
    renderPage();
    await waitFor(() =>
      expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument(),
    );

    const nameInput = screen.getByDisplayValue('Test Organisation');
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, 'New Name');

    expect(screen.getByRole('button', { name: /speichern/i })).toBeEnabled();
  });

  it('save calls updateOrg with new name body', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: orgFixture });
    vi.mocked(apiClient.put).mockResolvedValueOnce({
      data: { ...orgFixture, name: 'Updated Org' },
    });
    renderPage();
    await waitFor(() =>
      expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument(),
    );

    const nameInput = screen.getByDisplayValue('Test Organisation');
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, 'Updated Org');
    await userEvent.click(screen.getByRole('button', { name: /speichern/i }));

    await waitFor(() => expect(vi.mocked(apiClient.put)).toHaveBeenCalledOnce());
    expect(vi.mocked(apiClient.put)).toHaveBeenCalledWith(
      '/api/orgs/test-org',
      expect.objectContaining({ name: 'Updated Org' }),
    );
  });

  it('retention tab loads and shows the configurable periods', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: orgFixture })
      .mockResolvedValueOnce({ data: retentionFixture });
    renderPage();
    await waitFor(() =>
      expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole('tab', { name: /löschfristen/i }));
    await waitFor(() => expect(screen.getByDisplayValue('90')).toBeInTheDocument());
    expect(screen.getByText(/LK_B/)).toBeInTheDocument();
    expect(screen.getByDisplayValue('30')).toBeInTheDocument();
    expect(screen.getByDisplayValue('365')).toBeInTheDocument();
    // The period for answer text and comments, and the operator's backup period.
    expect(screen.getByText('Antworttexte & Kommentare')).toBeInTheDocument();
    expect(screen.getByDisplayValue('400')).toBeInTheDocument();
    expect(screen.getByText(/Die App nimmt 30 Tage an/)).toBeInTheDocument();
  });

  it('states the backup period the operator saved', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: orgFixture })
      .mockResolvedValueOnce({
        data: { ...retentionFixture, backup_retention_days: 365, backup_retention_source: 'instance' },
      });
    renderPage();
    await waitFor(() =>
      expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole('tab', { name: /löschfristen/i }));
    expect(
      await screen.findByText(/kann gelöschte Daten noch 365 Tage lang enthalten/),
    ).toBeInTheDocument();
  });

  it('retention save sends the full override set', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: orgFixture })
      .mockResolvedValueOnce({ data: retentionFixture });
    vi.mocked(apiClient.put).mockResolvedValueOnce({
      data: {
        ...retentionFixture,
        periods: retentionFixture.periods.map((p) =>
          p.deletion_class === 'LK_B' ? { ...p, days: 120, source: 'override' } : p,
        ),
      },
    });
    renderPage();
    await waitFor(() =>
      expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole('tab', { name: /löschfristen/i }));
    await waitFor(() => expect(screen.getByDisplayValue('90')).toBeInTheDocument());

    const lkbInput = screen.getByDisplayValue('90');
    await userEvent.clear(lkbInput);
    await userEvent.type(lkbInput, '120');
    await userEvent.click(screen.getByRole('button', { name: /speichern/i }));

    await waitFor(() => expect(vi.mocked(apiClient.put)).toHaveBeenCalledOnce());
    expect(vi.mocked(apiClient.put)).toHaveBeenCalledWith(
      '/api/orgs/test-org/admin/retention',
      { lk_a_days: 30, lk_b_days: 120, lk_c_days: 365, lk_h_days: 400 },
    );
  });

  it('success alert appears after save', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: orgFixture });
    vi.mocked(apiClient.put).mockResolvedValueOnce({
      data: { ...orgFixture, name: 'Updated Org' },
    });
    renderPage();
    await waitFor(() =>
      expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument(),
    );

    const nameInput = screen.getByDisplayValue('Test Organisation');
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, 'Updated Org');
    await userEvent.click(screen.getByRole('button', { name: /speichern/i }));

    await waitFor(() =>
      expect(screen.getByText('Einstellungen wurden gespeichert.')).toBeInTheDocument(),
    );
  });

  it('has no axe accessibility violations on initial render', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: orgFixture });
    const { container } = renderPage();
    await waitFor(() =>
      expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument(),
    );
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  // The tab strip itself scrolls on a phone. With the standard variant the last
  // tabs would lie past the screen edge and could not be reached. The
  // scrollable strip keeps them in a sideways scroll container.
  it('scrolls the tab strip and reaches every tab by keyboard', async () => {
    // The arrow-key walk selects each tab once: the org on load, the retention
    // config on Rechtliches (kept for Löschfristen), the media summary on
    // Medien, the mail status when Enter opens E-Mail.
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: orgFixture })
      .mockResolvedValueOnce({ data: retentionFixture })
      .mockResolvedValueOnce({ data: { photo: 0, video: 0, audio: 0 } })
      .mockResolvedValueOnce({ data: { enabled: false } });
    renderPage();
    await waitFor(() =>
      expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument(),
    );

    const scroller = screen.getByRole('tablist').parentElement!;
    expect(scroller).toHaveClass('MuiTabs-scroller', 'MuiTabs-scrollableX');
    expect(window.getComputedStyle(scroller).overflowX).toBe('auto');

    // Roving tabindex: the arrow keys walk focus through the whole strip,
    // including the tabs a narrow screen cannot show at once.
    screen.getByRole('tab', { name: 'Allgemein' }).focus();
    for (const label of ['Rechtliches', 'Löschfristen', 'Medien', 'E-Mail']) {
      await userEvent.keyboard('{ArrowRight}');
      expect(screen.getByRole('tab', { name: label })).toHaveFocus();
    }

    // Enter activates the focused tab: E-Mail opens its panel.
    await userEvent.keyboard('{Enter}');
    expect(screen.getByRole('tab', { name: 'E-Mail' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(
      await screen.findByText(/Einladungen und Passwort-Links werden nicht zugestellt/),
    ).toBeInTheDocument();
  });
});

describe('OrgSettings, legal templates', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getGeocodeConfig).mockResolvedValue(defaultGeocodeConfig);
  });

  const filledOrg = {
    ...orgFixture,
    legal_imprint: '<p>Impressum-Text</p>',
    legal_privacy: '<p>Datenschutz-Text</p>',
    legal_accessibility: '<p>Barrierefreiheit-Text</p>',
  };

  const renderWithLegalTab = async (org: object = orgFixture) => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: org });
    const view = renderPage();
    await waitFor(() =>
      expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole('tab', { name: 'Rechtliches' }));
    await screen.findByRole('textbox', { name: 'Impressum' });
    return view;
  };

  it('offers one template button per legal text', async () => {
    await renderWithLegalTab();

    expect(screen.getByRole('button', { name: 'Vorlage einfügen: Impressum' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Vorlage einfügen: Datenschutz' })).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Vorlage einfügen: Barrierefreiheit' }),
    ).toBeInTheDocument();
  });

  it('inserts the template into an empty field without a question and without saving', async () => {
    await renderWithLegalTab();
    const accessibility = screen.getByRole('textbox', { name: 'Barrierefreiheit' });

    await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Barrierefreiheit' }));

    await waitFor(() => expect(accessibility).toHaveTextContent('Nicht barrierefreie Inhalte'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Impressum' })).toHaveTextContent('');
    expect(apiClient.put).not.toHaveBeenCalled();
  });

  it('states the log periods of the installation in the privacy template', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: orgFixture })
      .mockResolvedValueOnce({ data: { ...retentionFixture, technical_log_days: 120, audit_log_days: 730 } });
    renderPage();
    await waitFor(() =>
      expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole('tab', { name: 'Rechtliches' }));
    await screen.findByRole('textbox', { name: 'Impressum' });

    await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Datenschutz' }));
    const privacy = screen.getByRole('textbox', { name: 'Datenschutz' });
    await waitFor(() => expect(privacy).toHaveTextContent('Technische Protokolle: höchstens 120 Tage.'));
    expect(privacy).toHaveTextContent('Sicherheitsprotokoll: 730 Tage.');
  });

  it('states the sweep period of a run that never ends and the result-link period in the privacy template', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: orgFixture })
      .mockResolvedValueOnce({ data: { ...retentionFixture, abandoned_run_days: 45, result_link_days: 30 } });
    renderPage();
    await waitFor(() =>
      expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole('tab', { name: 'Rechtliches' }));
    await screen.findByRole('textbox', { name: 'Impressum' });

    await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Datenschutz' }));
    const privacy = screen.getByRole('textbox', { name: 'Datenschutz' });
    await waitFor(() => expect(privacy).toHaveTextContent('45 Tage nach dem Start des Laufs'));
    expect(privacy).toHaveTextContent('Ergebnis-Links: 30 Tage nach der Veröffentlichung');
  });

  it('states the tile server and the address search of the installation in the privacy template', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: orgFixture })
      .mockResolvedValueOnce({ data: retentionFixture });
    vi.mocked(getGeocodeConfig).mockResolvedValue({
      typeahead: false,
      sources: ['local', 'nominatim'],
      centre: null,
      tile_url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    });
    renderPage();
    await waitFor(() =>
      expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole('tab', { name: 'Rechtliches' }));
    await screen.findByRole('textbox', { name: 'Impressum' });

    await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Datenschutz' }));
    const privacy = screen.getByRole('textbox', { name: 'Datenschutz' });
    await waitFor(() => expect(privacy).toHaveTextContent('OpenStreetMap Foundation'));
    expect(privacy).toHaveTextContent('Fastly');
    expect(privacy).toHaveTextContent('fragt er zusätzlich den Dienst Nominatim.');
  });

  it('keeps the map wording when the retention settings fail to load', async () => {
    // The map values must not depend on the retention values.
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: orgFixture })
      .mockRejectedValueOnce(new ApiError(500, 'retention failed'));
    vi.mocked(getGeocodeConfig).mockResolvedValue({
      ...defaultGeocodeConfig,
      tile_url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
      sources: ['local', 'photon'],
    });
    renderPage();
    await waitFor(() =>
      expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole('tab', { name: 'Rechtliches' }));
    await screen.findByRole('textbox', { name: 'Impressum' });

    await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Datenschutz' }));
    const privacy = screen.getByRole('textbox', { name: 'Datenschutz' });
    await waitFor(() => expect(privacy).toHaveTextContent('OpenStreetMap Foundation'));
    expect(privacy).toHaveTextContent('fragt er zusätzlich den Dienst Photon.');
    // The periods stay placeholders: the retention settings never arrived.
    expect(privacy).toHaveTextContent('[DAUER DER TECHNISCHEN PROTOKOLLE]');
  });

  it('disables the privacy insert until the map configuration is known', async () => {
    // An insertion before the answer arrives must not assert the FOSSGIS default
    // on an installation configured otherwise.
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: orgFixture });
    let resolveConfig: (config: GeocodeConfig) => void = () => {};
    vi.mocked(getGeocodeConfig).mockImplementation(
      () => new Promise<GeocodeConfig>((resolve) => (resolveConfig = resolve)),
    );
    renderPage();
    await waitFor(() =>
      expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole('tab', { name: 'Rechtliches' }));
    await screen.findByRole('textbox', { name: 'Impressum' });

    expect(screen.getByRole('button', { name: 'Vorlage einfügen: Datenschutz' })).toBeDisabled();

    resolveConfig({
      ...defaultGeocodeConfig,
      tile_url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Vorlage einfügen: Datenschutz' })).toBeEnabled(),
    );
  });

  it('inserts bracket placeholders and says so when the map configuration fails to load', async () => {
    // A failed fetch must never assert the FOSSGIS default: the unknown-host and
    // unknown-search brackets stand in instead.
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: orgFixture })
      .mockResolvedValueOnce({ data: retentionFixture });
    vi.mocked(getGeocodeConfig).mockResolvedValue({
      typeahead: false,
      sources: [],
      centre: null,
      unknown: true,
    });
    renderPage();
    await waitFor(() =>
      expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole('tab', { name: 'Rechtliches' }));
    await screen.findByRole('textbox', { name: 'Impressum' });

    expect(
      await screen.findByText(/Karten- und Adresssuche-Einstellungen .* konnten nicht geladen werden/),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Datenschutz' }));
    const privacy = screen.getByRole('textbox', { name: 'Datenschutz' });
    await waitFor(() => expect(privacy).toHaveTextContent('[NAME DES ANBIETERS DER KARTENBILDER]'));
    expect(privacy).toHaveTextContent('[OB DIESE SUCHE EINEN DIENST AUSSERHALB UNSERES SERVERS FRAGT');
    expect(privacy).not.toHaveTextContent('FOSSGIS');
    expect(privacy).not.toHaveTextContent('Fastly');
  });

  it('says in English that the map configuration failed to load', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: orgFixture })
      .mockResolvedValueOnce({ data: retentionFixture });
    vi.mocked(getGeocodeConfig).mockResolvedValue({ typeahead: false, sources: [], centre: null, unknown: true });
    await i18n.changeLanguage('en');
    try {
      renderPage();
      await waitFor(() => expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument());
      await userEvent.click(screen.getByRole('tab', { name: 'Legal' }));

      expect(await screen.findByText(/settings of this installation could not be loaded/)).toBeInTheDocument();
      expect(screen.queryByText('mapLoadFailed')).not.toBeInTheDocument();
    } finally {
      await i18n.changeLanguage('de');
    }
  });

  it('saves the inserted template only through the save button', async () => {
    await renderWithLegalTab();
    vi.mocked(apiClient.put).mockResolvedValueOnce({
      data: { ...orgFixture, legal_imprint: legalTemplate('imprint') },
    });

    await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Impressum' }));
    await userEvent.click(screen.getByRole('button', { name: /speichern/i }));

    await waitFor(() =>
      expect(apiClient.put).toHaveBeenCalledWith('/api/orgs/test-org', {
        legal_imprint: legalTemplate('imprint'),
      }),
    );
  });

  it('opens an old heading as a heading and sends only the text that changed', async () => {
    const legacyOrg = {
      ...filledOrg,
      legal_imprint: null,
      legal_privacy: '<h1>Datenschutz</h1><pre><code>Zeile eins\nZeile zwei</code></pre><p>Ruf <code>ls</code> auf.</p>',
    };
    await renderWithLegalTab(legacyOrg);
    vi.mocked(apiClient.put).mockResolvedValueOnce({ data: legacyOrg });
    const privacy = screen.getByRole('textbox', { name: 'Datenschutz' });
    await waitFor(() => expect(within(privacy).getByRole('heading', { level: 2 })).toHaveTextContent('Datenschutz'));
    expect(apiClient.put).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Impressum' }));
    await userEvent.click(screen.getByRole('button', { name: /speichern/i }));

    await waitFor(() => expect(apiClient.put).toHaveBeenCalledTimes(1));
    const body = vi.mocked(apiClient.put).mock.calls[0][1] as Record<string, unknown>;
    expect(Object.keys(body)).toEqual(['legal_imprint']);
    expect(body.legal_imprint).toEqual(legalTemplate('imprint'));
  });

  it('asks before it replaces existing text, and keeps the text on cancel', async () => {
    await renderWithLegalTab(filledOrg);
    const privacy = screen.getByRole('textbox', { name: 'Datenschutz' });

    await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Datenschutz' }));

    const dialog = await screen.findByRole('dialog', { name: 'Text ersetzen?' });
    expect(dialog).toHaveTextContent('Das Feld „Datenschutz“ enthält schon Text.');
    expect(privacy).toHaveTextContent('Datenschutz-Text');

    await userEvent.click(within(dialog).getByRole('button', { name: 'Abbrechen' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(privacy).toHaveTextContent('Datenschutz-Text');
  });

  it('replaces existing text after the confirmation', async () => {
    await renderWithLegalTab(filledOrg);
    const privacy = screen.getByRole('textbox', { name: 'Datenschutz' });

    await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Datenschutz' }));
    const dialog = await screen.findByRole('dialog', { name: 'Text ersetzen?' });
    await userEvent.click(within(dialog).getByRole('button', { name: 'Ersetzen' }));

    await waitFor(() => expect(privacy).toHaveTextContent('1. Wer ist verantwortlich'));
    expect(privacy).not.toHaveTextContent('Datenschutz-Text');
    expect(apiClient.put).not.toHaveBeenCalled();
  });

  it('shows no placeholder warning for a finished text', async () => {
    await renderWithLegalTab(filledOrg);

    expect(screen.queryByText(/enthält noch Platzhalter/)).not.toBeInTheDocument();
  });

  it('warns about the placeholders of an inserted template', async () => {
    await renderWithLegalTab();

    await userEvent.click(screen.getByRole('button', { name: 'Vorlage einfügen: Impressum' }));

    const warning = await screen.findByText(/^Dieser Text enthält noch Platzhalter: /);
    expect(warning).toHaveTextContent('[NAME DER ORGANISATION], ');
  });

  it('warns about placeholders in a text that is already saved', async () => {
    await renderWithLegalTab({
      ...filledOrg,
      legal_privacy: '<p>Schreibe an [E-MAIL-ADRESSE]. Stand: [DATUM]</p>',
    });

    expect(
      screen.getByText('Dieser Text enthält noch Platzhalter: [E-MAIL-ADRESSE], [DATUM]'),
    ).toBeInTheDocument();
  });

  it('has no axe violations on the legal tab with a warning shown', async () => {
    const { container } = await renderWithLegalTab({
      ...filledOrg,
      legal_privacy: '<p>Stand: [DATUM]</p>',
    });

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});

describe('OrgSettings — media purge', () => {
  beforeEach(() => vi.resetAllMocks());

  const mediaSummary = { photo: 3, video: 2, audio: 1 };

  // Page load GETs the org; opening the media tab GETs the summary. A purge
  // refreshes the summary a third time.
  const renderWithMediaTab = async (summary: object = mediaSummary) => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: orgFixture })
      .mockResolvedValueOnce({ data: summary });
    renderPage();
    await waitFor(() =>
      expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole('tab', { name: /medien/i }));
  };

  const openPurgeDialog = async () => {
    await waitFor(() => expect(screen.getByText('3 gespeichert')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Fotos bereinigen' }));
    return await screen.findByRole('dialog');
  };

  it('shows the stored count of photos, videos and audio files', async () => {
    await renderWithMediaTab();

    await waitFor(() => expect(screen.getByText('3 gespeichert')).toBeInTheDocument());
    expect(screen.getByText('2 gespeichert')).toBeInTheDocument();
    expect(screen.getByText('1 gespeichert')).toBeInTheDocument();
    expect(apiClient.get).toHaveBeenCalledWith('/api/orgs/test-org/admin/media/summary');
  });

  it('purge actions are disabled for a type with no stored objects', async () => {
    await renderWithMediaTab({ photo: 0, video: 0, audio: 0 });

    await waitFor(() => expect(screen.getAllByText('0 gespeichert')).toHaveLength(3));
    const buttons = screen.getAllByRole('button', { name: /bereinigen$/ });
    expect(buttons).toHaveLength(3);
    for (const button of buttons) {
      expect(button).toBeDisabled();
    }
  });

  it('the dialog explains what is removed, that scores stay, and asks for the slug', async () => {
    await renderWithMediaTab();
    const dialog = await openPurgeDialog();

    expect(within(dialog).getByText('Fotos bereinigen?')).toBeInTheDocument();
    expect(within(dialog).getByText(/unwiderruflich gelöscht/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Medium entfernt/)).toBeInTheDocument();
    expect(
      within(dialog).getByText('Bewertungen, Punkte und Ranglisten bleiben unverändert.'),
    ).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Kurzname der Organisation')).toBeInTheDocument();
  });

  it('confirm stays disabled until the typed slug matches the organization', async () => {
    await renderWithMediaTab();
    const dialog = await openPurgeDialog();

    const confirm = within(dialog).getByRole('button', { name: 'Bereinigen' });
    expect(confirm).toBeDisabled();

    const slugInput = within(dialog).getByLabelText('Kurzname der Organisation');
    await userEvent.type(slugInput, 'wrong-org');
    expect(confirm).toBeDisabled();

    await userEvent.clear(slugInput);
    await userEvent.type(slugInput, 'test-org');
    expect(confirm).toBeEnabled();

    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it('purges with type and confirm slug as payload, then refreshes the counts', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: orgFixture })
      .mockResolvedValueOnce({ data: mediaSummary })
      .mockResolvedValueOnce({ data: { photo: 0, video: 2, audio: 1 } });
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { type: 'photo', purged: 3 } });
    renderPage();
    await waitFor(() =>
      expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole('tab', { name: /medien/i }));
    const dialog = await openPurgeDialog();

    await userEvent.type(
      within(dialog).getByLabelText('Kurzname der Organisation'),
      'test-org',
    );
    await userEvent.click(within(dialog).getByRole('button', { name: 'Bereinigen' }));

    await waitFor(() => expect(apiClient.post).toHaveBeenCalledOnce());
    expect(apiClient.post).toHaveBeenCalledWith('/api/orgs/test-org/admin/media/purge', {
      type: 'photo',
      confirm_slug: 'test-org',
    });

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    expect(await screen.findByText('3 Dateien gelöscht.')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('0 gespeichert')).toBeInTheDocument());
  });

  it('shows the API error in the dialog when the purge is refused', async () => {
    await renderWithMediaTab();
    const dialog = await openPurgeDialog();

    vi.mocked(apiClient.post).mockRejectedValueOnce(
      new ApiError(422, 'Confirmation slug does not match', 'CONFIRMATION_MISMATCH'),
    );
    await userEvent.type(
      within(dialog).getByLabelText('Kurzname der Organisation'),
      'test-org',
    );
    await userEvent.click(within(dialog).getByRole('button', { name: 'Bereinigen' }));

    expect(await within(screen.getByRole('dialog')).findByText(/does not match/)).toBeInTheDocument();
    // The dialog stays open so the admin can retry.
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});

// The backups heading on the retention tab is an h2 directly after the page's
// h1. The h1 breaks its one long word with hyphens:auto next to its
// overflow-wrap, so the browser may break it at a syllable.
describe('OrgSettings — headings and the hyphenated break', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getGeocodeConfig).mockResolvedValue(defaultGeocodeConfig);
  });

  const renderRetentionTab = async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: orgFixture })
      .mockResolvedValueOnce({ data: retentionFixture });
    const result = renderPage();
    await waitFor(() =>
      expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole('tab', { name: /löschfristen/i }));
    await screen.findByText('Datensicherungen');
    return result;
  };

  // No heading skips a level, and the look stays: the backups heading keeps
  // subtitle2.
  it('jumps no heading level: the backups heading is an h2 under the h1', async () => {
    const { container } = await renderRetentionTab();

    const levels = Array.from(container.querySelectorAll('h1,h2,h3,h4,h5,h6')).map(
      (h) => Number(h.tagName[1]),
    );
    expect(levels).toEqual([1, 2]);
    expect(container.querySelector('h2')).toHaveClass('MuiTypography-subtitle2');
  });

  // The hyphenated break rides on the same sx as the overflow-wrap. jsdom
  // applies no stylesheet cascade, so the rule is asserted against the
  // generated CSS of the h1's own class (the pattern of AppShell's padding
  // test).
  it('breaks the long settings word with a hyphen where the language allows it', async () => {
    await renderRetentionTab();

    const h1 = screen.getByRole('heading', { level: 1, name: 'Organisations\u00ADeinstellungen' });
    const ownCss = Array.from(document.styleSheets)
      .flatMap((sheet) => Array.from((sheet as CSSStyleSheet).cssRules))
      .map((rule) => rule.cssText)
      .filter((text) => Array.from(h1.classList).some((cls) => text.includes(cls)))
      .join('\n');

    expect(ownCss).toMatch(/hyphens:\s*auto/);
  });

  // hyphens auto needs German hyphenation data, which not every browser ships.
  // One soft hyphen (U+00AD) in the German string breaks the word at the same
  // place everywhere and shows a hyphen only at the break.
  it('holds exactly one soft hyphen in the German title, between Organisations and einstellungen', async () => {
    await renderRetentionTab();

    const title = screen.getByRole('heading', { level: 1 }).textContent ?? '';
    expect(title.split('\u00AD')).toEqual(['Organisations', 'einstellungen']);
  });
});

// The automatic start switch is a field of the general form. It is saved with
// Speichern, not by a click, and its help line follows the switch before the
// save.
describe('OrgSettings — automatic start switch', () => {
  const ON_TEXT = 'Geplante Läufe starten zu ihrer Startzeit von selbst.';
  const OFF_TEXT = 'Die Startzeit ist nur ein Plan, und die Spielleitung startet den Lauf von Hand.';
  const SWITCH = { name: /läufe zur geplanten startzeit automatisch starten/i };

  const orgWith = (settings: Record<string, unknown>) => ({ ...orgFixture, settings });

  const open = async (org: typeof orgFixture | ReturnType<typeof orgWith>) => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: org });
    const view = renderPage();
    await waitFor(() => expect(screen.getByDisplayValue('Test Organisation')).toBeInTheDocument());
    return view;
  };
  const saveButton = () => screen.getByRole('button', { name: /speichern/i });

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getGeocodeConfig).mockResolvedValue(defaultGeocodeConfig);
  });

  it('sends nothing when the switch is changed', async () => {
    await open(orgFixture);
    await userEvent.click(screen.getByRole('switch', SWITCH));
    expect(vi.mocked(apiClient.put)).not.toHaveBeenCalled();
  });

  it('saves the switch with Speichern and keeps the other settings keys', async () => {
    await open(orgWith({ other_key: 7 }));
    vi.mocked(apiClient.put).mockResolvedValueOnce({
      data: orgWith({ other_key: 7, auto_start_runs: false }),
    });
    await userEvent.click(screen.getByRole('switch', SWITCH));
    await userEvent.click(saveButton());

    await waitFor(() => expect(vi.mocked(apiClient.put)).toHaveBeenCalledOnce());
    expect(vi.mocked(apiClient.put)).toHaveBeenCalledWith('/api/orgs/test-org', {
      name: 'Test Organisation',
      languages: ['de'],
      settings: { other_key: 7, auto_start_runs: false },
    });
    expect(await screen.findByText('Einstellungen wurden gespeichert.')).toBeInTheDocument();
    expect(screen.getByRole('switch', SWITCH)).not.toBeChecked();
    expect(saveButton()).toBeDisabled();
  });

  it('saves the switch together with the name of the same form', async () => {
    await open(orgFixture);
    vi.mocked(apiClient.put).mockResolvedValueOnce({
      data: { ...orgWith({ auto_start_runs: false }), name: 'Updated Org' },
    });
    const nameInput = screen.getByDisplayValue('Test Organisation');
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, 'Updated Org');
    await userEvent.click(screen.getByRole('switch', SWITCH));
    await userEvent.click(saveButton());

    await waitFor(() => expect(vi.mocked(apiClient.put)).toHaveBeenCalledOnce());
    expect(vi.mocked(apiClient.put)).toHaveBeenCalledWith(
      '/api/orgs/test-org',
      expect.objectContaining({
        name: 'Updated Org',
        settings: { auto_start_runs: false },
      }),
    );
  });

  it('enables Speichern with the switch and disables it again when put back', async () => {
    await open(orgFixture);
    expect(saveButton()).toBeDisabled();
    await userEvent.click(screen.getByRole('switch', SWITCH));
    expect(saveButton()).toBeEnabled();
    await userEvent.click(screen.getByRole('switch', SWITCH));
    expect(saveButton()).toBeDisabled();
  });

  it('compares with the stored value when the stored switch is off', async () => {
    await open(orgWith({ auto_start_runs: false }));
    expect(screen.getByRole('switch', SWITCH)).not.toBeChecked();
    expect(saveButton()).toBeDisabled();
    await userEvent.click(screen.getByRole('switch', SWITCH));
    expect(saveButton()).toBeEnabled();
    vi.mocked(apiClient.put).mockResolvedValueOnce({ data: orgWith({ auto_start_runs: true }) });
    await userEvent.click(saveButton());
    await waitFor(() =>
      expect(vi.mocked(apiClient.put)).toHaveBeenCalledWith(
        '/api/orgs/test-org',
        expect.objectContaining({ settings: { auto_start_runs: true } }),
      ),
    );
  });

  it('shows one text for on and one for off, following the switch at once', async () => {
    await open(orgFixture);
    expect(screen.getByText(ON_TEXT)).toBeInTheDocument();
    expect(screen.queryByText(OFF_TEXT)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('switch', SWITCH));
    expect(screen.getByText(OFF_TEXT)).toBeInTheDocument();
    expect(screen.queryByText(ON_TEXT)).not.toBeInTheDocument();
    expect(vi.mocked(apiClient.put)).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('switch', SWITCH));
    expect(screen.getByText(ON_TEXT)).toBeInTheDocument();
  });

  it('shows the off text for a stored off value', async () => {
    await open(orgWith({ auto_start_runs: false }));
    expect(screen.getByText(OFF_TEXT)).toBeInTheDocument();
    expect(screen.queryByText(ON_TEXT)).not.toBeInTheDocument();
  });

  it('shows the switch on without a stored value and does not write the key for another field', async () => {
    await open(orgFixture);
    expect(screen.getByRole('switch', SWITCH)).toBeChecked();
    vi.mocked(apiClient.put).mockResolvedValueOnce({ data: { ...orgFixture, name: 'Updated Org' } });
    const nameInput = screen.getByDisplayValue('Test Organisation');
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, 'Updated Org');
    await userEvent.click(saveButton());

    await waitFor(() => expect(vi.mocked(apiClient.put)).toHaveBeenCalledOnce());
    const body = vi.mocked(apiClient.put).mock.calls[0][1] as Record<string, unknown>;
    expect(body).not.toHaveProperty('settings');
    expect(JSON.stringify(body)).not.toContain('auto_start_runs');
  });

  it('keeps the choice, shows the error and keeps Speichern enabled after a failed save', async () => {
    await open(orgFixture);
    vi.mocked(apiClient.put).mockRejectedValueOnce(new ApiError(500, 'Speichern fehlgeschlagen'));
    await userEvent.click(screen.getByRole('switch', SWITCH));
    await userEvent.click(saveButton());

    expect(await screen.findByText('Speichern fehlgeschlagen')).toBeInTheDocument();
    expect(screen.getByRole('switch', SWITCH)).not.toBeChecked();
    expect(screen.getByText(OFF_TEXT)).toBeInTheDocument();
    expect(saveButton()).toBeEnabled();
  });

  it('ties the line to the switch with aria-describedby', async () => {
    await open(orgFixture);
    const input = screen.getByRole('switch', SWITCH);
    const id = input.getAttribute('aria-describedby');
    expect(id).toBeTruthy();
    expect(document.getElementById(id as string)).toHaveTextContent(ON_TEXT);
    await userEvent.click(input);
    expect(document.getElementById(id as string)).toHaveTextContent(OFF_TEXT);
  });

  it('has no axe violations with the switch off', async () => {
    const { container } = await open(orgFixture);
    await userEvent.click(screen.getByRole('switch', SWITCH));
    expect(await axe(container)).toHaveNoViolations();
  });
});
