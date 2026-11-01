import React, { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'vitest-axe';

import '../i18n';
import i18n from '../i18n';
import MapPicker from './MapPicker';
import { TileLayer } from './Leaflet';
import * as geocode from '../utils/geocode';
import type { GeocodeResult } from '../utils/geocode';

// Leaflet needs a real layout. The search field is what these tests are about.
const { setView } = vi.hoisted(() => ({ setView: vi.fn() }));
vi.mock('./Leaflet', () => ({
  // Mirrors the real MapContainer: a name turns the container into a named
  // region.
  MapContainer: ({ children, name }: { children?: React.ReactNode; name?: string }) => (
    <div data-testid="map" role={name ? 'region' : undefined} aria-label={name}>
      {children}
    </div>
  ),
  TileLayer: vi.fn(() => null),
  ZoomControl: () => null,
  Marker: () => <div data-testid="pin" />,
}));
vi.mock('./leafletHooks', () => ({
  useMap: () => ({ flyTo: vi.fn(), setView, getZoom: () => 13, attributionControl: { setPrefix: vi.fn() } }),
  useMapEvents: () => null,
}));

vi.mock('../utils/geocode', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../utils/geocode')>();
  return {
    ...actual,
    getGeocodeConfig: vi.fn(),
    suggestPlaces: vi.fn(),
    forwardGeocode: vi.fn(),
    reverseGeocode: vi.fn(),
  };
});

const config = vi.mocked(geocode.getGeocodeConfig);
const suggest = vi.mocked(geocode.suggestPlaces);
const search = vi.mocked(geocode.forwardGeocode);

const HOFGARTEN: GeocodeResult = {
  lat: 48.3731,
  lng: 10.896,
  displayName: 'Hofgarten, Augsburg',
  district: 'Innenstadt',
  category: 'leisure',
  origin: 'region',
};
const HOFGARTENSTRASSE: GeocodeResult = {
  lat: 48.31,
  lng: 10.9,
  displayName: 'Hofgartenstraße, Augsburg',
  category: 'street',
  origin: 'region',
};
const VIKTUALIENMARKT_MUNICH: GeocodeResult = {
  lat: 48.1351,
  lng: 11.5762,
  displayName: 'Viktualienmarkt, München',
  category: 'other',
  origin: 'outside',
};

/** The form around the picker: it keeps the confirmed address, as StationCreate does. */
const Harness: React.FC<{ onPick?: (next: { lat: number; lng: number; address: string }) => void }> = ({ onPick }) => {
  const [value, setValue] = useState<{ lat: number | null; lng: number | null; address: string }>({
    lat: null,
    lng: null,
    address: '',
  });
  const [pending, setPending] = useState(false);
  return (
    <>
      <MapPicker
        lat={value.lat}
        lng={value.lng}
        address={value.address}
        onChange={(next) => {
          setValue(next);
          onPick?.(next);
        }}
        searchLabel="Adresse suchen"
        onPendingChange={setPending}
      />
      <span data-testid="pending">{String(pending)}</span>
    </>
  );
};

const field = () => screen.getByRole('combobox', { name: 'Adresse suchen' });

describe('MapPicker address search', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de');
    config.mockReset();
    suggest.mockReset();
    search.mockReset();
  });

  // The map container takes the focus (Leaflet gives it
  // tabindex 0), so it needs a role and a name in the interface language.
  it('names the map as a region', () => {
    config.mockResolvedValue({ typeahead: false, sources: ['nominatim'] });
    render(<Harness />);

    expect(screen.getByRole('region', { name: 'Karte zur Wahl des Standorts' })).toBeInTheDocument();
  });

  describe('with suggestions while typing', () => {
    beforeEach(() => {
      config.mockResolvedValue({ typeahead: true, sources: ['local'] });
      suggest.mockResolvedValue([HOFGARTEN, HOFGARTENSTRASSE]);
    });

    it('offers hits for the typed beginning, with the kind of place and the district', async () => {
      render(<Harness />);
      await screen.findByText(/Ab zwei Zeichen erscheinen Vorschläge/);
      await userEvent.type(field(), 'Hofgar');

      const list = await screen.findByRole('listbox', { name: 'Treffer, bitte auswählen' });
      const options = within(list).getAllByRole('option');
      expect(options.map((option) => option.textContent)).toEqual([
        'Hofgarten, AugsburgPark und Freizeit · Innenstadt',
        'Hofgartenstraße, AugsburgStraße',
      ]);
      expect(field()).toHaveAttribute('aria-expanded', 'true');
      // One request for the pause after the last key, not one per key.
      expect(suggest).toHaveBeenCalledTimes(1);
      expect(suggest.mock.calls[0][0]).toBe('Hofgar');
      expect(search).not.toHaveBeenCalled();
    });

    it('tells a screen reader how many hits the list holds', async () => {
      render(<Harness />);
      await screen.findByText(/Ab zwei Zeichen/);
      await userEvent.type(field(), 'Hofgar');
      const announcement = await screen.findByText('2 Treffer. Mit den Pfeiltasten auswählen.');
      expect(announcement).toHaveAttribute('role', 'status');
      expect(announcement).toHaveAttribute('aria-live', 'polite');
    });

    it('picks a hit with the keyboard and stores the short address and the position', async () => {
      const onPick = vi.fn();
      render(<Harness onPick={onPick} />);
      await screen.findByText(/Ab zwei Zeichen/);
      await userEvent.type(field(), 'Hofgar');
      await screen.findByRole('listbox');
      expect(screen.getByTestId('pending')).toHaveTextContent('true');

      await userEvent.keyboard('{ArrowDown}');
      expect(field()).toHaveAttribute('aria-activedescendant', screen.getAllByRole('option')[0].id);
      await userEvent.keyboard('{Enter}');

      expect(onPick).toHaveBeenCalledWith({ lat: 48.3731, lng: 10.896, address: 'Hofgarten, Augsburg' });
      expect(field()).toHaveValue('Hofgarten, Augsburg');
      expect(screen.queryByRole('listbox')).toBeNull();
      expect(screen.getByTestId('pending')).toHaveTextContent('false');
      expect(screen.getByTestId('pin')).toBeInTheDocument();
    });

    // Fields must not be red before the user did anything wrong.
    it('shows no red hint while the creator types, and shows it after they leave the field', async () => {
      render(<Harness />);
      await screen.findByText(/Ab zwei Zeichen/);
      await userEvent.type(field(), 'Hofgar');
      await screen.findByRole('listbox');
      expect(screen.getByTestId('pending')).toHaveTextContent('true');
      expect(screen.queryByText(/Adresse noch nicht gewählt/)).toBeNull();

      await userEvent.keyboard('{Escape}');
      // Out of the picker: past the clear button and the imprint link under the map.
      await userEvent.tab();
      await userEvent.tab();
      await userEvent.tab();
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'Adresse noch nicht gewählt. Einen Vorschlag wählen, oder Enter drücken und dann einen Treffer wählen.',
      );

      await userEvent.click(field());
      expect(screen.queryByText(/Adresse noch nicht gewählt/)).toBeNull();
    });

    it('closes the list with Escape and keeps the text', async () => {
      render(<Harness />);
      await screen.findByText(/Ab zwei Zeichen/);
      await userEvent.type(field(), 'Hofgar');
      await screen.findByRole('listbox');
      await userEvent.keyboard('{Escape}');
      expect(screen.queryByRole('listbox')).toBeNull();
      expect(field()).toHaveValue('Hofgar');
    });

    it('does not ask for one letter', async () => {
      render(<Harness />);
      await screen.findByText(/Ab zwei Zeichen/);
      await userEvent.type(field(), 'H');
      await new Promise((resolve) => setTimeout(resolve, 350));
      expect(suggest).not.toHaveBeenCalled();
    });

    it('drops the answer for a text the creator typed over', async () => {
      let answerFirst: (results: GeocodeResult[]) => void = () => {};
      suggest.mockReset();
      suggest
        .mockImplementationOnce(() => new Promise((resolve) => (answerFirst = resolve)))
        .mockResolvedValueOnce([HOFGARTENSTRASSE]);
      render(<Harness />);
      await screen.findByText(/Ab zwei Zeichen/);
      await userEvent.type(field(), 'Hof');
      await waitFor(() => expect(suggest).toHaveBeenCalledTimes(1));
      await userEvent.type(field(), 'gartens');
      await screen.findByRole('option', { name: /Hofgartenstraße/ });
      answerFirst([HOFGARTEN]);
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(screen.getAllByRole('option')).toHaveLength(1);
      expect(suggest.mock.calls[0][2]?.aborted).toBe(true);
    });

    it('sends the whole text on Enter without a highlighted hit', async () => {
      search.mockResolvedValue({ results: [HOFGARTEN, HOFGARTENSTRASSE], widened: false });
      render(<Harness />);
      await screen.findByText(/Ab zwei Zeichen/);
      await userEvent.type(field(), 'Hofgarten{Enter}');
      await waitFor(() => expect(search).toHaveBeenCalledWith('Hofgarten', { lat: 48.3705, lng: 10.8978 }));
    });

    it('has no accessibility violations with the list open', async () => {
      const { container } = render(<Harness />);
      await screen.findByText(/Ab zwei Zeichen/);
      await userEvent.type(field(), 'Hofgar');
      await screen.findByRole('listbox');
      expect(await axe(container)).toHaveNoViolations();
    });
  });

  describe('without suggestions while typing', () => {
    beforeEach(() => {
      config.mockResolvedValue({ typeahead: false, sources: ['nominatim'] });
    });

    it('asks nothing while typing and says how to search', async () => {
      render(<Harness />);
      await screen.findByText(/Nominatim/);
      await userEvent.type(field(), 'Hofgarten');
      await new Promise((resolve) => setTimeout(resolve, 350));
      expect(suggest).not.toHaveBeenCalled();
      expect(screen.getByText(/dann Enter drücken oder die Lupe antippen/)).toBeInTheDocument();
    });

    it('lists the hits of a sent search', async () => {
      search.mockResolvedValue({ results: [HOFGARTEN, HOFGARTENSTRASSE], widened: false });
      render(<Harness />);
      await userEvent.type(field(), 'Hofgarten{Enter}');
      expect(await screen.findAllByRole('option')).toHaveLength(2);
    });

    it('says that the hits come from other places, and does not take the only one', async () => {
      const onPick = vi.fn();
      search.mockResolvedValue({
        results: [
          {
            lat: 48.15,
            lng: 11.56,
            displayName: 'Ausländerbehörde, Seidlstraße 27, 80335 München',
            category: 'authority',
            origin: 'outside',
          },
        ],
        widened: true,
      });
      render(<Harness onPick={onPick} />);
      await userEvent.type(field(), 'Ausländerbehörde{Enter}');
      expect(await screen.findAllByRole('option')).toHaveLength(1);
      expect(within(screen.getByRole('listbox')).getByText('Treffer aus anderen Orten')).toBeInTheDocument();
      expect(screen.getByText('1 Treffer aus anderen Orten. Mit den Pfeiltasten auswählen.')).toHaveAttribute(
        'role',
        'status',
      );
      expect(onPick).not.toHaveBeenCalled();
    });

    // The index nearly always gives a near-miss, so the hits from other places need their own heading.
    it('lists the region first and the hits from other places under their own heading', async () => {
      search.mockResolvedValue({ results: [HOFGARTEN, VIKTUALIENMARKT_MUNICH, HOFGARTENSTRASSE], widened: false });
      const { container } = render(<Harness />);
      await userEvent.type(field(), 'Viktualienmarkt{Enter}');
      const list = await screen.findByRole('listbox', { name: 'Treffer, bitte auswählen' });
      expect(within(list).getAllByRole('option').map((option) => option.textContent)).toEqual([
        'Hofgarten, AugsburgPark und Freizeit · Innenstadt',
        'Hofgartenstraße, AugsburgStraße',
        'Viktualienmarkt, München',
      ]);
      // One heading only, and it stands above the hit from the other place.
      const heading = within(list).getByText('Treffer aus anderen Orten');
      expect(within(list).getAllByText('Treffer aus anderen Orten')).toHaveLength(1);
      expect(heading.compareDocumentPosition(within(list).getByRole('option', { name: /Viktualienmarkt/ }))).toBe(
        Node.DOCUMENT_POSITION_FOLLOWING,
      );
      expect(heading.compareDocumentPosition(within(list).getByRole('option', { name: /Hofgartenstraße/ }))).toBe(
        Node.DOCUMENT_POSITION_PRECEDING,
      );
      expect(await axe(container)).toHaveNoViolations();
    });

    it('tells a screen reader the hits per group', async () => {
      search.mockResolvedValue({ results: [VIKTUALIENMARKT_MUNICH, HOFGARTEN, HOFGARTENSTRASSE], widened: false });
      render(<Harness />);
      await userEvent.type(field(), 'Viktualienmarkt{Enter}');
      await screen.findByRole('listbox');
      expect(
        screen.getByText('2 Treffer in der Region, 1 Treffer aus anderen Orten. Mit den Pfeiltasten auswählen.'),
      ).toHaveAttribute('role', 'status');
    });

    it('tells it in English as well', async () => {
      await i18n.changeLanguage('en');
      search.mockResolvedValue({ results: [HOFGARTEN, VIKTUALIENMARKT_MUNICH], widened: false });
      render(<Harness />);
      await userEvent.type(field(), 'Viktualienmarkt{Enter}');
      await screen.findByRole('listbox');
      expect(within(screen.getByRole('listbox')).getByText('Matches from other places')).toBeInTheDocument();
      expect(
        screen.getByText('1 match in the region, 1 match from other places. Use the arrow keys to pick one.'),
      ).toHaveAttribute('role', 'status');
    });

    it('does not take the only hit when it lies in another place, even without the flag widened', async () => {
      const onPick = vi.fn();
      search.mockResolvedValue({ results: [VIKTUALIENMARKT_MUNICH], widened: false });
      render(<Harness onPick={onPick} />);
      await userEvent.type(field(), 'Viktualienmarkt{Enter}');
      expect(await screen.findAllByRole('option')).toHaveLength(1);
      expect(onPick).not.toHaveBeenCalled();
    });

    it('takes the only hit at once', async () => {
      const onPick = vi.fn();
      search.mockResolvedValue({ results: [HOFGARTEN], widened: false });
      render(<Harness onPick={onPick} />);
      await userEvent.type(field(), 'Hofgarten');
      await userEvent.click(screen.getByRole('button', { name: 'Adresse suchen' }));
      await waitFor(() => expect(onPick).toHaveBeenCalledWith({ lat: 48.3731, lng: 10.896, address: 'Hofgarten, Augsburg' }));
    });

    it('says what to do when nothing is found', async () => {
      search.mockResolvedValue({ results: [], widened: false });
      render(<Harness />);
      await userEvent.type(field(), 'Ausländerbehörde{Enter}');
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'Keine Adresse gefunden. Bitte Straße und Hausnummer eingeben oder den Pin auf der Karte setzen.',
      );
      expect(screen.getByTestId('pending')).toHaveTextContent('true');
    });

    it('says that the search failed', async () => {
      search.mockRejectedValue(new Error('502'));
      render(<Harness />);
      await userEvent.type(field(), 'Hofgarten{Enter}');
      expect(await screen.findByRole('alert')).toHaveTextContent('Adresssuche fehlgeschlagen.');
    });

    it('names the data and the service', async () => {
      render(<Harness />);
      expect(await screen.findByText('Adresssuche: © OpenStreetMap-Mitwirkende, Nominatim')).toBeInTheDocument();
    });
  });
});

describe('MapPicker start point', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de');
    config.mockReset();
    search.mockReset();
    setView.mockReset();
  });

  it('loads tiles from the configured server, and none before the config has answered', async () => {
    let answer: (value: geocode.GeocodeConfig) => void = () => {};
    config.mockImplementationOnce(() => new Promise((resolve) => (answer = resolve as typeof answer)));
    render(<Harness />);
    const tiles = vi.mocked(TileLayer);
    const before = tiles.mock.calls.length;
    // No tile request until the installation has said where the tiles come from.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(tiles.mock.calls.length).toBe(before);

    answer({
      typeahead: false,
      sources: [],
      tile_url: 'https://tiles.example.org/{z}/{x}/{y}.png',
      tile_attribution: '',
    });
    await waitFor(() => expect(tiles.mock.calls.length).toBe(before + 1));
    expect(tiles.mock.calls.at(-1)?.[0]).toEqual({
      url: 'https://tiles.example.org/{z}/{x}/{y}.png',
      attribution: expect.stringContaining('tiles.example.org'),
    });
  });

  // The exact JSON test_geocode.py asserts for GET /api/geocode/config by
  // default: the field names the server sends reach the map unchanged.
  it('reads the tile fields of the exact answer the server sends', async () => {
    config.mockResolvedValue(
      JSON.parse(
        '{"typeahead": false, "sources": [], "centre": null,' +
          ' "tile_url": "https://tile.openstreetmap.de/{z}/{x}/{y}.png", "tile_attribution": ""}',
      ) as geocode.GeocodeConfig,
    );
    render(<Harness />);
    await waitFor(() =>
      expect(vi.mocked(TileLayer).mock.calls.at(-1)?.[0]).toEqual({
        url: 'https://tile.openstreetmap.de/{z}/{x}/{y}.png',
        attribution: expect.stringContaining('FOSSGIS e.V., CC BY-SA 2.0'),
      }),
    );
    expect(vi.mocked(TileLayer).mock.calls.at(-1)?.[0].attribution).toContain(
      'https://www.openstreetmap.org/fixthemap',
    );
  });

  it('takes an attribution override as it stands', async () => {
    config.mockResolvedValue({ typeahead: false, sources: [], tile_attribution: '&copy; Hier' });
    render(<Harness />);
    await waitFor(() =>
      expect(vi.mocked(TileLayer).mock.calls.at(-1)?.[0]).toEqual(
        expect.objectContaining({ attribution: '&copy; Hier' }),
      ),
    );
  });

  it('requests no tile and says so when the installation did not answer', async () => {
    const before = vi.mocked(TileLayer).mock.calls.length;
    config.mockResolvedValue({ typeahead: false, sources: [], centre: null, unknown: true });
    render(<Harness />);
    expect(await screen.findByText('Die Karte ist gerade nicht verfügbar.')).toBeInTheDocument();
    expect(vi.mocked(TileLayer).mock.calls.length).toBe(before);
  });

  // The FOSSGIS terms ask for an operator contact within one click of the map.
  it('offers the imprint link directly under the map', async () => {
    config.mockResolvedValue({ typeahead: false, sources: [] });
    render(<Harness />);
    const imprint = await screen.findByRole('link', { name: 'Impressum' });
    expect(imprint).toHaveAttribute('href', '/legal/impressum');
    expect(screen.getByTestId('map').compareDocumentPosition(imprint)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it('starts the map and the search at the point the installation names', async () => {
    config.mockResolvedValue({ typeahead: false, sources: ['nominatim'], centre: { lat: 48.1351, lng: 11.582 } });
    search.mockResolvedValue({ results: [], widened: false });
    render(<Harness />);
    await waitFor(() => expect(setView).toHaveBeenCalledWith([48.1351, 11.582], 13, { animate: false }));
    await userEvent.type(field(), 'Marienplatz{Enter}');
    await waitFor(() => expect(search).toHaveBeenCalledWith('Marienplatz', { lat: 48.1351, lng: 11.582 }));
  });

  it('falls back to Augsburg when the installation names no point', async () => {
    config.mockResolvedValue({ typeahead: false, sources: ['nominatim'], centre: null });
    search.mockResolvedValue({ results: [], widened: false });
    render(<Harness />);
    await userEvent.type(field(), 'Dom{Enter}');
    await waitFor(() => expect(search).toHaveBeenCalledWith('Dom', { lat: 48.3705, lng: 10.8978 }));
  });
});

// Without a place index and without an outside service no search can find an address.
describe('MapPicker without an address search', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de');
    config.mockReset();
    search.mockReset();
  });

  it('says that the installation has no address search and offers no search field', async () => {
    config.mockResolvedValue({ typeahead: false, sources: [], centre: null });
    render(<Harness />);
    expect(
      await screen.findByText('Für diese Installation ist keine Adresssuche eingerichtet. Setze den Pin auf der Karte.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Adresse suchen' })).toBeNull();
    expect(screen.queryByText(/Adresssuche: ©/)).toBeNull();
    // The map stays, so the pin can be set.
    expect(screen.getByTestId('map')).toBeInTheDocument();
    expect(search).not.toHaveBeenCalled();
  });

  it('says it in English as well', async () => {
    await i18n.changeLanguage('en');
    config.mockResolvedValue({ typeahead: false, sources: [], centre: null });
    render(<Harness />);
    expect(
      await screen.findByText('This installation has no address search. Place the pin on the map.'),
    ).toBeInTheDocument();
  });

  it('keeps the search field when the installation did not answer', async () => {
    config.mockResolvedValue({ typeahead: false, sources: [], centre: null, unknown: true });
    render(<Harness />);
    await waitFor(() => expect(config).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(field()).toBeInTheDocument();
    expect(screen.queryByText(/keine Adresssuche eingerichtet/)).toBeNull();
  });
});
