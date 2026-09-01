/**
 * Address search of the station form.
 *
 * The browser asks only this installation, at /api/geocode. The server answers
 * from its regional place index and from the outside service the operator
 * chose. So no map service sees the creator's browser, and the server decides
 * whether suggestions while typing are allowed.
 */
import apiClient from '../api/client';
import i18n from '../i18n';

export type GeocodeSource = 'local' | 'nominatim' | 'photon';

/**
 * Where a hit lies: 'region' for the place index and the bounded pass of the
 * outside service, 'outside' for the open pass.
 */
export type GeocodeOrigin = 'region' | 'outside';

/** One hit as the server sends it: address parts, no finished text. */
export interface GeocodeHit {
  lat: number;
  lng: number;
  kind: 'poi' | 'street' | 'address';
  category: string;
  name?: string | null;
  street?: string | null;
  housenumber?: string | null;
  postcode?: string | null;
  city?: string | null;
  district?: string | null;
  source: GeocodeSource;
  /** Missing from older servers: their `widened` flag then says it for all hits. */
  origin?: GeocodeOrigin;
}

export interface GeocodeResult {
  lat: number;
  lng: number;
  /** The short address that is stored on the station and shown to participants. */
  displayName: string;
  /** The district, for the result list only. It tells two equal short addresses apart. */
  district?: string;
  /** The kind of place, for the result list only: 'authority', 'stop', 'street'. */
  category?: string;
  /** Where the hit lies. The list shows hits from outside the region under their own heading. */
  origin: GeocodeOrigin;
}

/** The hits of a sent search. */
interface GeocodeSearch {
  results: GeocodeResult[];
  /** The region gave nothing, so every hit comes from outside it. */
  widened: boolean;
}

export interface GeocodeBias {
  lat: number;
  lng: number;
}

export interface GeocodeConfig {
  /** The installation may suggest while the creator types. */
  typeahead: boolean;
  /** Where hits come from, for the attribution line. Empty: the installation has no address search. */
  sources: GeocodeSource[];
  /**
   * Where the map starts before a pin exists: the operator's setting
   * GEOCODER_MAP_CENTRE, else the centre of the place index. Null when neither exists.
   */
  centre?: GeocodeBias | null;
  /** The tile server of both maps, MAP_TILE_URL. Missing from older servers: the frontend's default. */
  tile_url?: string;
  /** The tile attribution as HTML, MAP_TILE_ATTRIBUTION. Empty or missing: derived from the tile host. */
  tile_attribution?: string;
  /** Set by the browser when the installation did not answer: its sources are unknown. */
  unknown?: true;
}

/** Without an answer the field works as a plain search: Enter or the button. */
const FALLBACK_CONFIG: GeocodeConfig = { typeahead: false, sources: [], centre: null, unknown: true };

/**
 * Whether the station form offers the search field. Only an installation that
 * answered with no source has no address search: there every search would end
 * in "no address found". While the answer is pending, or when it failed, the
 * field stays.
 */
export function offersAddressSearch(config: GeocodeConfig | null): boolean {
  return config === null || config.unknown === true || config.sources.length > 0;
}

/**
 * Where the map of the station form starts when the installation names no
 * start point and holds no place index: Augsburg, where Clavis began.
 */
export const FALLBACK_CENTRE: [number, number] = [48.3705, 10.8978];

type AddressParts = Pick<GeocodeHit, 'name' | 'street' | 'housenumber' | 'postcode' | 'city'>;

/**
 * The short address from the parts of a hit: the place's name where it has
 * one, street and house number, and the town. The postcode stands only next to
 * a house number, where it is part of a postal address: "Hofgarten, Augsburg",
 * "Rathaus, Rathausplatz 2, 86150 Augsburg", "Maximilianstraße, Augsburg".
 * Districts, state and country never appear.
 */
export function shortAddress(parts: AddressParts): string {
  const street = [parts.street, parts.housenumber].filter(Boolean).join(' ');
  const town = [parts.housenumber ? parts.postcode : null, parts.city].filter(Boolean).join(' ');
  const name = parts.name && parts.name !== street && parts.name !== parts.street ? parts.name : '';
  return [name, street, town].filter(Boolean).join(', ');
}

function toResult(hit: GeocodeHit, widened: boolean): GeocodeResult {
  return {
    lat: hit.lat,
    lng: hit.lng,
    displayName: shortAddress(hit),
    ...(hit.district ? { district: hit.district } : {}),
    category: hit.category,
    origin: hit.origin ?? (widened ? 'outside' : 'region'),
  };
}

/** One of each: two hits that read the same in the list help nobody. */
function toResults(hits: GeocodeHit[], widened = false): GeocodeResult[] {
  const seen = new Set<string>();
  return hits
    .map((hit) => toResult(hit, widened))
    .filter((result) => result.displayName !== '')
    .filter((result) => {
      const key = `${result.displayName}|${result.district ?? ''}|${result.category ?? ''}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

let configRequest: Promise<GeocodeConfig> | null = null;

/** What the installation's search can do. Asked once per page load. */
export function getGeocodeConfig(): Promise<GeocodeConfig> {
  configRequest ??= apiClient
    .get<GeocodeConfig>('/api/geocode/config')
    .then(({ data }) => data)
    .catch(() => {
      configRequest = null;
      return FALLBACK_CONFIG;
    });
  return configRequest;
}

/** Hits while the creator types. The caller aborts the request of the text before. */
export async function suggestPlaces(query: string, bias: GeocodeBias, signal?: AbortSignal): Promise<GeocodeResult[]> {
  const q = query.trim();
  if (!q) return [];
  const { data } = await apiClient.get<{ results: GeocodeHit[]; widened?: boolean }>('/api/geocode/suggest', {
    params: { q, lat: bias.lat, lng: bias.lng, lang: i18n.language },
    signal,
  });
  return toResults(data.results, data.widened === true);
}

/** Hits for a search the creator sent with Enter or the button. */
export async function forwardGeocode(query: string, bias: GeocodeBias): Promise<GeocodeSearch> {
  const q = query.trim();
  if (!q) return { results: [], widened: false };
  const { data } = await apiClient.get<{ results: GeocodeHit[]; widened?: boolean }>('/api/geocode/search', {
    params: { q, lat: bias.lat, lng: bias.lng, lang: i18n.language },
  });
  const widened = data.widened === true;
  return { results: toResults(data.results, widened), widened };
}

/** Address text for a point, or null when no source knows one. */
export async function reverseGeocode(lat: number, lng: number): Promise<string | null> {
  try {
    const { data } = await apiClient.get<{ result: GeocodeHit | null }>('/api/geocode/reverse', {
      params: { lat, lng, lang: i18n.language },
    });
    return data.result ? shortAddress(data.result) || null : null;
  } catch {
    return null;
  }
}

/** Test hook: ask the installation again. */
export function resetGeocodeConfig(): void {
  configRequest = null;
}
