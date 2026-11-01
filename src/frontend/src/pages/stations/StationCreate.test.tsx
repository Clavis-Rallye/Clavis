import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router';
import StationCreate from './StationCreate';
import { AuthContext } from '../../auth/AuthContext';
import apiClient, { ApiError } from '../../api/client';
import * as geocode from '../../utils/geocode';

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

vi.mock('../../utils/geocode', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../utils/geocode')>();
  return { ...actual, getGeocodeConfig: vi.fn() };
});

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const user = {
  id: 'u1',
  email: 'creator@example.com',
  display_name: 'Creator',
  role: 'creator' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const station = { id: 's1', title: { de: 'Rathausplatz' }, title_image_url: null };
const link = { link_id: 'link-1', rallye_id: 'r1', sort_order: 0, station };

/** Shows where the page went, and the router state it carried. */
const Landing: React.FC<{ name: string }> = ({ name }) => {
  const location = useLocation();
  return (
    <div>
      {name} {location.search} {JSON.stringify(location.state)}
    </div>
  );
};

const renderCreate = (initialEntry: string) =>
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
          <Route path="/stations/new" element={<StationCreate />} />
          <Route path="/stations/:stationId" element={<Landing name="Station page" />} />
          <Route path="/rallyes/:rallyeId" element={<Landing name="Rallye page" />} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

const fillTitleAndImage = async (withImage: boolean) => {
  await userEvent.type(screen.getByRole('textbox', { name: /Titel.*Deutsch/i }), 'Rathausplatz');
  if (withImage) {
    const file = new File(['x'], 'rathaus.png', { type: 'image/png' });
    await userEvent.upload(screen.getByTestId('station-image-input'), file);
    expect(screen.getByRole('img', { name: 'Vorschau des Stations-Titelbilds' })).toBeInTheDocument();
  }
};

describe('StationCreate', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // jsdom has no object URLs.
    URL.createObjectURL = vi.fn(() => 'blob:preview');
    URL.revokeObjectURL = vi.fn();
    vi.mocked(geocode.getGeocodeConfig).mockResolvedValue({
      typeahead: true,
      sources: ['photon'],
      centre: null,
    });
  });

  it('with an address search, the help line starts with the search', async () => {
    renderCreate('/stations/new');
    expect(await screen.findByText(/^Adresse suchen, Pin auf der Karte/)).toBeInTheDocument();
  });

  it('without an address search, the help line tells to set the pin on the map', async () => {
    vi.mocked(geocode.getGeocodeConfig).mockResolvedValue({ typeahead: false, sources: [], centre: null });
    renderCreate('/stations/new');
    expect(await screen.findByText(/^Pin auf der Karte verschieben oder klicken/)).toBeInTheDocument();
    expect(screen.queryByText(/^Adresse suchen/)).not.toBeInTheDocument();
  });

  it('offers the title image on the create page', () => {
    renderCreate('/stations/new');
    expect(screen.getByText('Titelbild')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Bild hochladen' })).toBeInTheDocument();
  });

  it('from a rallye: saves the station, uploads the image, and opens the rallye with the station open', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: link });
    vi.mocked(apiClient.put).mockResolvedValueOnce({ data: station });
    renderCreate('/stations/new?rallyeId=r1');

    await fillTitleAndImage(true);
    await userEvent.click(screen.getByRole('button', { name: 'Station erstellen' }));

    expect(await screen.findByText(/Rallye page \?open=link-1/)).toBeInTheDocument();
    expect(apiClient.post).toHaveBeenCalledWith('/api/orgs/test-org/rallyes/r1/stations', expect.anything());
    expect(apiClient.put).toHaveBeenCalledWith('/api/orgs/test-org/stations/s1/title-image', expect.any(FormData));
  });

  it('from a rallye without an image: no upload, and the rallye opens with the station open', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: link });
    renderCreate('/stations/new?rallyeId=r1');

    await fillTitleAndImage(false);
    await userEvent.click(screen.getByRole('button', { name: 'Station erstellen' }));

    expect(await screen.findByText(/Rallye page \?open=link-1/)).toBeInTheDocument();
    expect(apiClient.put).not.toHaveBeenCalled();
  });

  it('from the library: the station page opens after saving', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: station });
    vi.mocked(apiClient.put).mockResolvedValueOnce({ data: station });
    renderCreate('/stations/new');

    await fillTitleAndImage(true);
    await userEvent.click(screen.getByRole('button', { name: 'Station erstellen' }));

    expect(await screen.findByText(/Station page/)).toBeInTheDocument();
    expect(apiClient.post).toHaveBeenCalledWith('/api/orgs/test-org/stations/', expect.anything());
    expect(apiClient.put).toHaveBeenCalledTimes(1);
  });

  it('a failed image upload leaves the form: the station page opens and carries the notice, and nothing is created twice', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: link });
    vi.mocked(apiClient.put).mockRejectedValueOnce(new Error('too large'));
    renderCreate('/stations/new?rallyeId=r1');

    await fillTitleAndImage(true);
    await userEvent.click(screen.getByRole('button', { name: 'Station erstellen' }));

    expect(await screen.findByText(/Station page \?rallyeId=r1 \{"titleImageFailed":true\}/)).toBeInTheDocument();
    await waitFor(() => expect(apiClient.post).toHaveBeenCalledTimes(1));
  });

  // The reason of a coded refusal reaches the station page.
  it.each([
    [415, 'IMAGE_NOT_DECODABLE'],
    [413, 'IMAGE_TOO_LARGE'],
  ])('a title image refused with %i %s: the station page gets the code', async (status, code) => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: station });
    vi.mocked(apiClient.put).mockRejectedValueOnce(new ApiError(status, 'server text', code));
    renderCreate('/stations/new');

    await fillTitleAndImage(true);
    await userEvent.click(screen.getByRole('button', { name: 'Station erstellen' }));

    expect(await screen.findByText(/Station page/)).toHaveTextContent(
      `{"titleImageFailed":true,"titleImageRefusal":"${code}"}`,
    );
    expect(apiClient.post).toHaveBeenCalledTimes(1);
  });

  // The station page learns that the station is new.
  it('from the library: the station page is told that the station was created', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: station });
    renderCreate('/stations/new');

    await fillTitleAndImage(false);
    await userEvent.click(screen.getByRole('button', { name: 'Station erstellen' }));

    expect(await screen.findByText(/Station page/)).toHaveTextContent('{"created":true}');
  });

  // A tip link without a label is refused once, at the tips.
  it('a tip link without a label: one refusal at the tips, the field marked and focused', async () => {
    renderCreate('/stations/new');

    await fillTitleAndImage(false);
    await userEvent.type(screen.getByRole('textbox', { name: 'Popup-Überschrift (Deutsch)' }), 'Tipps');
    await userEvent.click(screen.getByRole('button', { name: 'Link hinzufügen' }));
    await userEvent.type(screen.getByRole('textbox', { name: 'URL' }), 'https://example.org');
    await userEvent.click(screen.getByRole('button', { name: 'Station erstellen' }));

    const label = screen.getByRole('textbox', { name: 'Link 1: Beschriftung (Deutsch)' });
    await waitFor(() => expect(label).toHaveAttribute('aria-invalid', 'true'));
    expect(label).toHaveFocus();
    expect(label).toHaveAccessibleDescription(/Jeder Link braucht eine Beschriftung\./);
    expect(screen.getAllByText('Jeder Link braucht eine Beschriftung.')).toHaveLength(1);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(apiClient.post).not.toHaveBeenCalled();
  });
});
