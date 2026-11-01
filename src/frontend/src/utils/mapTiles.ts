/**
 * The tiles of both maps.
 *
 * Which tile server an installation uses is a setting: MAP_TILE_URL on the
 * server, handed to the browser through /api/geocode/config. The attribution
 * follows the source, because the tile terms ask for it: the OpenStreetMap
 * data credit for every host, the style credit and a link to report map
 * errors for the FOSSGIS tiles, the host name for any other server. A
 * setting MAP_TILE_ATTRIBUTION may override all of it as HTML.
 */

/** The same default as the server: the FOSSGIS tiles, in Germany. */
export const DEFAULT_TILE_URL = 'https://tile.openstreetmap.de/{z}/{x}/{y}.png';

/** The host of a tile URL, or '' when the URL is not one. A `{s}.` subdomain placeholder does not count. */
function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^\{s\}\./, '');
  } catch {
    return '';
  }
}

/** The attribution that belongs to a tile server, as HTML. */
export function tileAttribution(url: string): string {
  const host = hostOf(url);
  const data = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap-Mitwirkende</a>';
  if (host === 'tile.openstreetmap.de') {
    return `${data} · FOSSGIS e.V., CC BY-SA 2.0 · <a href="https://www.openstreetmap.org/fixthemap">Fehler melden</a>`;
  }
  if (host === 'tile.openstreetmap.org') {
    return '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';
  }
  return `${data} · ${host}`;
}

/** A config answer: the tile URL the server sent, and the attribution override if it sent one. */
interface TileConfig {
  tile_url?: string;
  tile_attribution?: string;
  /** Set by the browser when the installation did not answer. */
  unknown?: true;
}

interface TileSource {
  url: string;
  attribution: string;
}

/**
 * The tile layer of both maps, from the installation's config: its URL, and its
 * attribution override or else the attribution of the tile host. Null while
 * the answer is pending and when the installation did not answer. The maps then
 * request no tile at all instead of one from a server nobody configured.
 */
export function tilesFromConfig(config: TileConfig | null): TileSource | null {
  if (!config || config.unknown) return null;
  const url = config.tile_url?.trim() || DEFAULT_TILE_URL;
  const override = config.tile_attribution?.trim();
  return { url, attribution: override || tileAttribution(url) };
}

/**
 * Whether a request looks like a map tile: an image from another origin whose
 * path ends in three numbers (z, x and y), at any depth below the host. A file
 * ending, a retina suffix such as @2x and a query string are optional. The tile
 * host is a runtime setting (MAP_TILE_URL) and the service worker is built
 * before it is known, so the worker matches tiles by shape, not by host.
 *
 * This is the `urlPattern` of the worker's tile cache rule in vite.config.ts.
 * Workbox writes a function pattern into the worker by its source text
 * (`toString()`), so this function must stay self-contained: it may use
 * nothing of this module or any import, only its argument and `globalThis`.
 */
export function matchTileRequest({
  url,
  request,
}: {
  url: URL;
  request: { destination?: string };
}): boolean {
  if (request.destination !== 'image') return false;
  const own = (globalThis as { location?: { origin?: string } }).location?.origin ?? '';
  if (own && url.origin === own) return false;
  return /\/\d+\/\d+\/\d+(?:@\d+(?:\.\d+)?x)?(?:\.[a-z0-9]+)?$/i.test(url.pathname);
}
