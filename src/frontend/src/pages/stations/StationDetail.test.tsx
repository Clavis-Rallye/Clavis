import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import StationDetail from './StationDetail';
import { AuthContext } from '../../auth/AuthContext';
import apiClient from '../../api/client';

// The TipTap editor needs layout APIs jsdom lacks (getClientRects, elementFromPoint).
vi.mock('../../components/RichTextEditor', () => ({
  default: ({ value, onChange, ariaLabel, id }: { value: string; onChange: (v: string) => void; ariaLabel?: string; id?: string }) => (
    <textarea id={id} aria-label={ariaLabel} value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}));

// Leaflet needs a real layout that jsdom does not provide.
vi.mock('../../components/MapPicker', () => ({
  default: () => <div data-testid="map-picker-stub" />,
}));

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

const stationFixture = (overrides: Partial<Record<string, unknown>> = {}) => ({
  id: 's1',
  organization_id: 'org-1',
  title: { de: 'Rathausplatz' },
  description: { de: '' },
  address: null,
  location_lat: null,
  location_lng: null,
  title_image_url: null,
  tips: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  created_by_user_id: 'u1',
  created_by_display_name: 'Admin',
  usage: { rallye_ids: [], rallye_titles: [] },
  usage_count: 0,
  ...overrides,
});

const renderDetail = (initialEntry: string | { pathname: string; state: unknown } = '/stations/s1') =>
  render(
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
          <Route path="/stations/:stationId" element={<StationDetail />} />
          <Route path="/stations" element={<div>Station list page</div>} />
          <Route path="/rallyes/:rallyeId" element={<div>Rallye detail page</div>} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('StationDetail', () => {
  beforeEach(() => vi.resetAllMocks());

  // When the create page saved the station but not its title image, this page says so.
  it('says that the title image was not uploaded when the create page reports it', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: stationFixture() });
    renderDetail({ pathname: '/stations/s1', state: { titleImageFailed: true } });

    expect(await screen.findByText(/Das Titelbild ließ sich nicht hochladen/)).toBeInTheDocument();
  });

  // The create page carries the refusal code, this page says the reason.
  it.each([
    ['IMAGE_NOT_DECODABLE', /Diese Datei ist kein lesbares Bild/],
    ['IMAGE_TOO_LARGE', /Das Bild ist zu groß/],
  ])('says why the title image was refused on the create page: %s', async (code, reason) => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: stationFixture() });
    renderDetail({ pathname: '/stations/s1', state: { titleImageFailed: true, titleImageRefusal: code } });

    expect(await screen.findByText(reason)).toBeInTheDocument();
    expect(screen.getByText(/Die Station ist gespeichert/)).toBeInTheDocument();
  });

  it('shows no reason for a refusal code it does not know', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: stationFixture() });
    renderDetail({ pathname: '/stations/s1', state: { titleImageFailed: true, titleImageRefusal: 'toString' } });

    expect(await screen.findByText(/Das Titelbild ließ sich nicht hochladen/)).toBeInTheDocument();
    expect(screen.queryByText(/kein lesbares Bild|zu groß/)).not.toBeInTheDocument();
  });

  it('shows no upload notice on a normal visit', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: stationFixture() });
    renderDetail();

    await screen.findByRole('link', { name: /Zurück/ });
    expect(screen.queryByText(/Das Titelbild ließ sich nicht hochladen/)).not.toBeInTheDocument();
  });

  // The create page says that the station is new, this page says so.
  it('says that the station was created when the create page reports it', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: stationFixture() });
    renderDetail({ pathname: '/stations/s1', state: { created: true } });

    expect(await screen.findByText('Die Station wurde erstellt.')).toBeInTheDocument();
  });

  it('shows no creation notice on a normal visit', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: stationFixture() });
    renderDetail();

    await screen.findByRole('link', { name: /Zurück/ });
    expect(screen.queryByText('Die Station wurde erstellt.')).not.toBeInTheDocument();
  });

  // "Bild ersetzen" stores at once, and the page says so.
  it('says that the title image is saved after "Bild ersetzen", without "Speichern"', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: stationFixture({ title_image_url: '/api/files/old.png' }),
    });
    vi.mocked(apiClient.put).mockResolvedValue({
      data: stationFixture({ title_image_url: '/api/files/new.png' }),
    });
    renderDetail();
    await screen.findByRole('button', { name: 'Bild ersetzen' });

    const file = new File(['x'], 'neu.png', { type: 'image/png' });
    await userEvent.upload(screen.getByTestId('station-image-input'), file);

    expect(await screen.findByText('Das Titelbild ist gespeichert.')).toBeInTheDocument();
    expect(apiClient.put).toHaveBeenCalledTimes(1);
    expect(apiClient.put).toHaveBeenCalledWith('/api/orgs/test-org/stations/s1/title-image', expect.any(FormData));
  });

  it('says nothing about a saved image when the upload is refused', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: stationFixture({ title_image_url: '/api/files/old.png' }),
    });
    vi.mocked(apiClient.put).mockRejectedValue(new Error('refused'));
    renderDetail();
    await screen.findByRole('button', { name: 'Bild ersetzen' });

    await userEvent.upload(screen.getByTestId('station-image-input'), new File(['x'], 'neu.png', { type: 'image/png' }));

    await waitFor(() => expect(apiClient.put).toHaveBeenCalledTimes(1));
    expect(screen.queryByText('Das Titelbild ist gespeichert.')).not.toBeInTheDocument();
  });

  // Without ?rallyeId= the back link goes to the station library.
  it('back link goes to the station list when opened without a rallye context', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: stationFixture() });

    renderDetail('/stations/s1');

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Rathausplatz' })).toBeInTheDocument(),
    );

    const back = screen.getByRole('link', { name: /Zurück zur Übersicht/ });
    expect(back).toHaveAttribute('href', '/stations');
  });

  // A station opened from a rallye (?rallyeId=) links back to it,
  // with the label naming the rallye rather than the library.
  it('back link goes to the rallye when opened with ?rallyeId=', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: stationFixture() });

    renderDetail('/stations/s1?rallyeId=r1');

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Rathausplatz' })).toBeInTheDocument(),
    );

    const back = screen.getByRole('link', { name: /Zurück zur Rallye/ });
    expect(back).toHaveAttribute('href', '/rallyes/r1');
    expect(screen.queryByRole('link', { name: /Zurück zur Übersicht/ })).not.toBeInTheDocument();
  });

  // Each rallye using this station renders as a chip linking to it.
  it('renders a chip per rallye using this station, linking to each one', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: stationFixture({
        usage: {
          rallye_ids: ['r1', 'r2'],
          rallye_titles: [{ de: 'Erste Rallye' }, { de: 'Zweite Rallye' }],
        },
        usage_count: 2,
      }),
    });

    renderDetail('/stations/s1');

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Rathausplatz' })).toBeInTheDocument(),
    );

    const chip1 = screen.getByRole('link', { name: 'Erste Rallye' });
    expect(chip1).toHaveAttribute('href', '/rallyes/r1');
    const chip2 = screen.getByRole('link', { name: 'Zweite Rallye' });
    expect(chip2).toHaveAttribute('href', '/rallyes/r2');
  });

  it('shows no usage alert when the station is unused', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: stationFixture() });

    renderDetail('/stations/s1');

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Rathausplatz' })).toBeInTheDocument(),
    );

    expect(screen.queryByRole('link', { name: /Rallye/ })).not.toBeInTheDocument();
  });

  // Archiving a station in use names both the affected rallyes and
  // the way back, and does not fall back to the plain (no-usage) wording.
  it('the archive confirmation for a station in use names the rallyes and the way back', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: stationFixture({
        usage: { rallye_ids: ['r1'], rallye_titles: [{ de: 'Erste Rallye' }] },
        usage_count: 1,
      }),
    });

    renderDetail('/stations/s1');

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Rathausplatz' })).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByRole('button', { name: 'Archivieren' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Erste Rallye/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Archivierte anzeigen/)).toBeInTheDocument();
  });

  it('has no axe accessibility violations on initial render', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: stationFixture({
        usage: { rallye_ids: ['r1'], rallye_titles: [{ de: 'Erste Rallye' }] },
        usage_count: 1,
      }),
    });

    const { container } = renderDetail('/stations/s1?rallyeId=r1');
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Rathausplatz' })).toBeInTheDocument(),
    );

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  // The creator's editor.
  describe('editing', () => {
    const openDetail = async (fixture = stationFixture()) => {
      vi.mocked(apiClient.get).mockResolvedValue({ data: fixture });
      renderDetail('/stations/s1');
      await waitFor(() =>
        expect(screen.getByRole('heading', { name: 'Rathausplatz' })).toBeInTheDocument(),
      );
    };
    const titleField = () => screen.getByRole('textbox', { name: /Titel \(Deutsch\)/ });

    // The field names the missing German title instead of only turning red.
    it('names the missing German title when the field is left empty', async () => {
      await openDetail();

      fireEvent.change(titleField(), { target: { value: '' } });
      fireEvent.blur(titleField());

      expect(await screen.findByText('Pflichtfeld: bitte auf Deutsch ausfüllen.')).toBeInTheDocument();
    });

    // "Speichern" gives a sign that it worked.
    it('says "Gespeichert." after a save', async () => {
      await openDetail();
      vi.mocked(apiClient.put).mockResolvedValue({ data: stationFixture({ title: { de: 'Rathausplatz neu' } }) });

      fireEvent.change(titleField(), { target: { value: 'Rathausplatz neu' } });
      fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));

      expect(await screen.findByText('Gespeichert.')).toBeInTheDocument();
    });

    // A tip link without a label is refused once, at the tips.
    it('a tip link without a label: one refusal at the tips, the field marked and focused', async () => {
      await openDetail(
        stationFixture({ tips: { header: { de: 'Tipps' }, links: [{ label: { de: 'Karte' }, url: 'https://example.org' }] } }),
      );
      const label = () => screen.getByRole('textbox', { name: 'Link 1: Beschriftung (Deutsch)' });

      fireEvent.change(label(), { target: { value: '' } });
      fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));

      await waitFor(() => expect(label()).toHaveAttribute('aria-invalid', 'true'));
      expect(label()).toHaveFocus();
      expect(label()).toHaveAccessibleDescription(/Jeder Link braucht eine Beschriftung\./);
      expect(screen.getAllByText('Jeder Link braucht eine Beschriftung.')).toHaveLength(1);
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
      expect(apiClient.put).not.toHaveBeenCalled();
    });

    // The back link does not drop unsaved changes without a word.
    it('asks before the back link drops unsaved changes', async () => {
      await openDetail();

      fireEvent.change(titleField(), { target: { value: 'Geändert' } });
      fireEvent.click(screen.getByRole('link', { name: /Zurück zur Übersicht/ }));

      const dialog = await screen.findByRole('dialog', { name: 'Änderungen verwerfen?' });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Auf der Seite bleiben' }));
      await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Änderungen verwerfen?' })).toBeNull());
      expect(titleField()).toHaveValue('Geändert');
    });
  });

  // "Duplizieren" asks first and creates one copy.
  describe('duplicate', () => {
    const openDetail = async () => {
      vi.mocked(apiClient.get).mockResolvedValue({ data: stationFixture() });
      renderDetail('/stations/s1');
      await waitFor(() =>
        expect(screen.getByRole('heading', { name: 'Rathausplatz' })).toBeInTheDocument(),
      );
    };

    it('opens a confirm dialog and creates nothing yet', async () => {
      await openDetail();

      fireEvent.click(screen.getByRole('button', { name: 'Duplizieren' }));

      const dialog = await screen.findByRole('dialog');
      expect(dialog).toHaveTextContent('Eine Kopie dieser Station erstellen?');
      expect(apiClient.post).not.toHaveBeenCalled();
    });

    it('cancel closes the dialog and creates nothing', async () => {
      await openDetail();

      fireEvent.click(screen.getByRole('button', { name: 'Duplizieren' }));
      const dialog = await screen.findByRole('dialog');
      fireEvent.click(within(dialog).getByRole('button', { name: 'Abbrechen' }));

      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      expect(apiClient.post).not.toHaveBeenCalled();
    });

    it('confirm creates exactly one copy, also on a second click during the request', async () => {
      await openDetail();
      let finish: (value: unknown) => void = () => {};
      vi.mocked(apiClient.post).mockReturnValue(new Promise((resolve) => { finish = resolve; }));

      fireEvent.click(screen.getByRole('button', { name: 'Duplizieren' }));
      const dialog = await screen.findByRole('dialog');
      const confirm = within(dialog).getByRole('button', { name: 'Duplizieren' });
      fireEvent.click(confirm);
      fireEvent.click(confirm);

      expect(apiClient.post).toHaveBeenCalledTimes(1);
      expect(vi.mocked(apiClient.post).mock.calls[0][0]).toContain('/stations/s1/duplicate');
      await act(async () => { finish({ data: stationFixture({ id: 's2' }) }); });
    });
  });
});
