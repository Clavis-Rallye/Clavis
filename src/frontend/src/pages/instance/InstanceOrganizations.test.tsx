import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import { ThemeProvider } from '@mui/material/styles';
import InstanceOrganizations from './InstanceOrganizations';
import theme from '../../theme';
import apiClient, { ApiError } from '../../api/client';
import i18n from '../../i18n';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const meResponse = { id: 'admin-1', email: 'operator@example.com', totp_confirmed: true };

const orgsResponse = {
  items: [
    {
      name: 'Stadtverwaltung Augsburg',
      slug: 'stadtverwaltung-augsburg',
      content_languages: ['de'],
      created_at: '2026-01-01T00:00:00Z',
      is_archived: false,
      user_count: 12,
      rallye_count: 3,
      run_count: 5,
      org_admin_count: 2,
      pending_admin_invitation_count: 1,
      active_run_count: 4,
      last_activity_at: '2026-03-01T10:00:00Z',
    },
    {
      name: 'Retired Org',
      slug: 'retired-org',
      content_languages: ['de', 'en'],
      created_at: '2026-02-01T00:00:00Z',
      is_archived: true,
      user_count: 4,
      rallye_count: 1,
      run_count: 0,
      org_admin_count: 1,
      pending_admin_invitation_count: 0,
      // A run that was active when the organization was archived.
      active_run_count: 7,
      last_activity_at: null,
    },
  ],
  total: 2,
};

// A scheduled deletion adds `deletion_due_at` to the archived row.
const orgsResponseWithScheduledDeletion = {
  items: orgsResponse.items.map((o) =>
    o.slug === 'retired-org' ? { ...o, deletion_due_at: '2026-03-03T00:00:00Z' } : o,
  ),
  total: 2,
};

const renderPage = () =>
  render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={['/instance']}>
        <Routes>
          <Route path="/instance" element={<InstanceOrganizations />} />
          <Route path="/instance/login" element={<div>Operator login</div>} />
          <Route path="/login" element={<div>Regular login</div>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );

const mockAuthenticated = (orgs: typeof orgsResponse = orgsResponse) => {
  vi.mocked(apiClient.get).mockImplementation((url: string) => {
    if (url === '/api/instance/auth/me') return Promise.resolve({ data: meResponse });
    if (url === '/api/instance/organizations') return Promise.resolve({ data: orgs });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
};

describe('InstanceOrganizations', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  // WCAG 2.4.6: headings and labels describe their purpose.
  it('the row buttons of the organizations name their organization and are unique', async () => {
    mockAuthenticated();
    renderPage();
    await screen.findByText('Stadtverwaltung Augsburg');
    await userEvent.click(screen.getByRole('switch', { name: 'Archivierte anzeigen' }));

    const names = (row: HTMLElement) =>
      within(row)
        .getAllByRole('button')
        .map((b) => b.getAttribute('aria-label') ?? '');
    const active = names(screen.getByText('Stadtverwaltung Augsburg').closest('tr')!);
    const archived = names(screen.getByText('Retired Org').closest('tr')!);
    expect(active).toEqual(
      expect.arrayContaining([
        'Bearbeiten: Stadtverwaltung Augsburg',
        'Verwaltung einladen: Stadtverwaltung Augsburg',
        'Archivieren: Stadtverwaltung Augsburg',
      ]),
    );
    expect(archived).toEqual(
      expect.arrayContaining(['Bearbeiten: Retired Org', 'Löschen: Retired Org']),
    );
    const all = [...active, ...archived].filter((n) => /^(Bearbeiten|Verwaltung|Archivieren|Löschen)/.test(n));
    expect(new Set(all).size).toBe(all.length);
  });

  // The column shows the later of the last run start and the last answer, so
  // "played" would be wrong for a run nobody had answered in.
  describe('the activity column header', () => {
    afterEach(async () => {
      await i18n.changeLanguage('de');
    });

    it.each([
      ['de', 'Letzte Aktivität'],
      ['en', 'Last activity'],
    ])('in %s reads "%s" and nowhere says "Zuletzt gespielt"', async (lng, header) => {
      await i18n.changeLanguage(lng);
      mockAuthenticated();
      renderPage();

      await screen.findByText('Stadtverwaltung Augsburg');
      expect(screen.getByRole('columnheader', { name: new RegExp(header) })).toBeInTheDocument();
      expect(screen.queryByText(/Zuletzt gespielt/)).toBeNull();
      expect(screen.queryByText(/Last played/)).toBeNull();
    });
  });

  it('renders the table with counts, activity and the archived chip once the switch is on', async () => {
    mockAuthenticated();
    renderPage();

    await waitFor(() => expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument());
    const activeRow = screen.getByText('Stadtverwaltung Augsburg').closest('tr')!;
    expect(within(activeRow).getByText('12')).toBeInTheDocument();
    expect(within(activeRow).getByText('3')).toBeInTheDocument();
    expect(within(activeRow).getByText('5')).toBeInTheDocument();
    expect(within(activeRow).getByText('2')).toBeInTheDocument();
    expect(within(activeRow).getByText('1')).toBeInTheDocument();
    expect(within(activeRow).getByText('4')).toBeInTheDocument();
    expect(within(activeRow).getByText(/03\.2026/)).toBeInTheDocument();
    expect(within(activeRow).getByText('Aktiv')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('switch', { name: 'Archivierte anzeigen' }));
    const archivedRow = screen.getByText('Retired Org').closest('tr')!;
    expect(within(archivedRow).getByText('Archiviert')).toBeInTheDocument();
    // No activity, and no count of active runs: nobody can reach a run of an
    // archived organization, so "7" would read as "still running".
    expect(within(archivedRow).getAllByText('–')).toHaveLength(2);
    expect(within(archivedRow).queryByText('7')).toBeNull();
    // Nobody can sign in there, so an invitation leads nowhere.
    expect(within(archivedRow).queryByRole('button', { name: 'Verwaltung einladen: Retired Org' })).toBeNull();
    expect(within(activeRow).getByRole('button', { name: 'Verwaltung einladen: Stadtverwaltung Augsburg' })).toBeInTheDocument();
  });

  // The archive dialog names the active runs. Without one, it says nothing about runs being active.
  it('the archive dialog warns about active runs, and only when there are some', async () => {
    mockAuthenticated();
    const view = renderPage();
    await waitFor(() => expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Archivieren: Stadtverwaltung Augsburg' }));
    expect(within(screen.getByRole('dialog')).getByText(/4 Läufe sind gerade aktiv/)).toBeInTheDocument();
    view.unmount();

    mockAuthenticated({
      ...orgsResponse,
      items: [{ ...orgsResponse.items[0], active_run_count: 0 }, orgsResponse.items[1]],
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Archivieren: Stadtverwaltung Augsburg' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(/Die Mitglieder können sich nicht mehr anmelden/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/gerade aktiv/)).toBeNull();
  });

  it('names one active run in the singular', async () => {
    mockAuthenticated({
      ...orgsResponse,
      items: [{ ...orgsResponse.items[0], active_run_count: 1 }, orgsResponse.items[1]],
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Archivieren: Stadtverwaltung Augsburg' }));
    expect(within(screen.getByRole('dialog')).getByText(/1 Lauf ist gerade aktiv/)).toBeInTheDocument();
  });

  it('the archived switch filters archived organizations out of the table by default', async () => {
    mockAuthenticated();
    renderPage();

    await waitFor(() => expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument());
    expect(screen.queryByText('Retired Org')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('switch', { name: 'Archivierte anzeigen' }));
    expect(screen.getByText('Retired Org')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('switch', { name: 'Archivierte anzeigen' }));
    expect(screen.queryByText('Retired Org')).not.toBeInTheDocument();
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

  it('archiving an organization asks for confirmation, then calls the archive endpoint', async () => {
    mockAuthenticated();
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { slug: 'stadtverwaltung-augsburg', is_archived: true, message: 'Organization archived' },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Archivieren: Stadtverwaltung Augsburg' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(/die mitglieder können sich nicht mehr anmelden/i)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Archivieren' }));

    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith(
        '/api/instance/organizations/stadtverwaltung-augsburg/archive',
      ),
    );
    await waitFor(() =>
      expect(screen.getByText(/Stadtverwaltung Augsburg.*wurde archiviert/)).toBeInTheDocument(),
    );
  });

  it('restoring an organization calls the restore endpoint', async () => {
    mockAuthenticated();
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { slug: 'retired-org', is_archived: false, message: 'Organization restored' },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('switch', { name: 'Archivierte anzeigen' }));
    await waitFor(() => expect(screen.getByText('Retired Org')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Wiederherstellen' }));
    const dialog = screen.getByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Wiederherstellen' }));

    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith('/api/instance/organizations/retired-org/restore'),
    );
  });

  // An archived row's "Löschen" action opens the delete
  // dialog, and the exact slug schedules the deletion via the API.
  it('deleting an archived organization asks for the slug, then calls the deletion endpoint', async () => {
    mockAuthenticated();
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { slug: 'retired-org', deletion_due_at: '2026-03-03T00:00:00Z', message: 'Organization deletion scheduled' },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('switch', { name: 'Archivierte anzeigen' }));
    await waitFor(() => expect(screen.getByText('Retired Org')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Löschen: Retired Org' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(/nach 30 tagen werden alle rallyes, läufe, konten und medien/i)).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText(/kurzname zur bestätigung/i), 'retired-org');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Löschung planen' }));

    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith(
        '/api/instance/organizations/retired-org/deletion',
        { confirm_slug: 'retired-org' },
      ),
    );
    await waitFor(() =>
      expect(screen.getByText(/löschung von „Retired Org“ ist für den.*geplant/i)).toBeInTheDocument(),
    );
  });

  // The "Löschen" action only appears on archived rows.
  it('does not offer the delete action on an active organization', async () => {
    mockAuthenticated();
    renderPage();
    await waitFor(() => expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument());

    expect(screen.queryByRole('button', { name: 'Löschen: Stadtverwaltung Augsburg' })).not.toBeInTheDocument();
  });

  // A scheduled deletion shows a due-date chip on the row,
  // and the restore action says it cancels the deletion instead of the
  // usual "Wiederherstellen" label.
  it('shows the deletion due chip and the cancel-deletion restore label for a scheduled deletion', async () => {
    mockAuthenticated(orgsResponseWithScheduledDeletion);
    renderPage();
    await waitFor(() => expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('switch', { name: 'Archivierte anzeigen' }));

    const archivedRow = screen.getByText('Retired Org').closest('tr')!;
    expect(within(archivedRow).getByText(/löschung am/i)).toBeInTheDocument();
    expect(within(archivedRow).getByRole('button', { name: 'Löschung abbrechen und wiederherstellen' })).toBeInTheDocument();
    expect(within(archivedRow).queryByRole('button', { name: 'Wiederherstellen' })).not.toBeInTheDocument();

    // The dialog names both effects, too.
    await userEvent.click(within(archivedRow).getByRole('button', { name: 'Löschung abbrechen und wiederherstellen' }));
    const dialog = await screen.findByRole('dialog', { name: 'Löschung abbrechen und wiederherstellen?' });
    expect(within(dialog).getByText(/geplante Löschung von Retired Org wird abgebrochen/)).toBeInTheDocument();
    // Not "Abbrechen" beside "Löschung abbrechen": two cancels with opposite effect.
    expect(within(dialog).getByRole('button', { name: 'Löschung beibehalten' })).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: 'Abbrechen' })).toBeNull();
  });

  // While a deletion is planned the row offers the cancel-and-restore
  // action and no "Löschen".
  it('does not offer "Löschen" on a row with a planned deletion', async () => {
    mockAuthenticated(orgsResponseWithScheduledDeletion);
    renderPage();
    await waitFor(() => expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('switch', { name: 'Archivierte anzeigen' }));

    const archivedRow = screen.getByText('Retired Org').closest('tr')!;
    expect(
      within(archivedRow).getByRole('button', { name: 'Löschung abbrechen und wiederherstellen' }),
    ).toBeInTheDocument();
    expect(within(archivedRow).queryByRole('button', { name: 'Löschen: Retired Org' })).not.toBeInTheDocument();
  });

  // A failed archive must not vanish behind the modal's
  // aria-hidden backdrop: it renders inside the still-open confirm dialog.
  it('shows the archive error inside the confirm dialog and keeps it open on failure', async () => {
    mockAuthenticated();
    vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(500, 'Server error'));
    renderPage();
    await waitFor(() => expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Archivieren: Stadtverwaltung Augsburg' }));
    const dialog = screen.getByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Archivieren' }));

    await waitFor(() =>
      expect(within(dialog).getByRole('alert')).toHaveTextContent(/fehlgeschlagen/i),
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(apiClient.post).toHaveBeenCalledTimes(1);
  });

  it('shows the restore error inside the confirm dialog and keeps it open on failure', async () => {
    mockAuthenticated();
    vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(500, 'Server error'));
    renderPage();
    await waitFor(() => expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('switch', { name: 'Archivierte anzeigen' }));
    await waitFor(() => expect(screen.getByText('Retired Org')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Wiederherstellen' }));
    const dialog = screen.getByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Wiederherstellen' }));

    await waitFor(() =>
      expect(within(dialog).getByRole('alert')).toHaveTextContent(/fehlgeschlagen/i),
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(apiClient.post).toHaveBeenCalledTimes(1);
  });

  // The confirm dialog's cancel button reads from the `instance`
  // namespace, same as every other string on the page.
  it('labels the confirm dialog cancel button from the instance namespace', async () => {
    mockAuthenticated();
    renderPage();
    await waitFor(() => expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Archivieren: Stadtverwaltung Augsburg' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('button', { name: 'Abbrechen' })).toBeInTheDocument();
  });

  // Security: the operator cookie is HttpOnly, so only the server clears it.
  // Sign-out must navigate away only once the server confirms the logout.
  it('signs out and navigates to the operator login once the server confirms it', async () => {
    mockAuthenticated();
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { message: 'Logout successful' } });
    renderPage();
    await waitFor(() => expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Abmelden' }));

    await waitFor(() => expect(apiClient.post).toHaveBeenCalledWith('/api/instance/auth/logout'));
    await waitFor(() => expect(screen.getByText('Operator login')).toBeInTheDocument());
  });

  it('stays on the page and shows an error when sign-out fails, never navigating away', async () => {
    mockAuthenticated();
    vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(500, 'Server error'));
    renderPage();
    await waitFor(() => expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Abmelden' }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/fehlgeschlagen/i));
    expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument();
    expect(screen.queryByText('Operator login')).not.toBeInTheDocument();
  });

  it('"Neue Organisation" opens the create dialog', async () => {
    mockAuthenticated();
    renderPage();
    await waitFor(() => expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Neue Organisation' }));
    expect(screen.getByRole('heading', { name: 'Neue Organisation anlegen' })).toBeInTheDocument();
  });

  // The row's edit action opens the edit dialog with the
  // organization's current name, languages and read-only slug.
  it('the row edit action opens the edit dialog prefilled with the organization', async () => {
    mockAuthenticated();
    renderPage();
    await waitFor(() => expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Bearbeiten: Stadtverwaltung Augsburg' }));
    expect(screen.getByRole('heading', { name: 'Organisation bearbeiten' })).toBeInTheDocument();
    expect(screen.getByLabelText(/name der organisation/i)).toHaveValue('Stadtverwaltung Augsburg');
    expect(screen.getByLabelText(/^kurzname/i)).toHaveValue('stadtverwaltung-augsburg');
    expect(screen.getByLabelText(/^kurzname/i)).toBeDisabled();
  });

  // The row's "Verwaltung einladen" action opens the invite dialog.
  it('the row "Verwaltung einladen" action opens the invite dialog', async () => {
    mockAuthenticated();
    renderPage();
    await waitFor(() => expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Verwaltung einladen: Stadtverwaltung Augsburg' }));
    expect(screen.getByRole('heading', { name: 'Verwaltung einladen' })).toBeInTheDocument();
    expect(screen.getByLabelText(/e-mail-adresse/i)).toBeInTheDocument();
  });

  it('has no axe accessibility violations once signed in', async () => {
    mockAuthenticated();
    const { container } = renderPage();
    await waitFor(() => expect(screen.getByText('Stadtverwaltung Augsburg')).toBeInTheDocument());

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
