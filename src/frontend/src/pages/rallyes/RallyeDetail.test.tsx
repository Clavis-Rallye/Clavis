import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route, useSearchParams } from 'react-router';
import { axe } from 'vitest-axe';
import { ThemeProvider } from '@mui/material/styles';
import RallyeDetail from './RallyeDetail';
import theme from '../../theme';
import { AuthContext } from '../../auth/AuthContext';
import apiClient from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const user = {
  id: 'u1',
  email: 'admin@example.com',
  display_name: 'Admin',
  role: 'org_admin' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const rallyeFixture = (overrides: Partial<Record<string, unknown>> = {}) => ({
  id: 'r1',
  organization_id: 'org-1',
  title: { de: 'Meine Rallye' },
  description: { de: 'Eine Beschreibung' },
  is_published: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  ...overrides,
});

// A RallyeStationLink wrapping a library Station (no rallye_id/sort_order on
// the Station itself; the link carries sort_order).
const linkFixture = (stationId: string, title: string, order: number) => ({
  link_id: `link-${stationId}`,
  rallye_id: 'r1',
  sort_order: order,
  challenge_count: 0,
  total_points: 0,
  total_duration_minutes: 0,
  station: {
    id: stationId,
    organization_id: 'org-1',
    title: { de: title },
    description: { de: '' },
    address: null,
    location_lat: null,
    location_lng: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    usage: {
      rallye_ids: ['r1'],
      rallye_titles: [{ de: 'Meine Rallye' }],
    },
  },
});

// Renders the ?rallyeId= a station link carried, so a test can assert on it
// without needing StationDetail's own dependencies (MapPicker, RichTextEditor).
const StationRoutePlaceholder = () => {
  const [params] = useSearchParams();
  return <div>Station detail page rallyeId={params.get('rallyeId')}</div>;
};

// Wrapped in the real ThemeProvider: the contrast assertion below is only
// meaningful when the palette the component reads is the shipped one.
const renderDetail = (
  initialEntry: string | { pathname: string; search?: string; state?: unknown } = '/rallyes/r1',
) =>
  render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <AuthContext.Provider
          value={{
            user,
            isAuthenticated: true,
            isLoading: false,
            login: vi.fn(),
            logout: vi.fn(),
            checkAuth: vi.fn(),
            needsTwoFactorSetup: () => false,
          }}
        >
          <Routes>
            <Route path="/rallyes/:rallyeId" element={<RallyeDetail />} />
            <Route path="/rallyes" element={<div>List page</div>} />
            <Route path="/rallyes/r2" element={<div>New copy detail</div>} />
            <Route path="/stations/:stationId" element={<StationRoutePlaceholder />} />
          </Routes>
        </AuthContext.Provider>
      </MemoryRouter>
    </ThemeProvider>,
  );

describe('RallyeDetail', () => {
  beforeEach(() => vi.resetAllMocks());

  it('loads rallye + linked stations and renders them by sort_order', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({
        data: {
          // Returned out of order; component sorts by sort_order.
          items: [linkFixture('s1', 'Alpha', 1), linkFixture('s2', 'Bravo', 0)],
          total: 2,
        },
      });

    renderDetail();

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Meine Rallye' })).toBeInTheDocument();
    });
    // sort_order: Bravo=0, Alpha=1 → Bravo 1st, Alpha 2nd
    const bravo = screen.getByText('Bravo');
    const alpha = screen.getByText('Alpha');
    expect(bravo).toBeInTheDocument();
    expect(alpha).toBeInTheDocument();
    // Bravo renders before Alpha in the DOM (sorted by sort_order).
    expect(bravo.compareDocumentPosition(alpha) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  // The publish switch is a field of the form. It moves a local
  // value and "Speichern" sends it.
  describe('publish switch is saved with "Speichern"', () => {
    const emptyLinks = { data: { items: [], total: 0, skip: 0, limit: 100 } };
    const open = async (overrides: Partial<Record<string, unknown>> = {}) => {
      vi.mocked(apiClient.get)
        .mockResolvedValueOnce({ data: rallyeFixture(overrides) })
        .mockResolvedValueOnce(emptyLinks);
      renderDetail();
      await waitFor(() =>
        expect(screen.getByRole('heading', { name: 'Meine Rallye' })).toBeInTheDocument(),
      );
    };
    // The chip stands under the page heading, apart from the switch's label.
    const head = () =>
      within(screen.getByRole('heading', { name: 'Meine Rallye' }).parentElement as HTMLElement);
    const toggle = () => screen.getByRole('switch', { name: 'Veröffentlicht' });
    const saveButton = () => screen.getByRole('button', { name: 'Speichern' });

    it('switch on sends nothing; "Speichern" sends the publication, the notice names it and the chip follows', async () => {
      await open();
      vi.mocked(apiClient.put).mockResolvedValueOnce({
        data: rallyeFixture({ is_published: true }),
      });
      expect(head().getByText('Entwurf')).toBeInTheDocument();
      expect(saveButton()).toBeDisabled();

      await userEvent.click(toggle());

      expect(toggle()).toBeChecked();
      expect(apiClient.put).not.toHaveBeenCalled();
      expect(saveButton()).toBeEnabled();
      // The chip shows what is saved, the text under the switch follows the switch.
      expect(head().getByText('Entwurf')).toBeInTheDocument();
      expect(screen.getByText(/nur für Erstellung und Verwaltung sichtbar/i)).toBeInTheDocument();

      await userEvent.click(saveButton());

      await waitFor(() => expect(apiClient.put).toHaveBeenCalledTimes(1));
      expect(apiClient.put).toHaveBeenCalledWith('/api/orgs/test-org/rallyes/r1', {
        title: { de: 'Meine Rallye' },
        description: { de: 'Eine Beschreibung' },
        is_published: true,
      });
      expect(await screen.findByText('Die Rallye ist jetzt veröffentlicht.')).toBeInTheDocument();
      expect(head().getByText('Veröffentlicht')).toBeInTheDocument();
      expect(saveButton()).toBeDisabled();
    });

    it('switch on and off again leaves "Speichern" grey and sends nothing', async () => {
      await open();

      await userEvent.click(toggle());
      expect(saveButton()).toBeEnabled();
      await userEvent.click(toggle());

      expect(toggle()).not.toBeChecked();
      expect(saveButton()).toBeDisabled();
      expect(screen.getByText(/kann die Spielleitung Läufe/i)).toBeInTheDocument();
      expect(apiClient.put).not.toHaveBeenCalled();
    });

    it('a changed title and the switch go out in one request', async () => {
      await open();
      vi.mocked(apiClient.put).mockResolvedValueOnce({
        data: rallyeFixture({ title: { de: 'Meine Rallye neu' }, is_published: true }),
      });

      await userEvent.type(screen.getByDisplayValue('Meine Rallye'), ' neu');
      await userEvent.click(toggle());
      await userEvent.click(saveButton());

      await waitFor(() => expect(apiClient.put).toHaveBeenCalledTimes(1));
      expect(apiClient.put).toHaveBeenCalledWith(
        '/api/orgs/test-org/rallyes/r1',
        expect.objectContaining({ title: { de: 'Meine Rallye neu' }, is_published: true }),
      );
    });

    it('a save that leaves the publication alone gives the plain saved notice', async () => {
      await open();
      vi.mocked(apiClient.put).mockResolvedValueOnce({
        data: rallyeFixture({ title: { de: 'Meine Rallye neu' } }),
      });

      await userEvent.type(screen.getByDisplayValue('Meine Rallye'), ' neu');
      await userEvent.click(saveButton());

      expect(await screen.findByText('Gespeichert.')).toBeInTheDocument();
      expect(apiClient.put).toHaveBeenCalledWith(
        '/api/orgs/test-org/rallyes/r1',
        expect.objectContaining({ is_published: false }),
      );
    });

    it('the back link asks before it drops a moved switch, as for a changed title', async () => {
      await open();

      await userEvent.click(toggle());
      await userEvent.click(screen.getByRole('link', { name: /Zurück zu Rallyes/ }));

      const dialog = await screen.findByRole('dialog', { name: 'Änderungen verwerfen?' });
      await userEvent.click(within(dialog).getByRole('button', { name: 'Auf der Seite bleiben' }));
      await waitFor(() =>
        expect(screen.queryByRole('dialog', { name: 'Änderungen verwerfen?' })).toBeNull(),
      );
      expect(toggle()).toBeChecked();

      // Switch back: nothing is unsaved, the link leaves without a question.
      await userEvent.click(toggle());
      await userEvent.type(screen.getByDisplayValue('Meine Rallye'), ' neu');
      await userEvent.click(screen.getByRole('link', { name: /Zurück zu Rallyes/ }));
      const again = await screen.findByRole('dialog', { name: 'Änderungen verwerfen?' });
      await userEvent.click(within(again).getByRole('button', { name: 'Verwerfen und verlassen' }));
      expect(await screen.findByText('List page')).toBeInTheDocument();
    });

    it('the back link leaves at once when nothing is unsaved', async () => {
      await open();
      await userEvent.click(screen.getByRole('link', { name: /Zurück zu Rallyes/ }));
      expect(await screen.findByText('List page')).toBeInTheDocument();
    });

    it('switching a published rallye off and saving says it is no longer published', async () => {
      await open({ is_published: true });
      vi.mocked(apiClient.put).mockResolvedValueOnce({
        data: rallyeFixture({ is_published: false }),
      });

      await userEvent.click(toggle());
      expect(head().getByText('Veröffentlicht')).toBeInTheDocument();
      await userEvent.click(saveButton());

      expect(
        await screen.findByText('Die Rallye ist jetzt nicht mehr veröffentlicht.'),
      ).toBeInTheDocument();
      expect(apiClient.put).toHaveBeenCalledWith(
        '/api/orgs/test-org/rallyes/r1',
        expect.objectContaining({ is_published: false }),
      );
      expect(head().getByText('Entwurf')).toBeInTheDocument();
    });

    it('a failed save keeps the switch, shows the error and leaves "Speichern" active', async () => {
      await open();
      vi.mocked(apiClient.put).mockRejectedValueOnce(new Error('Speichern fehlgeschlagen'));

      await userEvent.click(toggle());
      await userEvent.click(saveButton());

      expect(await screen.findByText(/Speichern fehlgeschlagen/)).toBeInTheDocument();
      expect(toggle()).toBeChecked();
      expect(saveButton()).toBeEnabled();
      expect(head().getByText('Entwurf')).toBeInTheDocument();
    });
  });

  it('publish switch names the state, not the action, in both positions', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({ data: { items: [], total: 0, skip: 0, limit: 100 } });

    renderDetail();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Meine Rallye' })).toBeInTheDocument(),
    );

    const toggle = screen.getByRole('switch', { name: 'Veröffentlicht' });
    expect(toggle).not.toBeChecked();

    await userEvent.click(toggle);

    // Same label after the flip — the switch position carries on/off.
    expect(screen.getByRole('switch', { name: 'Veröffentlicht' })).toBeChecked();
  });

  // The consequence of switching off is stated in helper text.
  it('states what switching the publish toggle off costs', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture({ is_published: true }) })
      .mockResolvedValueOnce({ data: { items: [], total: 0, skip: 0, limit: 100 } });

    renderDetail();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Meine Rallye' })).toBeInTheDocument(),
    );

    expect(
      screen.getByText(/nur für Erstellung und Verwaltung sichtbar/i),
    ).toBeInTheDocument();
  });

  // The action and its confirmation say "Archivieren", not "Löschen".
  it('archive confirm → DELETE then navigates back to list', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({ data: { items: [], total: 0, skip: 0, limit: 100 } });

    vi.mocked(apiClient.delete).mockResolvedValueOnce({ data: undefined });

    renderDetail();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Meine Rallye' })).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByRole('button', { name: /archivieren/i }));
    // ConfirmDialog opens — primary action is "Archivieren" (from content archive.archive)
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/archivierte anzeigen/i)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: /archivieren/i }));

    await waitFor(() => {
      expect(apiClient.delete).toHaveBeenCalledWith('/api/orgs/test-org/rallyes/r1');
    });
    await waitFor(() => {
      expect(screen.getByText('List page')).toBeInTheDocument();
    });
  });

  // The rallye form hints where the content languages are set, with a
  // settings link for org admins only.
  it('hints where the content languages are set, with a settings link for org admins', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({ data: { items: [], total: 0, skip: 0, limit: 100 } });

    renderDetail();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Meine Rallye' })).toBeInTheDocument(),
    );

    expect(screen.getByText(/Inhaltssprachen/i)).toBeInTheDocument();
    const link = screen.getByRole('link', { name: 'Zu den Einstellungen' });
    expect(link).toHaveAttribute('href', '/admin/settings');
  });

  it('hides the settings link for a non org-admin role', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({ data: { items: [], total: 0, skip: 0, limit: 100 } });

    render(
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={['/rallyes/r1']}>
          <AuthContext.Provider
            value={{
              user: { ...user, role: 'creator' },
              isAuthenticated: true,
              isLoading: false,
              login: vi.fn(),
              logout: vi.fn(),
              checkAuth: vi.fn(),
              needsTwoFactorSetup: () => false,
            }}
          >
            <Routes>
              <Route path="/rallyes/:rallyeId" element={<RallyeDetail />} />
            </Routes>
          </AuthContext.Provider>
        </MemoryRouter>
      </ThemeProvider>,
    );
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Meine Rallye' })).toBeInTheDocument(),
    );

    expect(screen.getByText(/Inhaltssprachen/i)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Zu den Einstellungen' })).not.toBeInTheDocument();
  });

  it('duplicate confirm → POST duplicate and navigates to new rallye', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({ data: { items: [], total: 0, skip: 0, limit: 100 } })
      // After navigate to r2, the new route fetches afresh
      .mockResolvedValueOnce({ data: rallyeFixture({ id: 'r2', title: { de: 'Meine Rallye (Kopie)' } }) })
      .mockResolvedValueOnce({ data: { items: [], total: 0, skip: 0, limit: 100 } });

    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: rallyeFixture({ id: 'r2', title: { de: 'Meine Rallye (Kopie)' } }),
    });

    renderDetail();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Meine Rallye' })).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByRole('button', { name: /duplizieren/i }));
    const dialog = await screen.findByRole('dialog');
    // The dialog says that stations and tasks are shared, and its button names the action.
    expect(within(dialog).getByText(/ändert sie in beiden Rallyes/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Duplizieren' }));

    await waitFor(() => {
      expect(apiClient.post).toHaveBeenCalledWith(
        '/api/orgs/test-org/rallyes/r1/duplicate',
      );
    });
    await waitFor(() => {
      expect(screen.getByText('New copy detail')).toBeInTheDocument();
    });
  });

  it('save button disabled when form not dirty', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({ data: { items: [], total: 0, skip: 0, limit: 100 } });

    renderDetail();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Meine Rallye' })).toBeInTheDocument(),
    );

    expect(screen.getByRole('button', { name: 'Speichern' })).toBeDisabled();
  });

  it('expands a station and loads its matched challenges from the rallye-station endpoint', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({
        data: { items: [linkFixture('s1', 'Alpha', 0)], total: 1 },
      })
      // Challenge list for the expanded rallye-station (link_id "link-s1")
      .mockResolvedValueOnce({
        data: {
          items: [
            {
              link_id: 'cl1',
              rallye_station_id: 'link-s1',
              sort_order: 0,
              challenge: {
                id: 'c1',
                organization_id: 'org-1',
                challenge_type: 'text',
                title: { de: 'Rätsel' },
                instructions: { de: '' },
                scoring_rules: { max_points: 10 },
                time_estimate_minutes: 5,
                suggested_solution: null,
                created_at: '2026-01-01T00:00:00Z',
                updated_at: '2026-01-01T00:00:00Z',
              },
            },
          ],
          total: 1,
        },
      });

    renderDetail();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Meine Rallye' })).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByRole('button', { name: /Aufgaben anzeigen/i }));

    await waitFor(() => {
      expect(apiClient.get).toHaveBeenCalledWith(
        '/api/orgs/test-org/rallyes/r1/stations/link-s1/challenges',
      );
    });
    await waitFor(() => {
      expect(screen.getByText('1. Rätsel')).toBeInTheDocument();
    });
    // A task in the rallye view has an edit button, like its station.
    expect(screen.getByRole('button', { name: 'Aufgabe bearbeiten' })).toBeInTheDocument();
  });

  // Expanding a challenge row shows the task text
  // read-only inline, never an editable control.
  it('expanding a challenge row shows the task text, and nothing in the preview is editable', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({
        data: { items: [linkFixture('s1', 'Alpha', 0)], total: 1 },
      })
      // Challenge list for the expanded rallye-station, with a task text in German
      .mockResolvedValueOnce({
        data: {
          items: [
            {
              link_id: 'cl1',
              rallye_station_id: 'link-s1',
              sort_order: 0,
              challenge: {
                id: 'c1',
                organization_id: 'org-1',
                challenge_type: 'text',
                title: { de: 'Rätsel' },
                instructions: { de: '<p>Finde den Brunnen am Rathausplatz.</p>', en: '' },
                scoring_rules: { max_points: 10 },
                time_estimate_minutes: 5,
                suggested_solution: null,
                created_at: '2026-01-01T00:00:00Z',
                updated_at: '2026-01-01T00:00:00Z',
              },
            },
          ],
          total: 1,
        },
      });

    renderDetail();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Meine Rallye' })).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByRole('button', { name: /Aufgaben anzeigen/i }));
    await waitFor(() => {
      expect(screen.getByText('1. Rätsel')).toBeInTheDocument();
    });

    // Collapsed: the task text is not on screen yet.
    expect(screen.queryByText('Finde den Brunnen am Rathausplatz.')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Aufgabentext anzeigen/i }));

    expect(await screen.findByText('Finde den Brunnen am Rathausplatz.')).toBeInTheDocument();

    // Read-only: inside the rendered task text there is no button, no input,
    // no contentEditable — the preview only ever displays stored content.
    const preview = screen.getByText('Finde den Brunnen am Rathausplatz.').closest(
      'div',
    ) as HTMLElement;
    expect(within(preview).queryByRole('button')).toBeNull();
    expect(within(preview).queryByRole('textbox')).toBeNull();
    expect(preview.querySelector('input, textarea, select, [contenteditable]')).toBeNull();
  });

  // When the current content language has no task text, the
  // preview falls back to the first language that has one.
  it('task preview falls back to the first language that has text', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({
        data: { items: [linkFixture('s1', 'Alpha', 0)], total: 1 },
      })
      .mockResolvedValueOnce({
        data: {
          items: [
            {
              link_id: 'cl1',
              rallye_station_id: 'link-s1',
              sort_order: 0,
              challenge: {
                id: 'c1',
                organization_id: 'org-1',
                challenge_type: 'text',
                title: { de: 'Rätsel' },
                instructions: { de: '', en: '', fr: '<p>Trouve la fontaine.</p>' },
                scoring_rules: { max_points: 10 },
                time_estimate_minutes: 5,
                suggested_solution: null,
                created_at: '2026-01-01T00:00:00Z',
                updated_at: '2026-01-01T00:00:00Z',
              },
            },
          ],
          total: 1,
        },
      });

    renderDetail();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Meine Rallye' })).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByRole('button', { name: /Aufgaben anzeigen/i }));
    await waitFor(() => {
      expect(screen.getByText('1. Rätsel')).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole('button', { name: /Aufgabentext anzeigen/i }));

    expect(await screen.findByText('Trouve la fontaine.')).toBeInTheDocument();
  });

  // The create-and-link round trip from ChallengeCreate lands back
  // here with ?open={link_id}: the station stays expanded instead of
  // landing collapsed, and its challenges are fetched without a click.
  it('auto-expands the station named in ?open= and loads its challenges', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({
        data: { items: [linkFixture('s1', 'Alpha', 0)], total: 1 },
      })
      .mockResolvedValueOnce({
        data: {
          items: [
            {
              link_id: 'cl1',
              rallye_station_id: 'link-s1',
              sort_order: 0,
              challenge: {
                id: 'c1',
                organization_id: 'org-1',
                challenge_type: 'text',
                title: { de: 'Rätsel' },
                instructions: { de: '' },
                scoring_rules: { max_points: 10 },
                time_estimate_minutes: 5,
                suggested_solution: null,
                created_at: '2026-01-01T00:00:00Z',
                updated_at: '2026-01-01T00:00:00Z',
              },
            },
          ],
          total: 1,
        },
      });

    renderDetail('/rallyes/r1?open=link-s1');

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Meine Rallye' })).toBeInTheDocument(),
    );

    // No click on "Aufgaben anzeigen" — the station opens on its own.
    await waitFor(() => {
      expect(apiClient.get).toHaveBeenCalledWith(
        '/api/orgs/test-org/rallyes/r1/stations/link-s1/challenges',
      );
    });
    expect(await screen.findByText('1. Rätsel')).toBeInTheDocument();
  });

  // The ?open= auto-expand must apply once, not pin the
  // station open: collapsing it afterwards has to stick.
  it('a station opened via ?open= can still be collapsed, and stays collapsed', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({
        data: { items: [linkFixture('s1', 'Alpha', 0)], total: 1 },
      })
      .mockResolvedValueOnce({
        data: {
          items: [
            {
              link_id: 'cl1',
              rallye_station_id: 'link-s1',
              sort_order: 0,
              challenge: {
                id: 'c1',
                organization_id: 'org-1',
                challenge_type: 'text',
                title: { de: 'Rätsel' },
                instructions: { de: '' },
                scoring_rules: { max_points: 10 },
                time_estimate_minutes: 5,
                suggested_solution: null,
                created_at: '2026-01-01T00:00:00Z',
                updated_at: '2026-01-01T00:00:00Z',
              },
            },
          ],
          total: 1,
        },
      });

    renderDetail('/rallyes/r1?open=link-s1');

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Meine Rallye' })).toBeInTheDocument(),
    );
    expect(await screen.findByText('1. Rätsel')).toBeInTheDocument();

    const toggle = screen.getByRole('button', { name: /Aufgaben anzeigen/i });
    await userEvent.click(toggle);

    // Collapsed right after the click — the ?open= effect must not re-fire
    // and pin it back open.
    expect(screen.queryByText('1. Rätsel')).not.toBeInTheDocument();
    // And it stays collapsed — no later re-expand from the same effect.
    await waitFor(() => {
      expect(screen.queryByText('1. Rätsel')).not.toBeInTheDocument();
    });
  });

  // WCAG 2.4.6: two rows must not share a button name.
  it('the remove and edit buttons of two stations name their station', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({
        data: { items: [linkFixture('s1', 'Alpha', 0), linkFixture('s2', 'Bravo', 1)], total: 2 },
      });
    renderDetail();
    await screen.findByText('Alpha');

    const names = screen
      .getAllByRole('button')
      .map((b) => b.getAttribute('aria-label') ?? '')
      .filter((n) => /^Station (entfernen|bearbeiten)/.test(n));
    expect(names.sort()).toEqual([
      'Station bearbeiten: Alpha',
      'Station bearbeiten: Bravo',
      'Station entfernen: Alpha',
      'Station entfernen: Bravo',
    ]);
    expect(new Set(names).size).toBe(names.length);
  });

  // At 320 px the five action buttons would take the whole row and leave the
  // name 0 px. jsdom lays nothing out, so the test reads the rule:
  // below sm the row wraps and the actions take their own line under the name.
  it('the actions of a station row wrap under the name below sm', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({ data: { items: [linkFixture('s1', 'Alpha', 0)], total: 1 } });
    renderDetail();
    const heading = await screen.findByRole('heading', { level: 3, name: 'Alpha' });
    const head = heading.parentElement!.parentElement!;
    const actions = screen.getByRole('button', { name: 'Station entfernen: Alpha' }).parentElement!;
    // Order for the keyboard: the name, then the actions, the drag handle last.
    expect(head.contains(actions)).toBe(true);
    expect(within(actions).getAllByRole('button').at(-1)).toHaveAccessibleName('Reihenfolge ändern');

    const css = Array.from(document.querySelectorAll('style'))
      .map((s) => s.textContent ?? '')
      .join('\n')
      .replace(/\s+/g, '');
    const rule = (el: Element) => {
      const cls = Array.from(el.classList).find((c) => c.startsWith('css-'))!;
      const at = (min: number) =>
        css.match(new RegExp(`@media\\(min-width:${min}px\\)\\{\\.${cls}\\{([^}]*)\\}`))?.[1] ?? '';
      return { base: at(0), sm: at(600) };
    };
    expect(rule(head).base).toContain('flex-wrap:wrap');
    expect(rule(head).sm).toContain('flex-wrap:nowrap');
    expect(rule(actions).base).toContain('flex-basis:100%');
    expect(rule(actions).sm).toContain('flex-basis:auto');
  });

  // Opening a station from the rallye carries ?rallyeId= so the
  // station links back here (StationDetail reads it).
  it('opening a station via its edit icon carries ?rallyeId= back to this rallye', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({
        data: { items: [linkFixture('s1', 'Alpha', 0)], total: 1 },
      });

    renderDetail();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Meine Rallye' })).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByRole('button', { name: 'Station bearbeiten: Alpha' }));

    expect(await screen.findByText('Station detail page rallyeId=r1')).toBeInTheDocument();
  });

  // A station in more than one rallye shows the
  // count and offers its own copy for this rallye; a station only here shows
  // neither.
  it('marks a shared station and gives this rallye its own copy', async () => {
    const shared = linkFixture('s1', 'Rathaus', 0);
    (shared.station as Record<string, unknown>).usage_count = 3;
    const only = linkFixture('s2', 'Perlach', 1);
    (only.station as Record<string, unknown>).usage_count = 1;
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({ data: { items: [shared, only], total: 2 } });
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { ...shared, station: { ...shared.station, id: 's1-copy' } },
    });

    renderDetail();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Meine Rallye' })).toBeInTheDocument(),
    );

    expect(screen.getByText('3 Rallyes')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Als Kopie bearbeiten' })).toHaveLength(1);

    await userEvent.click(screen.getByRole('button', { name: 'Als Kopie bearbeiten' }));

    // It asks first, and creates nothing until the answer.
    const copyDialog = await screen.findByRole('dialog', { name: 'Als Kopie bearbeiten' });
    expect(apiClient.post).not.toHaveBeenCalled();
    await userEvent.click(within(copyDialog).getByRole('button', { name: 'Kopie anlegen' }));

    expect(apiClient.post).toHaveBeenCalledWith('/api/orgs/test-org/rallyes/r1/stations/link-s1/copy');
    expect(await screen.findByText('Station detail page rallyeId=r1')).toBeInTheDocument();
  });

  it('has no axe accessibility violations on initial render', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({ data: { items: [], total: 0, skip: 0, limit: 100 } });

    const { container } = renderDetail();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Meine Rallye' })).toBeInTheDocument(),
    );
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  // The stat tiles print their number in the on-surface text colour and
  // keep the accent on the tile's edge.
  //
  // The axe call is the contrast check, but under jsdom `color-contrast`
  // can never fail: elements have no layout, so axe files the rule under
  // `incomplete`, not `violations`. The computed-colour
  // assertion above it is what actually fails when the accent returns to the
  // number. Real contrast is measured in the Playwright a11y pass.
  it('has no color-contrast violations on the stat tiles', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({ data: { items: [], total: 0, skip: 0, limit: 100 } });

    const { container } = renderDetail();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Meine Rallye' })).toBeInTheDocument(),
    );

    // The number next to the "Stationen" caption, in palette.text.primary.
    const tileNumber = screen.getByText('Stationen').nextElementSibling as HTMLElement;
    expect(window.getComputedStyle(tileNumber).color).toBe('rgb(29, 27, 32)');

    const results = await axe(container, { runOnly: ['color-contrast'] });
    expect(results).toHaveNoViolations();
  });

  it('names each "Aufgaben anzeigen" button with the title of its station', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({
        data: { items: [linkFixture('s1', 'Alpha', 0), linkFixture('s2', 'Bravo', 1)], total: 2 },
      });
    renderDetail();
    expect(
      await screen.findByRole('button', { name: 'Aufgaben anzeigen: Alpha' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Aufgaben anzeigen: Bravo' })).toBeInTheDocument();
  });

  it('says in the remove-station dialog that started runs keep their copy', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({ data: { items: [linkFixture('s1', 'Alpha', 0)], total: 1 } });
    renderDetail();
    await userEvent.click(await screen.findByRole('button', { name: 'Station entfernen: Alpha' }));
    const dialog = await screen.findByRole('dialog');
    expect(
      within(dialog).getByText(/Bereits gestartete Läufe behalten ihre Kopie\./),
    ).toBeInTheDocument();
  });

  it('says that the rallye was created when RallyeCreate navigated here', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({ data: { items: [], total: 0 } });
    renderDetail({ pathname: '/rallyes/r1', state: { created: true } });
    expect(await screen.findByText('Rallye erfolgreich erstellt')).toBeInTheDocument();
  });

  it('says that the challenge was created when ChallengeCreate navigated here', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({ data: { items: [], total: 0 } });
    renderDetail({ pathname: '/rallyes/r1', state: { challengeCreated: true } });
    expect(await screen.findByText('Aufgabe erstellt.')).toBeInTheDocument();
  });

  it('shows no creation notice on a plain visit', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({ data: { items: [], total: 0 } });
    renderDetail();
    await screen.findByRole('heading', { name: 'Meine Rallye' });
    expect(screen.queryByText('Rallye erfolgreich erstellt')).not.toBeInTheDocument();
    expect(screen.queryByText('Aufgabe erstellt.')).not.toBeInTheDocument();
  });
});


// Typography subtitle1 renders h6 by the MUI default, so the station name sets
// h3 to sit under the page's h1 and the section's h2.
describe('RallyeDetail — heading levels', () => {
  beforeEach(() => vi.resetAllMocks());

  // No heading skips a level, and the look stays: the station name keeps
  // subtitle1.
  it('jumps no heading level: the station name is an h3 under the section h2', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: rallyeFixture() })
      .mockResolvedValueOnce({
        data: { items: [linkFixture('s1', 'Alpha', 1), linkFixture('s2', 'Bravo', 0)], total: 2 },
      });

    const { container } = renderDetail();
    await waitFor(() => expect(screen.getByText('Alpha')).toBeInTheDocument());

    const levels = Array.from(container.querySelectorAll('h1,h2,h3,h4,h5,h6')).map(
      (h) => Number(h.tagName[1]),
    );
    expect(levels).toEqual([1, 2, 3, 3]);
    expect(container.querySelector('h3')).toHaveClass('MuiTypography-subtitle1');
  });
});
