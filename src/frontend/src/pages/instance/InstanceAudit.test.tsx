import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import { ThemeProvider } from '@mui/material/styles';
import InstanceAudit from './InstanceAudit';
import theme from '../../theme';
import apiClient, { ApiError } from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const meResponse = { id: 'admin-1', email: 'operator@example.com', totp_confirmed: true };

const auditResponse = {
  items: [
    {
      timestamp: '2026-03-01T10:00:00Z',
      event: 'instance.org_created',
      operator_id: 'admin-1',
      operator_email: 'operator@example.com',
      target_slug: 'stadtverwaltung-augsburg',
    },
    {
      timestamp: '2026-03-02T11:00:00Z',
      event: 'instance.org_archived',
      operator_id: 'admin-1',
      operator_email: 'operator@example.com',
      target_slug: 'retired-org',
    },
  ],
  total: 2,
  offset: 0,
  limit: 25,
};

const renderPage = () =>
  render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={['/instance/audit']}>
        <Routes>
          <Route path="/instance/audit" element={<InstanceAudit />} />
          <Route path="/instance/login" element={<div>Operator login</div>} />
          <Route path="/login" element={<div>Regular login</div>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );

const mockAuthenticated = () => {
  vi.mocked(apiClient.get).mockImplementation((url: string) => {
    if (url === '/api/instance/auth/me') return Promise.resolve({ data: meResponse });
    if (url === '/api/instance/audit') return Promise.resolve({ data: auditResponse });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
};

describe('InstanceAudit', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  // Newest first, time, event, operator and target slug, paged and filterable
  // by event.
  it('lists the instance.* audit events newest first with time, event, operator and target slug', async () => {
    mockAuthenticated();
    renderPage();

    await waitFor(() => expect(screen.getByText('Organisation angelegt')).toBeInTheDocument());
    const row = screen.getByText('Organisation angelegt').closest('tr')!;
    expect(within(row).getByText('operator@example.com')).toBeInTheDocument();
    expect(within(row).getByText('stadtverwaltung-augsburg')).toBeInTheDocument();

    await waitFor(() =>
      expect(apiClient.get).toHaveBeenCalledWith('/api/instance/audit', {
        params: { event: undefined, offset: 0, limit: 25 },
      }),
    );
  });

  // The time of a row shows its seconds, as the log of an organization does.
  it('shows the time of a row with its seconds', async () => {
    mockAuthenticated();
    renderPage();

    await waitFor(() => expect(screen.getByText('Organisation angelegt')).toBeInTheDocument());
    const row = screen.getByText('Organisation angelegt').closest('tr')!;
    expect(row.querySelector('td')!.textContent).toMatch(/\d{1,2}:\d{2}:\d{2}/);
  });

  it('filtering by event re-queries the API with the event param', async () => {
    mockAuthenticated();
    renderPage();
    await waitFor(() => expect(screen.getByText('Organisation angelegt')).toBeInTheDocument());

    await userEvent.click(screen.getByLabelText('Ereignis'));
    await userEvent.click(await screen.findByRole('option', { name: 'Organisation archiviert' }));

    await waitFor(() =>
      expect(apiClient.get).toHaveBeenLastCalledWith('/api/instance/audit', {
        params: { event: 'instance.org_archived', offset: 0, limit: 25 },
      }),
    );
  });

  // The filter offers the three deletion events, every event shows by a German
  // label, and the scheduled deletion, which has no operator, shows "System".
  it('labels the events in German, offers the deletion events and names the system actor', async () => {
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url === '/api/instance/auth/me') return Promise.resolve({ data: meResponse });
      if (url === '/api/instance/audit') {
        return Promise.resolve({
          data: {
            items: [
              {
                timestamp: '2026-03-03T03:00:00Z',
                event: 'instance.org_deleted',
                operator_id: null,
                operator_email: null,
                target_slug: 'gone-org',
              },
            ],
            total: 1,
            offset: 0,
            limit: 25,
          },
        });
      }
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });
    renderPage();

    await waitFor(() => expect(screen.getByText('Organisation gelöscht')).toBeInTheDocument());
    const row = screen.getByText('Organisation gelöscht').closest('tr')!;
    expect(within(row).getByText('System')).toBeInTheDocument();
    expect(screen.queryByText('instance.org_deleted')).not.toBeInTheDocument();

    await userEvent.click(screen.getByLabelText('Ereignis'));
    for (const name of ['Löschung geplant', 'Löschung abgebrochen', 'Organisation gelöscht']) {
      expect(await screen.findByRole('option', { name })).toBeInTheDocument();
    }
  });

  // A failed sign-in is named as not signed in, not as the system, because a
  // person at an address made the attempt.
  it('names a failed sign-in as not signed in, not as the system', async () => {
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url === '/api/instance/auth/me') return Promise.resolve({ data: meResponse });
      if (url === '/api/instance/audit') {
        return Promise.resolve({
          data: {
            items: [
              {
                timestamp: '2026-11-18T09:00:00Z',
                event: 'instance.login_failed',
                operator_id: null,
                operator_email: null,
                target_slug: null,
                target_email: 'betreiber@example.com',
                actor_type: null,
              },
            ],
            total: 1,
            offset: 0,
            limit: 25,
          },
        });
      }
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });
    renderPage();

    await waitFor(() => expect(screen.getByText('Anmeldung fehlgeschlagen')).toBeInTheDocument());
    const row = screen.getByText('Anmeldung fehlgeschlagen').closest('tr')!;
    expect(within(row).getByText('Nicht angemeldet')).toBeInTheDocument();
    expect(within(row).queryByText('System')).not.toBeInTheDocument();
  });

  // An operator account changed by the server command shows the account as the
  // target and "Server-Befehl" as the actor. The filter offers the command's
  // events and the takeover events.
  it('names the server command and the operator account it changed', async () => {
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url === '/api/instance/auth/me') return Promise.resolve({ data: meResponse });
      if (url === '/api/instance/audit') {
        return Promise.resolve({
          data: {
            items: [
              {
                timestamp: '2026-11-18T09:00:00Z',
                event: 'instance.operator_created',
                operator_id: null,
                operator_email: null,
                target_slug: null,
                target_email: 'betreiber2@example.com',
                actor_type: 'cli',
              },
            ],
            total: 1,
            offset: 0,
            limit: 25,
          },
        });
      }
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });
    renderPage();

    await waitFor(() => expect(screen.getByText('Betreiberkonto angelegt')).toBeInTheDocument());
    const row = screen.getByText('Betreiberkonto angelegt').closest('tr')!;
    expect(within(row).getByText('Server-Befehl')).toBeInTheDocument();
    expect(within(row).getByText('betreiber2@example.com')).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText('Ereignis'));
    for (const name of [
      'Betreiberkonto gesperrt',
      'Betreiberkonto entsperrt',
      'Zwei-Faktor-Code zurückgesetzt',
      'Passwort gesetzt',
      'Verwaltung eingesehen',
      'Verwaltung übernommen',
    ]) {
      expect(await screen.findByRole('option', { name })).toBeInTheDocument();
    }
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

  // An organization token never reaches this page: the API answers 403, so the
  // page shows the same no-operator-access branch it shows for any other 403
  // from the session probe.
  it('shows the no-operator-access message for an organization token', async () => {
    vi.mocked(apiClient.get).mockRejectedValueOnce(new ApiError(403, 'Organization token'));
    renderPage();

    await waitFor(() => expect(screen.getByText('Kein Betreiber-Zugriff')).toBeInTheDocument());
  });

  it('renders the navigation with links to the other operator pages', async () => {
    mockAuthenticated();
    renderPage();
    await waitFor(() => expect(screen.getByText('Organisation angelegt')).toBeInTheDocument());

    const nav = screen.getByRole('navigation', { name: 'Betreiber-Navigation' });
    expect(within(nav).getByRole('link', { name: 'Organisationen' })).toBeInTheDocument();
    expect(within(nav).getByRole('link', { name: 'Rechtstexte' })).toBeInTheDocument();
    expect(within(nav).getByRole('link', { name: 'Konto' })).toBeInTheDocument();
  });

  it('has no axe accessibility violations once signed in', async () => {
    mockAuthenticated();
    const { container } = renderPage();
    await waitFor(() => expect(screen.getByText('Organisation angelegt')).toBeInTheDocument());

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
