import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'vitest-axe';
import ImportStationDialog from './ImportStationDialog';
import { AuthContext } from '../auth/AuthContext';
import apiClient from '../api/client';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
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

const STATIONS_URL = '/api/orgs/test-org/stations/';

const station = (id: string, title: string, address?: string) => ({
  id,
  organization_id: 'org-1',
  title: { de: title },
  description: { de: '' },
  address: address ?? null,
  location_lat: null,
  location_lng: null,
  title_image_url: null,
  tips: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  created_by_user_id: null,
  created_by_display_name: 'Sarah',
  usage: { rallye_ids: [], rallye_titles: [] },
});

const pageOf = (items: unknown[]) => ({
  data: { items, total: items.length, skip: 0, limit: 200 },
});

const renderDialog = (
  linkedStationIds: string[] = [],
  onImport: (stationId: string) => void = vi.fn(),
) =>
  render(
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
      <ImportStationDialog
        open
        linkedStationIds={linkedStationIds}
        onCancel={vi.fn()}
        onImport={onImport}
      />
    </AuthContext.Provider>,
  );

describe('ImportStationDialog search', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('sends the typed query after the debounce and renders the returned rows unfiltered', async () => {
    // The API narrows. Whatever it returns renders, including rows whose
    // German title does not contain the query (it may match in another
    // language or the address).
    vi.mocked(apiClient.get).mockImplementation(async (_url, config) => {
      const q = (config?.params as Record<string, unknown> | undefined)?.q;
      return pageOf(q ? [station('s-1', 'Rathaus'), station('s-2', 'Zeughaus')] : [
        station('s-1', 'Rathaus'),
      ]);
    });
    renderDialog();
    expect(await screen.findByText('Rathaus')).toBeInTheDocument();

    await userEvent.type(screen.getByRole('textbox', { name: 'Stationen durchsuchen' }), 'city');

    await waitFor(() =>
      expect(apiClient.get).toHaveBeenLastCalledWith(STATIONS_URL, {
        params: { q: 'city', limit: 200 },
      }),
    );
    // One burst of typing collapsed into a single request.
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    // The rows land only after the mocked response resolves and React commits.
    // Wait for them instead of asserting synchronously after the call.
    expect(await screen.findByText('Rathaus')).toBeInTheDocument();
    expect(await screen.findByText('Zeughaus')).toBeInTheDocument();
  });

  it('shows the no-match text for an active search, the empty text without one', async () => {
    vi.mocked(apiClient.get).mockResolvedValue(pageOf([]));
    renderDialog();
    expect(await screen.findByText('Keine Stationen in deiner Bibliothek.')).toBeInTheDocument();

    await userEvent.type(screen.getByRole('textbox', { name: 'Stationen durchsuchen' }), 'zzz');

    await waitFor(() =>
      expect(apiClient.get).toHaveBeenLastCalledWith(STATIONS_URL, {
        params: { q: 'zzz', limit: 200 },
      }),
    );
    expect(await screen.findByText('Keine Station passt zu dieser Suche.')).toBeInTheDocument();
    expect(screen.queryByText('Keine Stationen in deiner Bibliothek.')).not.toBeInTheDocument();
  });
});

// The list is a radio group, so a row can be chosen
// without a pointer and the choice is exposed to assistive technology.
describe('ImportStationDialog row choice', () => {
  const threeStations = () =>
    pageOf([
      station('s-1', 'Rathaus', 'Rathausplatz 2'),
      station('s-2', 'Zeughaus', 'Zeugplatz 4'),
      station('s-3', 'Dom', 'Frauentorstraße 1'),
    ]);

  // Tab from wherever the dialog put the focus until a radio button holds it.
  const tabToList = async (kb: ReturnType<typeof userEvent.setup>) => {
    for (let i = 0; i < 6; i += 1) {
      await kb.tab();
      if (document.activeElement?.getAttribute('type') === 'radio') return;
    }
  };

  const submitButton = () => screen.getByRole('button', { name: 'Station hinzufügen' });

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(apiClient.get).mockResolvedValue(threeStations());
  });

  it('chooses a row with the keyboard alone and enables the import button', async () => {
    const kb = userEvent.setup();
    renderDialog();
    await screen.findByText('Rathaus');
    expect(submitButton()).toBeDisabled();

    await tabToList(kb);
    const first = screen.getByRole('radio', { name: 'Rathaus' });
    expect(first).toHaveFocus();
    expect(first).not.toBeChecked();

    await kb.keyboard(' ');
    expect(first).toBeChecked();
    expect(submitButton()).toBeEnabled();

    // The arrow keys move the choice between the rows.
    await kb.keyboard('{ArrowDown}');
    const second = screen.getByRole('radio', { name: 'Zeughaus' });
    expect(second).toHaveFocus();
    expect(second).toBeChecked();
    expect(first).not.toBeChecked();
    expect(submitButton()).toBeEnabled();
  });

  it('chooses the focused row with Enter too', async () => {
    const kb = userEvent.setup();
    renderDialog();
    await screen.findByText('Rathaus');

    await tabToList(kb);
    await kb.keyboard('{Enter}');

    expect(screen.getByRole('radio', { name: 'Rathaus' })).toBeChecked();
    expect(submitButton()).toBeEnabled();
  });

  // jsdom draws nothing and only guesses `:focus-visible`, so the test holds the
  // two halves apart: the keyboard lands on the radio, and the wrapper of the
  // radio (the native input is transparent) has a ring for keyboard focus.
  it('gives the control the keyboard reached a focus ring', async () => {
    const kb = userEvent.setup();
    renderDialog();
    await screen.findByText('Rathaus');

    await tabToList(kb);
    const radio = screen.getByRole('radio', { name: 'Rathaus' });
    expect(radio).toHaveFocus();

    const wrapperClass = Array.from(radio.parentElement?.classList ?? []).find((c) =>
      c.startsWith('css-'),
    );
    expect(wrapperClass).toBeTruthy();
    const css = Array.from(document.querySelectorAll('style'))
      .map((el) => el.textContent)
      .join('');
    expect(css).toMatch(
      new RegExp(`\\.${wrapperClass}\\.Mui-focusVisible\\{[^}]*outline:2px solid`),
    );
  });

  it('exposes one named radio group whose radios carry the station titles', async () => {
    renderDialog();
    await screen.findByText('Rathaus');

    const group = screen.getByRole('radiogroup', { name: 'Vorhandene Station verwenden' });
    const radios = within(group).getAllByRole('radio');
    expect(radios).toHaveLength(3);
    expect(radios.map((r) => r.getAttribute('name'))).toEqual(
      Array(3).fill(radios[0].getAttribute('name')),
    );
    expect(radios[0].getAttribute('name')).toBeTruthy();
    expect(radios[0]).toHaveAccessibleName('Rathaus');
    expect(radios[1]).toHaveAccessibleName('Zeughaus');
    expect(radios[2]).toHaveAccessibleName('Dom');
    radios.forEach((r) => expect(r).not.toBeChecked());
  });

  it('exposes a linked row as not available, with the reason the row shows', async () => {
    const kb = userEvent.setup();
    renderDialog(['s-2']);
    await screen.findByText('Rathaus');

    const linked = screen.getByRole('radio', { name: 'Zeughaus' });
    expect(linked).toBeDisabled();
    expect(linked).toHaveAccessibleDescription('Bereits hinzugefügt');
    // The reason stays visible text in the row.
    expect(screen.getByText('Bereits hinzugefügt')).toBeVisible();

    // The keyboard passes over the row that cannot be chosen.
    await tabToList(kb);
    await kb.keyboard(' ');
    await kb.keyboard('{ArrowDown}');
    expect(screen.getByRole('radio', { name: 'Dom' })).toBeChecked();
    expect(linked).not.toBeChecked();
  });

  it('still chooses a row by a click anywhere on it', async () => {
    const kb = userEvent.setup();
    renderDialog(['s-2']);
    await screen.findByText('Rathaus');

    // A linked row takes no click.
    await kb.click(screen.getByText('Zeugplatz 4'));
    expect(screen.getByRole('radio', { name: 'Zeughaus' })).not.toBeChecked();
    expect(submitButton()).toBeDisabled();

    await kb.click(screen.getByText('Frauentorstraße 1'));
    expect(screen.getByRole('radio', { name: 'Dom' })).toBeChecked();
    expect(submitButton()).toBeEnabled();

    await kb.click(screen.getByRole('radio', { name: 'Rathaus' }));
    expect(screen.getByRole('radio', { name: 'Rathaus' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Dom' })).not.toBeChecked();
  });

  it('imports the station the keyboard chose', async () => {
    const kb = userEvent.setup();
    const onImport = vi.fn();
    renderDialog([], onImport);
    await screen.findByText('Rathaus');

    await tabToList(kb);
    await kb.keyboard(' ');
    await kb.keyboard('{ArrowDown}');
    await kb.click(submitButton());

    expect(onImport).toHaveBeenCalledWith('s-2');
  });

  // The dialog renders into a portal: scan the document body, not the empty
  // render container. jsdom has no layout, so every box measures 0 x 0 and axe
  // takes the content of the scrolling dialog for clipped: it then finds no
  // visible label behind `aria-labelledby` and reports `label-title-only`.
  // Giving the boxes a size lets the rule measure what a browser shows.
  it('has no axe violation with no row chosen and with one chosen', async () => {
    const kb = userEvent.setup();
    const layout = vi
      .spyOn(Element.prototype, 'getBoundingClientRect')
      .mockReturnValue(new DOMRect(0, 0, 1024, 768));
    try {
      const { baseElement } = renderDialog(['s-2']);
      await screen.findByText('Rathaus');

      expect(await axe(baseElement)).toHaveNoViolations();

      await kb.click(screen.getByRole('radio', { name: 'Rathaus' }));
      expect(screen.getByRole('radio', { name: 'Rathaus' })).toBeChecked();
      expect(await axe(baseElement)).toHaveNoViolations();
    } finally {
      layout.mockRestore();
    }
  });
});
