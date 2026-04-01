import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import RallyeList from './RallyeList';
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

const renderList = () =>
  render(
    <MemoryRouter initialEntries={['/rallyes']}>
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
          <Route path="/rallyes" element={<RallyeList />} />
          <Route path="/rallyes/new" element={<div>Create page</div>} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

/**
 * Mock GET so the rallyes list returns `items`, while the per-rallye
 * station-count calls (GET .../stations) resolve to an empty link list.
 */
const mockListWith = (items: unknown[]) => {
  vi.mocked(apiClient.get).mockImplementation((url: string) => {
    if (/\/stations$/.test(url)) {
      return Promise.resolve({ data: { items: [], total: 0 } });
    }
    return Promise.resolve({ data: { items, total: items.length, skip: 0, limit: 100 } });
  });
};

describe('RallyeList', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders empty state when no rallyes', async () => {
    mockListWith([]);
    renderList();
    await waitFor(() => {
      expect(screen.getByText('Noch keine Rallyes erstellt.')).toBeInTheDocument();
    });
  });

  it('renders rallye cards with title, description snippet, status chip', async () => {
    mockListWith([
      {
        id: 'r1',
        organization_id: 'org-1',
        title: { de: 'Augsburg Altstadt' },
        description: { de: 'Eine Tour durch die Altstadt' },
        is_published: true,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
      {
        id: 'r2',
        organization_id: 'org-1',
        title: { de: 'Zweite Rallye' },
        description: { de: '' },
        is_published: false,
        created_at: '2026-01-02T00:00:00Z',
        updated_at: '2026-01-02T00:00:00Z',
      },
    ]);
    renderList();

    await waitFor(() => {
      expect(screen.getByText('Augsburg Altstadt')).toBeInTheDocument();
    });
    expect(screen.getByText('Eine Tour durch die Altstadt')).toBeInTheDocument();
    expect(screen.getByText('Zweite Rallye')).toBeInTheDocument();
    // Published chip + draft chip (also the filter buttons bear these labels)
    expect(screen.getAllByText('Veröffentlicht').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Entwurf').length).toBeGreaterThan(0);
  });

  // WCAG 2.4.6: headings and labels describe their purpose.
  it('the archive buttons of two rallyes name their rallye and are unique', async () => {
    const rallye = (id: string, title: string) => ({
      id,
      organization_id: 'org-1',
      title: { de: title },
      description: { de: '' },
      is_published: true,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    });
    mockListWith([rallye('r1', 'Augsburg Altstadt'), rallye('r2', 'Zweite Rallye')]);
    renderList();
    await screen.findByText('Zweite Rallye');

    const names = screen
      .getAllByRole('button')
      .map((b) => b.getAttribute('aria-label') ?? '')
      .filter((n) => n.startsWith('Archivieren'));
    expect(names.sort()).toEqual(['Archivieren: Augsburg Altstadt', 'Archivieren: Zweite Rallye']);
  });

  it('the open button of a rallye carries the title of the rallye', async () => {
    mockListWith([
      {
        id: 'r1',
        organization_id: 'org-1',
        title: { de: 'Augsburg Altstadt' },
        description: { de: '' },
        is_published: true,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    ]);
    renderList();
    expect(
      await screen.findByRole('button', { name: 'Öffnen: Augsburg Altstadt' }),
    ).toBeInTheDocument();
  });

  it('filters to published only', async () => {
    mockListWith([
      {
        id: 'r1',
        organization_id: 'org-1',
        title: { de: 'Published One' },
        description: { de: '' },
        is_published: true,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
      {
        id: 'r2',
        organization_id: 'org-1',
        title: { de: 'Draft One' },
        description: { de: '' },
        is_published: false,
        created_at: '2026-01-02T00:00:00Z',
        updated_at: '2026-01-02T00:00:00Z',
      },
    ]);
    renderList();

    await waitFor(() => expect(screen.getByText('Published One')).toBeInTheDocument());
    expect(screen.getByText('Draft One')).toBeInTheDocument();

    // Open the Status filter select and pick "Veröffentlicht".
    await userEvent.click(screen.getByRole('combobox', { name: 'Status' }));
    await userEvent.click(
      within(screen.getByRole('listbox')).getByRole('option', { name: 'Veröffentlicht' }),
    );

    expect(screen.getByText('Published One')).toBeInTheDocument();
    expect(screen.queryByText('Draft One')).not.toBeInTheDocument();
  });

  it('filters by title search (case-insensitive)', async () => {
    mockListWith([
      {
        id: 'r1',
        organization_id: 'org-1',
        title: { de: 'Augsburg Tour' },
        description: { de: '' },
        is_published: false,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
      {
        id: 'r2',
        organization_id: 'org-1',
        title: { de: 'München Route' },
        description: { de: '' },
        is_published: false,
        created_at: '2026-01-02T00:00:00Z',
        updated_at: '2026-01-02T00:00:00Z',
      },
    ]);
    renderList();

    await waitFor(() => expect(screen.getByText('Augsburg Tour')).toBeInTheDocument());

    await userEvent.type(screen.getByLabelText('Suchen'), 'münchen');

    expect(screen.queryByText('Augsburg Tour')).not.toBeInTheDocument();
    expect(screen.getByText('München Route')).toBeInTheDocument();
  });

  // The list sorts by status descending (published, draft, archived),
  // name ascending inside each status group, not by name alone.
  it('sorts by status descending, then by name inside each status', async () => {
    mockListWith([
      {
        id: 'r1',
        organization_id: 'org-1',
        title: { de: 'Zebra Tour' },
        description: { de: '' },
        is_published: true,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
      {
        id: 'r2',
        organization_id: 'org-1',
        title: { de: 'Anton Tour' },
        description: { de: '' },
        is_published: false,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
      {
        id: 'r3',
        organization_id: 'org-1',
        title: { de: 'Mitte Tour' },
        description: { de: '' },
        is_published: true,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    ]);
    renderList();

    await waitFor(() => expect(screen.getByText('Zebra Tour')).toBeInTheDocument());

    const mitte = screen.getByText('Mitte Tour');
    const zebra = screen.getByText('Zebra Tour');
    const anton = screen.getByText('Anton Tour');
    // Published group ("Mitte", "Zebra") sorted by name, before the draft ("Anton").
    expect(mitte.compareDocumentPosition(zebra) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(zebra.compareDocumentPosition(anton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('shows retry button on fetch error', async () => {
    vi.mocked(apiClient.get).mockRejectedValueOnce(new Error('Network down'));
    renderList();
    await waitFor(() => {
      expect(screen.getByText(/network down/i)).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /erneut versuchen/i })).toBeInTheDocument();
  });

  it('hides create button for participant role', async () => {
    mockListWith([]);
    render(
      <MemoryRouter initialEntries={['/rallyes']}>
        <AuthContext.Provider
          value={{
            user: { ...user, role: 'participant' },
            isAuthenticated: true,
            isLoading: false,
            login: vi.fn(),
            logout: vi.fn(),
            checkAuth: vi.fn(),
            needsTwoFactorSetup: () => false,
          }}
        >
          <Routes>
            <Route path="/rallyes" element={<RallyeList />} />
          </Routes>
        </AuthContext.Provider>
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByText('Noch keine Rallyes erstellt.')).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: 'Rallye erstellen' })).not.toBeInTheDocument();
  });

  // The archive view for org_admin.
  it('org_admin toggle loads archived rallyes and offers restore + permanent delete', async () => {
    mockListWith([]);
    renderList();
    await waitFor(() => {
      expect(screen.getByText('Noch keine Rallyes erstellt.')).toBeInTheDocument();
    });

    // Toggle on → list re-fetches with include_deleted and shows the archived row
    mockListWith([
      {
        id: 'r-arch',
        organization_id: 'org-1',
        title: { de: 'Archivierte Rallye' },
        description: { de: '' },
        is_published: false,
        is_deleted: true,
        deleted_at: '2026-07-01T00:00:00Z',
        created_at: '2026-06-01T00:00:00Z',
        updated_at: '2026-07-01T00:00:00Z',
        created_by_user_id: null,
        created_by_display_name: null,
      },
    ]);
    await userEvent.click(screen.getByRole('switch', { name: 'Archivierte anzeigen' }));

    await waitFor(() => {
      expect(screen.getByText('Archivierte Rallye')).toBeInTheDocument();
    });
    expect(vi.mocked(apiClient.get)).toHaveBeenCalledWith(
      '/api/orgs/test-org/rallyes/',
      expect.objectContaining({ params: { include_deleted: true } }),
    );
    expect(screen.getByText('Archiviert')).toBeInTheDocument();

    // Restore action calls the restore endpoint
    vi.mocked(apiClient.post).mockResolvedValue({ data: {} });
    await userEvent.click(screen.getByRole('button', { name: 'Wiederherstellen' }));
    await waitFor(() => {
      expect(vi.mocked(apiClient.post)).toHaveBeenCalledWith(
        '/api/orgs/test-org/rallyes/r-arch/restore',
      );
    });
  });

  it('permanent delete asks for confirmation and calls the permanent endpoint', async () => {
    mockListWith([
      {
        id: 'r-arch',
        organization_id: 'org-1',
        title: { de: 'Archivierte Rallye' },
        description: { de: '' },
        is_published: false,
        is_deleted: true,
        deleted_at: '2026-07-01T00:00:00Z',
        created_at: '2026-06-01T00:00:00Z',
        updated_at: '2026-07-01T00:00:00Z',
        created_by_user_id: null,
        created_by_display_name: null,
      },
    ]);
    renderList();
    await waitFor(() => {
      expect(screen.getByText('Archivierte Rallye')).toBeInTheDocument();
    });

    vi.mocked(apiClient.delete).mockResolvedValue({ data: undefined });
    await userEvent.click(screen.getByRole('button', { name: 'Endgültig löschen' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Endgültig löschen' }));
    await waitFor(() => {
      expect(vi.mocked(apiClient.delete)).toHaveBeenCalledWith(
        '/api/orgs/test-org/rallyes/r-arch/permanent',
      );
    });
  });

  it('has no axe accessibility violations on initial render', async () => {
    mockListWith([]);
    const { container } = renderList();
    await waitFor(() =>
      expect(screen.getByText('Noch keine Rallyes erstellt.')).toBeInTheDocument(),
    );
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
