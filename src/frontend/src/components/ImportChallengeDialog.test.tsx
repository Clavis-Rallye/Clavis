import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'vitest-axe';
import ImportChallengeDialog from './ImportChallengeDialog';
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

const CHALLENGES_URL = '/api/orgs/test-org/challenges/';

const challenge = (
  id: string,
  title: string,
  type: string,
  stationIds: string[],
  updatedAt: string,
) => ({
  id,
  organization_id: 'org-1',
  challenge_type: type,
  title: { de: title },
  instructions: { de: '' },
  scoring_rules: { max_points: 10 },
  time_estimate_minutes: 5,
  suggested_solution: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: updatedAt,
  created_by_display_name: 'Sarah',
  usage: {
    station_ids: stationIds,
    station_titles: [],
    rallye_ids: [],
    rallye_titles: [],
  },
});

// The API narrows: the type filter arrives as `type` in the
// request, and the dialog renders exactly the rows the mocked response holds.
const allItems = () => [
  // Newest, but not used on station-1.
  challenge('c-new', 'Neueste Aufgabe', 'text', [], '2026-06-01T00:00:00Z'),
  // Older, but matched to station-1 in another rallye → must sort first.
  challenge('c-rel', 'Relevante Aufgabe', 'quiz', ['station-1'], '2026-02-01T00:00:00Z'),
  challenge('c-old', 'Alte Aufgabe', 'photo', [], '2026-01-15T00:00:00Z'),
];

const renderDialog = (
  linkedChallengeIds: string[] = [],
  onImport: (challengeId: string) => void = vi.fn(),
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
      <ImportChallengeDialog
        open
        linkedChallengeIds={linkedChallengeIds}
        stationId="station-1"
        onCancel={vi.fn()}
        onImport={onImport}
      />
    </AuthContext.Provider>,
  );

describe('ImportChallengeDialog', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(apiClient.get).mockImplementation(async (_url, config) => {
      const params = (config?.params ?? {}) as Record<string, unknown>;
      const items = params.type === 'photo' ? [allItems()[2]] : allItems();
      return { data: { items, total: items.length, skip: 0, limit: 200 } };
    });
  });

  it('sorts station-relevant challenges first and marks them', async () => {
    renderDialog();
    const rows = await waitFor(() => {
      const r = screen.getAllByRole('row').slice(1); // skip header
      expect(r).toHaveLength(3);
      return r;
    });
    expect(within(rows[0]).getByText('Relevante Aufgabe')).toBeInTheDocument();
    expect(within(rows[0]).getByText('Passt zu dieser Station')).toBeInTheDocument();
    // Remaining rows: most recently updated first.
    expect(within(rows[1]).getByText('Neueste Aufgabe')).toBeInTheDocument();
    expect(within(rows[2]).getByText('Alte Aufgabe')).toBeInTheDocument();
  });

  it('sends the type filter to the API and renders its response', async () => {
    renderDialog();
    await screen.findByText('Relevante Aufgabe');

    await userEvent.click(screen.getByLabelText('Typ'));
    await userEvent.click(await screen.findByRole('option', { name: 'Foto' }));

    await waitFor(() =>
      expect(apiClient.get).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: { type: 'photo', limit: 200 },
      }),
    );
    expect(await screen.findByText('Alte Aufgabe')).toBeInTheDocument();
    expect(screen.queryByText('Relevante Aufgabe')).not.toBeInTheDocument();
    expect(screen.queryByText('Neueste Aufgabe')).not.toBeInTheDocument();
  });

  it('shows the no-match text for a type-only filter with no rows, not the library empty text', async () => {
    vi.mocked(apiClient.get).mockImplementation(async (_url, config) => {
      const params = (config?.params ?? {}) as Record<string, unknown>;
      const items = params.type === 'audio' ? [] : allItems();
      return { data: { items, total: items.length, skip: 0, limit: 200 } };
    });
    renderDialog();
    await screen.findByText('Relevante Aufgabe');

    await userEvent.click(screen.getByLabelText('Typ'));
    await userEvent.click(await screen.findByRole('option', { name: 'Audio' }));

    await waitFor(() =>
      expect(apiClient.get).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: { type: 'audio', limit: 200 },
      }),
    );
    // The library has tasks, only none of this type: the filter found nothing.
    expect(
      await screen.findByText('Keine Aufgabe passt zu dieser Suche oder diesem Filter.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Keine Aufgaben in deiner Bibliothek.')).not.toBeInTheDocument();
  });

  it('offers the six challenge types a creator can choose', async () => {
    renderDialog();
    await screen.findByText('Relevante Aufgabe');

    await userEvent.click(screen.getByLabelText('Typ'));
    const options = await screen.findAllByRole('option');
    // The same order as the type choice of "Neue Aufgabe".
    expect(options.map((o) => o.textContent)).toEqual([
      'Alle Typen',
      'Foto',
      'Video',
      'Audio',
      'Text',
      'QR-Code',
      'Quiz',
    ]);
  });
});

// The list is a radio group, so a row can be chosen
// without a pointer and the choice is exposed to assistive technology.
describe('ImportChallengeDialog row choice', () => {
  // Rendered order: Relevante Aufgabe, Neueste Aufgabe, Alte Aufgabe.
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(apiClient.get).mockImplementation(async () => {
      const items = allItems();
      return { data: { items, total: items.length, skip: 0, limit: 200 } };
    });
  });

  // Tab from wherever the dialog put the focus until a radio button holds it.
  const tabToList = async (kb: ReturnType<typeof userEvent.setup>) => {
    for (let i = 0; i < 6; i += 1) {
      await kb.tab();
      if (document.activeElement?.getAttribute('type') === 'radio') return;
    }
  };

  const submitButton = () => screen.getByRole('button', { name: 'Aufgabe hinzufügen' });

  it('chooses a row with the keyboard alone and enables the import button', async () => {
    const kb = userEvent.setup();
    renderDialog();
    await screen.findByText('Relevante Aufgabe');
    expect(submitButton()).toBeDisabled();

    await tabToList(kb);
    const first = screen.getByRole('radio', { name: 'Relevante Aufgabe' });
    expect(first).toHaveFocus();
    expect(first).not.toBeChecked();

    await kb.keyboard(' ');
    expect(first).toBeChecked();
    expect(submitButton()).toBeEnabled();

    // The arrow keys move the choice between the rows.
    await kb.keyboard('{ArrowDown}');
    const second = screen.getByRole('radio', { name: 'Neueste Aufgabe' });
    expect(second).toHaveFocus();
    expect(second).toBeChecked();
    expect(first).not.toBeChecked();
    expect(submitButton()).toBeEnabled();
  });

  it('chooses the focused row with Enter too', async () => {
    const kb = userEvent.setup();
    renderDialog();
    await screen.findByText('Relevante Aufgabe');

    await tabToList(kb);
    await kb.keyboard('{Enter}');

    expect(screen.getByRole('radio', { name: 'Relevante Aufgabe' })).toBeChecked();
    expect(submitButton()).toBeEnabled();
  });

  // jsdom draws nothing and only guesses `:focus-visible`, so the test holds the
  // two halves apart: the keyboard lands on the radio, and the wrapper of the
  // radio (the native input is transparent) has a ring for keyboard focus.
  it('gives the control the keyboard reached a focus ring', async () => {
    const kb = userEvent.setup();
    renderDialog();
    await screen.findByText('Relevante Aufgabe');

    await tabToList(kb);
    const radio = screen.getByRole('radio', { name: 'Relevante Aufgabe' });
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

  it('exposes one named radio group whose radios carry the task titles', async () => {
    renderDialog();
    await screen.findByText('Relevante Aufgabe');

    const group = screen.getByRole('radiogroup', { name: 'Vorhandene Aufgabe verwenden' });
    const radios = within(group).getAllByRole('radio');
    expect(radios).toHaveLength(3);
    expect(radios.map((r) => r.getAttribute('name'))).toEqual(
      Array(3).fill(radios[0].getAttribute('name')),
    );
    expect(radios[0].getAttribute('name')).toBeTruthy();
    // The name is the title alone; the marker of the row is its description.
    expect(radios[0]).toHaveAccessibleName('Relevante Aufgabe');
    expect(radios[0]).toHaveAccessibleDescription('Passt zu dieser Station');
    expect(radios[1]).toHaveAccessibleName('Neueste Aufgabe');
    expect(radios[2]).toHaveAccessibleName('Alte Aufgabe');
    radios.forEach((r) => expect(r).not.toBeChecked());
  });

  it('exposes a linked row as not available, with the reason the row shows', async () => {
    const kb = userEvent.setup();
    renderDialog(['c-new']);
    await screen.findByText('Relevante Aufgabe');

    const linked = screen.getByRole('radio', { name: 'Neueste Aufgabe' });
    expect(linked).toBeDisabled();
    expect(linked).toHaveAccessibleDescription('Bereits hinzugefügt');
    // The reason stays visible text in the row.
    expect(screen.getByText('Bereits hinzugefügt')).toBeVisible();

    // The keyboard passes over the row that cannot be chosen.
    await tabToList(kb);
    await kb.keyboard(' ');
    await kb.keyboard('{ArrowDown}');
    expect(screen.getByRole('radio', { name: 'Alte Aufgabe' })).toBeChecked();
    expect(linked).not.toBeChecked();
  });

  it('still chooses a row by a click anywhere on it', async () => {
    const kb = userEvent.setup();
    renderDialog(['c-new']);
    await screen.findByText('Relevante Aufgabe');
    const rowOf = (title: string) => screen.getByText(title).closest('tr') as HTMLElement;

    // A linked row takes no click.
    await kb.click(within(rowOf('Neueste Aufgabe')).getByText('Text'));
    expect(screen.getByRole('radio', { name: 'Neueste Aufgabe' })).not.toBeChecked();
    expect(submitButton()).toBeDisabled();

    await kb.click(within(rowOf('Alte Aufgabe')).getByText('Foto'));
    expect(screen.getByRole('radio', { name: 'Alte Aufgabe' })).toBeChecked();
    expect(submitButton()).toBeEnabled();

    await kb.click(screen.getByRole('radio', { name: 'Relevante Aufgabe' }));
    expect(screen.getByRole('radio', { name: 'Relevante Aufgabe' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Alte Aufgabe' })).not.toBeChecked();
  });

  it('imports the task the keyboard chose', async () => {
    const kb = userEvent.setup();
    const onImport = vi.fn();
    renderDialog([], onImport);
    await screen.findByText('Relevante Aufgabe');

    await tabToList(kb);
    await kb.keyboard(' ');
    await kb.keyboard('{ArrowDown}');
    await kb.click(submitButton());

    expect(onImport).toHaveBeenCalledWith('c-new');
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
      const { baseElement } = renderDialog(['c-new']);
      await screen.findByText('Relevante Aufgabe');

      expect(await axe(baseElement)).toHaveNoViolations();

      await kb.click(screen.getByRole('radio', { name: 'Relevante Aufgabe' }));
      expect(screen.getByRole('radio', { name: 'Relevante Aufgabe' })).toBeChecked();
      expect(await axe(baseElement)).toHaveNoViolations();
    } finally {
      layout.mockRestore();
    }
  });
});
