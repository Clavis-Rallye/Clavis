import { describe, expect, it } from 'vitest';
import type { GeocodeConfig } from './geocode';
import { DEFAULT_TILE_URL, matchTileRequest, tileAttribution, tilesFromConfig } from './mapTiles';

describe('tileAttribution', () => {
  it('credits the data and the style for the FOSSGIS tiles, with a link to report errors', () => {
    expect(tileAttribution('https://tile.openstreetmap.de/{z}/{x}/{y}.png')).toBe(
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap-Mitwirkende</a>' +
        ' · FOSSGIS e.V., CC BY-SA 2.0' +
        ' · <a href="https://www.openstreetmap.org/fixthemap">Fehler melden</a>',
    );
  });

  it('credits the data only for the tiles of the OpenStreetMap Foundation, as before', () => {
    expect(tileAttribution('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png')).toBe(
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    );
  });

  it('credits the data and names the host of any other server', () => {
    expect(tileAttribution('https://tiles.example.org/{z}/{x}/{y}.png')).toBe(
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap-Mitwirkende</a> · tiles.example.org',
    );
  });
});

describe('tilesFromConfig', () => {
  it('takes the fields of the config exactly as the server sends them', () => {
    // The JSON the backend test asserts for GET /api/geocode/config.
    const answer = {
      typeahead: false,
      sources: [],
      centre: null,
      tile_url: 'https://tiles.example.org/{z}/{x}/{y}.png',
      tile_attribution: '',
    };
    expect(tilesFromConfig(answer)).toEqual({
      url: 'https://tiles.example.org/{z}/{x}/{y}.png',
      attribution: tileAttribution('https://tiles.example.org/{z}/{x}/{y}.png'),
    });
  });

  it('falls back to the default tiles when an older server sends no tile field', () => {
    const older: GeocodeConfig = { typeahead: false, sources: [] };
    expect(tilesFromConfig(older)?.url).toBe(DEFAULT_TILE_URL);
  });

  it('asks for no tile while the config has not answered', () => {
    expect(tilesFromConfig(null)).toBeNull();
  });

  it('asks for no tile when the installation did not answer, instead of one from a server nobody configured', () => {
    const failed: GeocodeConfig = { typeahead: false, sources: [], unknown: true };
    expect(tilesFromConfig(failed)).toBeNull();
  });

  it('takes an attribution override as it stands', () => {
    const tiles = tilesFromConfig({ tile_url: 'https://tile.openstreetmap.de/{z}/{x}/{y}.png', tile_attribution: '&copy; Hier' });
    expect(tiles?.attribution).toBe('&copy; Hier');
  });

  it('derives the attribution from the host without an override', () => {
    expect(tilesFromConfig({ tile_url: 'https://tile.openstreetmap.de/{z}/{x}/{y}.png' })?.attribution).toBe(
      tileAttribution('https://tile.openstreetmap.de/{z}/{x}/{y}.png'),
    );
  });
});

// The shape matcher of the service worker's tile cache rule (vite.config.ts).
describe('matchTileRequest', () => {
  const tile = (href: string, destination: string | undefined = 'image') =>
    matchTileRequest({ url: new URL(href), request: { destination } });

  it('matches a plain z/x/y tile of another origin', () => {
    expect(tile('https://tile.openstreetmap.de/14/8680/5610.png')).toBe(true);
  });

  it('matches a tile without a file ending', () => {
    expect(tile('https://tiles.example.org/14/8680/5610')).toBe(true);
  });

  it('matches a tile with a query string', () => {
    expect(tile('https://tiles.example.org/14/8680/5610.png?key=value')).toBe(true);
  });

  it('matches a tile behind a path prefix, with a query string', () => {
    expect(tile('https://tiles.example.org/styles/basic/14/8680/5610.png?key=value')).toBe(true);
  });

  it('matches a retina tile', () => {
    expect(tile('https://tiles.example.org/styles/basic/14/8680/5610@2x.png')).toBe(true);
  });

  it('leaves images of the own origin to the other rules', () => {
    expect(tile(`${self.location.origin}/icons/14/8680/5610.png`)).toBe(false);
  });

  it('matches no request that is not an image', () => {
    expect(tile('https://tiles.example.org/14/8680/5610.png', 'script')).toBe(false);
    expect(matchTileRequest({ url: new URL('https://tiles.example.org/14/8680/5610.png'), request: {} })).toBe(false);
  });

  it('matches no path that merely contains three numbers somewhere', () => {
    expect(tile('https://tiles.example.org/14/8680/5610/extra.png')).toBe(false);
    expect(tile('https://example.org/imprint/2026')).toBe(false);
  });

  // Workbox writes the pattern into the worker by its source text, where no
  // import of this module exists: the function must work on its own.
  it('works from its source text alone, as the built worker runs it', () => {
    const rebuilt = new Function(`return (${matchTileRequest.toString()})`)() as typeof matchTileRequest;
    expect(rebuilt({ url: new URL('https://t.example.org/a/1/2/3.png?k=v'), request: { destination: 'image' } })).toBe(true);
    expect(rebuilt({ url: new URL('https://t.example.org/a/1/2/3.png'), request: { destination: 'script' } })).toBe(false);
  });
});
