import { beforeEach, describe, expect, it, vi } from 'vitest';
import apiClient from '../api/client';
import i18n from '../i18n';
import {
  forwardGeocode,
  getGeocodeConfig,
  offersAddressSearch,
  resetGeocodeConfig,
  reverseGeocode,
  shortAddress,
  suggestPlaces,
  type GeocodeHit,
} from './geocode';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return { ...actual, default: { get: vi.fn() } };
});

const get = vi.mocked(apiClient.get);
const BIAS = { lat: 48.3705, lng: 10.8978 };

const hit = (parts: Partial<GeocodeHit>): GeocodeHit => ({
  lat: 48.37,
  lng: 10.9,
  kind: 'poi',
  category: 'other',
  source: 'local',
  ...parts,
});

describe('geocode', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de');
    get.mockReset();
    resetGeocodeConfig();
  });

  // The raw address repeats districts ("Hofgarten, Innenstadt, Innenstadt, St. Ulrich-Dom, Innenstadt, Augsburg, Bayern, Deutschland").
  describe('shortAddress', () => {
    it('names a place with its town and no district, state or country', () => {
      expect(shortAddress({ name: 'Hofgarten', postcode: '86152', city: 'Augsburg' })).toBe('Hofgarten, Augsburg');
    });

    it('gives a house its postcode', () => {
      expect(shortAddress({ street: 'Frauentorstraße', housenumber: '1', postcode: '86152', city: 'Augsburg' })).toBe(
        'Frauentorstraße 1, 86152 Augsburg',
      );
    });

    it('puts the name before the address of a place', () => {
      expect(
        shortAddress({ name: 'Rathaus', street: 'Rathausplatz', housenumber: '2', postcode: '86150', city: 'Augsburg' }),
      ).toBe('Rathaus, Rathausplatz 2, 86150 Augsburg');
    });

    it('does not repeat a name that is the street', () => {
      expect(shortAddress({ name: 'Maximilianstraße', street: 'Maximilianstraße', city: 'Friedberg' })).toBe(
        'Maximilianstraße, Friedberg',
      );
    });

    it('keeps a street without number free of the postcode', () => {
      expect(shortAddress({ street: 'Maximilianstraße', postcode: '86150', city: 'Augsburg' })).toBe(
        'Maximilianstraße, Augsburg',
      );
    });
  });

  it('asks this installation for suggestions, with the point the creator looks at', async () => {
    get.mockResolvedValue({
      data: {
        results: [
          hit({ name: 'Hofgarten', city: 'Augsburg', district: 'Innenstadt', category: 'leisure' }),
          hit({ kind: 'street', category: 'street', street: 'Hofgartenstraße', postcode: '86179', city: 'Augsburg' }),
        ],
      },
    });
    const signal = new AbortController().signal;
    expect(await suggestPlaces(' Hofgar ', BIAS, signal)).toEqual([
      {
        lat: 48.37,
        lng: 10.9,
        displayName: 'Hofgarten, Augsburg',
        district: 'Innenstadt',
        category: 'leisure',
        origin: 'region',
      },
      { lat: 48.37, lng: 10.9, displayName: 'Hofgartenstraße, Augsburg', category: 'street', origin: 'region' },
    ]);
    expect(get).toHaveBeenCalledWith('/api/geocode/suggest', {
      params: { q: 'Hofgar', lat: 48.3705, lng: 10.8978, lang: 'de' },
      signal,
    });
  });

  it('shows hits that read the same once, and keeps different districts apart', async () => {
    get.mockResolvedValue({
      data: {
        results: [
          hit({ kind: 'street', category: 'street', street: 'Rathausplatz', city: 'Augsburg' }),
          hit({ kind: 'street', category: 'street', street: 'Rathausplatz', city: 'Augsburg' }),
          hit({ kind: 'street', category: 'street', street: 'Rathausplatz', city: 'Augsburg', district: 'Haunstetten' }),
          hit({}),
        ],
      },
    });
    const { results, widened } = await forwardGeocode('Rathausplatz', BIAS);
    expect(widened).toBe(false);
    expect(results.map((result) => [result.displayName, result.district])).toEqual([
      ['Rathausplatz, Augsburg', undefined],
      ['Rathausplatz, Augsburg', 'Haunstetten'],
    ]);
    expect(get.mock.calls[0][0]).toBe('/api/geocode/search');
  });

  it('skips the request for an empty text and passes a failed search on', async () => {
    expect(await forwardGeocode('   ', BIAS)).toEqual({ results: [], widened: false });
    expect(await suggestPlaces('', BIAS)).toEqual([]);
    expect(get).not.toHaveBeenCalled();
    get.mockRejectedValue(new Error('502'));
    await expect(forwardGeocode('Dom', BIAS)).rejects.toThrow('502');
  });

  it('passes on that the hits come from outside the region', async () => {
    get.mockResolvedValue({
      data: {
        results: [hit({ name: 'Ausländerbehörde', city: 'München', source: 'nominatim', origin: 'outside' })],
        widened: true,
      },
    });
    expect(await forwardGeocode('Ausländerbehörde', BIAS)).toEqual({
      results: [
        { lat: 48.37, lng: 10.9, displayName: 'Ausländerbehörde, München', category: 'other', origin: 'outside' },
      ],
      widened: true,
    });
  });

  // A near-miss of the index makes the search count as not widened.
  it('keeps the origin of each hit, also when the search counts as not widened', async () => {
    get.mockResolvedValue({
      data: {
        results: [
          hit({ name: 'Viktualienhalle', city: 'Augsburg', origin: 'region' }),
          hit({ name: 'Viktualienmarkt', city: 'München', source: 'nominatim', origin: 'outside' }),
        ],
        widened: false,
      },
    });
    const { results, widened } = await forwardGeocode('Viktualienmarkt', BIAS);
    expect(widened).toBe(false);
    expect(results.map((result) => [result.displayName, result.origin])).toEqual([
      ['Viktualienhalle, Augsburg', 'region'],
      ['Viktualienmarkt, München', 'outside'],
    ]);
  });

  it('reads the origin from the flag widened when an older server sends none', async () => {
    get.mockResolvedValueOnce({ data: { results: [hit({ name: 'Dom', city: 'Augsburg' })] } });
    expect((await forwardGeocode('Dom', BIAS)).results[0].origin).toBe('region');
    get.mockResolvedValueOnce({ data: { results: [hit({ name: 'Dom', city: 'Köln' })], widened: true } });
    expect((await forwardGeocode('Dom', BIAS)).results[0].origin).toBe('outside');
    get.mockResolvedValueOnce({ data: { results: [hit({ name: 'Dom', city: 'Köln' })], widened: true } });
    expect((await suggestPlaces('Dom', BIAS))[0].origin).toBe('outside');
  });

  it('gives a pin the short address, or null when nothing is known or the request fails', async () => {
    get.mockResolvedValueOnce({
      data: { result: hit({ kind: 'address', street: 'Domplatz', housenumber: '1', postcode: '86152', city: 'Augsburg' }) },
    });
    expect(await reverseGeocode(48.37, 10.89)).toBe('Domplatz 1, 86152 Augsburg');
    expect(get).toHaveBeenCalledWith('/api/geocode/reverse', { params: { lat: 48.37, lng: 10.89, lang: 'de' } });
    get.mockResolvedValueOnce({ data: { result: null } });
    expect(await reverseGeocode(48.37, 10.89)).toBeNull();
    get.mockRejectedValueOnce(new Error('down'));
    expect(await reverseGeocode(48.37, 10.89)).toBeNull();
  });

  it('asks for the configuration once, and falls back to a plain search when it fails', async () => {
    get.mockResolvedValue({ data: { typeahead: true, sources: ['local'] } });
    expect(await getGeocodeConfig()).toEqual({ typeahead: true, sources: ['local'] });
    await getGeocodeConfig();
    expect(get).toHaveBeenCalledTimes(1);

    resetGeocodeConfig();
    get.mockRejectedValue(new Error('down'));
    expect(await getGeocodeConfig()).toEqual({ typeahead: false, sources: [], centre: null, unknown: true });
  });

  it('offers the search field unless the installation answered that it has no source', () => {
    expect(offersAddressSearch(null)).toBe(true);
    expect(offersAddressSearch({ typeahead: false, sources: ['nominatim'] })).toBe(true);
    expect(offersAddressSearch({ typeahead: false, sources: [], centre: null, unknown: true })).toBe(true);
    expect(offersAddressSearch({ typeahead: false, sources: [] })).toBe(false);
  });
});
