import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Autocomplete,
  Box,
  CircularProgress,
  IconButton,
  Link,
  InputAdornment,
  ListItemIcon,
  ListItemText,
  ListSubheader,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import { visuallyHidden } from '@mui/utils';
import SearchIcon from '@mui/icons-material/Search';
import MyLocationIcon from '@mui/icons-material/MyLocation';
import PlaceIcon from '@mui/icons-material/Place';
import { useTranslation } from 'react-i18next';
import {
  FALLBACK_CENTRE,
  forwardGeocode,
  getGeocodeConfig,
  offersAddressSearch,
  reverseGeocode,
  suggestPlaces,
  type GeocodeConfig,
  type GeocodeResult,
} from '../utils/geocode';
import { MapContainer, Marker, TileLayer } from './Leaflet';
import { useMap, useMapEvents } from './leafletHooks';
import { tilesFromConfig } from '../utils/mapTiles';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import MapControls from './MapControls';
import markerIconUrl from 'leaflet/dist/images/marker-icon.png';
import markerIcon2xUrl from 'leaflet/dist/images/marker-icon-2x.png';
import markerShadowUrl from 'leaflet/dist/images/marker-shadow.png';

// Fix the default marker icon. Leaflet's CSS references images that won't
// resolve under Vite's module bundler. Import the PNGs from the package, so
// Vite serves them same-origin. That keeps the strict CSP img-src 'self'
// viable and drops the unpkg CDN dependency.
const defaultIcon = L.icon({
  iconUrl: markerIconUrl,
  iconRetinaUrl: markerIcon2xUrl,
  shadowUrl: markerShadowUrl,
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41],
});
L.Marker.prototype.options.icon = defaultIcon;

interface MapPickerProps {
  /** Initial latitude. Null: the map starts at the installation's start point. */
  lat: number | null;
  /** Initial longitude. */
  lng: number | null;
  /** Initial address shown in the search field (e.g. on edit). */
  address?: string;
  onChange: (next: { lat: number; lng: number; address: string }) => void;
  disabled?: boolean;
  /** Search input placeholder. */
  searchLabel?: string;
  /**
   * Reports whether the search field holds text that was not searched yet or
   * whose result was not picked. The form blocks saving while this is true,
   * because an address without a map position would put the station at the
   * wrong house.
   */
  onPendingChange?: (pending: boolean) => void;
}

/** Zoom of the map after a pick or a pin drop. Level 17 shows single buildings. */
const PIN_ZOOM = 17;
/** Shorter texts match too much to help. The server refuses one letter as well. */
const SUGGEST_MIN_LENGTH = 2;
/** A creator who types fluently sends one request per pause, not one per key. */
const SUGGEST_DELAY_MS = 250;

/**
 * Hits from the region first, then the hits from outside it, each group in the
 * server's order. The sort is stable.
 */
function regionFirst(results: GeocodeResult[]): GeocodeResult[] {
  return [...results].sort((a, b) => Number(a.origin === 'outside') - Number(b.origin === 'outside'));
}

/**
 * The address search of a station with its map.
 *
 * The field is a combobox (WAI-ARIA 1.2, list autocomplete). Where the
 * installation allows it, hits appear while the creator types. Enter without a
 * highlighted hit, or the search button, sends the whole text: that search may
 * also ask the outside service. Picking a hit sets the pin and the short
 * address. Hits from outside the region stand last, under their own heading.
 * A screen reader hears the number of hits per group, and a search without
 * hits or with an error, from live regions. An installation without any
 * address search gets no field, only a notice: the pin is set on the map.
 */
const MapPicker: React.FC<MapPickerProps> = ({
  lat,
  lng,
  address,
  onChange,
  disabled = false,
  searchLabel = 'Adresse suchen',
  onPendingChange,
}) => {
  const { t } = useTranslation('content');
  const { t: tCommon } = useTranslation('common');
  const [query, setQuery] = useState(address ?? '');
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<GeocodeResult[]>([]);
  const [open, setOpen] = useState(false);
  // Focus is in the field or on the map: the creator is still at work here.
  const [focused, setFocused] = useState(false);
  const [config, setConfig] = useState<GeocodeConfig | null>(null);
  const [pin, setPin] = useState<[number, number] | null>(
    lat !== null && lng !== null ? [lat, lng] : null,
  );
  const skipExternalSync = useRef(false);
  const suggestTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const suggestRequest = useRef<AbortController | null>(null);

  useEffect(() => {
    let alive = true;
    void getGeocodeConfig().then((next) => {
      if (alive) setConfig(next);
    });
    return () => {
      alive = false;
    };
  }, []);

  const cancelSuggest = useCallback(() => {
    if (suggestTimer.current) clearTimeout(suggestTimer.current);
    suggestTimer.current = null;
    suggestRequest.current?.abort();
    suggestRequest.current = null;
  }, []);
  useEffect(() => cancelSuggest, [cancelSuggest]);

  // Reflect external lat/lng changes (e.g. on form load).
  useEffect(() => {
    if (skipExternalSync.current) {
      skipExternalSync.current = false;
      return;
    }
    if (lat !== null && lng !== null) setPin([lat, lng]);
  }, [lat, lng]);

  useEffect(() => {
    if (address !== undefined) setQuery(address);
  }, [address]);

  // Text in the field that is not the confirmed address is pending.
  const pending = query.trim() !== '' && query.trim() !== (address ?? '').trim();
  useEffect(() => {
    onPendingChange?.(pending);
  }, [pending, onPendingChange]);

  // Before a pin exists, the map and the search start at the installation's
  // start point: its setting, else the centre of its place index.
  const start = useMemo<[number, number]>(
    () => (config?.centre ? [config.centre.lat, config.centre.lng] : FALLBACK_CENTRE),
    [config],
  );
  const tiles = useMemo(() => tilesFromConfig(config), [config]);
  const bias = useMemo(
    () => (pin ? { lat: pin[0], lng: pin[1] } : { lat: start[0], lng: start[1] }),
    [pin, start],
  );

  const pickResult = useCallback(
    (result: GeocodeResult) => {
      cancelSuggest();
      skipExternalSync.current = true;
      setResults([]);
      setOpen(false);
      setError(null);
      setPin([result.lat, result.lng]);
      setQuery(result.displayName);
      onChange({ lat: result.lat, lng: result.lng, address: result.displayName });
    },
    [onChange, cancelSuggest],
  );

  const handleSearch = useCallback(async () => {
    if (disabled || !query.trim()) return;
    cancelSuggest();
    setSearching(true);
    setError(null);
    try {
      const found = regionFirst((await forwardGeocode(query, bias)).results);
      if (found.length === 0) {
        setResults([]);
        setOpen(false);
        setError(t('station.location.noResults'));
        return;
      }
      // The only hit is taken at once, but never one from outside the region:
      // the creator must see that the place is in another town.
      if (found.length === 1 && found[0].origin === 'region') {
        pickResult(found[0]);
        return;
      }
      setResults(found);
      setOpen(true);
    } catch {
      setResults([]);
      setOpen(false);
      setError(t('station.location.failed'));
    } finally {
      setSearching(false);
    }
  }, [query, disabled, bias, pickResult, cancelSuggest, t]);

  const handleInput = useCallback(
    (text: string) => {
      setQuery(text);
      setError(null);
      cancelSuggest();
      if (!config?.typeahead || text.trim().length < SUGGEST_MIN_LENGTH) {
        setResults([]);
        setOpen(false);
        return;
      }
      suggestTimer.current = setTimeout(() => {
        const request = new AbortController();
        suggestRequest.current = request;
        suggestPlaces(text, bias, request.signal)
          .then((found) => {
            if (request.signal.aborted) return;
            setResults(regionFirst(found));
            setOpen(found.length > 0);
          })
          .catch(() => {
            // A failed suggestion is silent: Enter still runs the full search and reports.
          });
      }, SUGGEST_DELAY_MS);
    },
    [config, bias, cancelSuggest],
  );

  const handlePinMove = useCallback(
    async (nextLat: number, nextLng: number) => {
      skipExternalSync.current = true;
      setPin([nextLat, nextLng]);
      setResults([]);
      setError(null);
      // Best-effort reverse geocode; failure leaves the search field as-is.
      try {
        const display = await reverseGeocode(nextLat, nextLng);
        if (display) {
          setQuery(display);
          onChange({ lat: nextLat, lng: nextLng, address: display });
          return;
        }
      } catch {
        /* ignore */
      }
      onChange({ lat: nextLat, lng: nextLng, address: query });
    },
    [onChange, query],
  );

  const initialCentre = useMemo<[number, number]>(() => pin ?? start, [pin, start]);

  const secondLine = (result: GeocodeResult) => {
    const kind = result.category ? t(`station.location.category.${result.category}`, { defaultValue: '' }) : '';
    return [kind, result.district].filter(Boolean).join(' · ');
  };
  const services = (config?.sources ?? [])
    .filter((source) => source !== 'local')
    .map((source) => t(`station.location.service.${source}`));
  const searchable = offersAddressSearch(config);

  // The count follows the groups of the list: region first, then other places.
  const outsideCount = results.filter((result) => result.origin === 'outside').length;
  const regionCount = results.length - outsideCount;
  const countText =
    outsideCount === 0
      ? t('station.location.resultCount', { count: results.length })
      : `${[
          regionCount > 0 ? t('station.location.resultCountRegion', { count: regionCount }) : null,
          t('station.location.resultCountOutside', { count: outsideCount }),
        ]
          .filter(Boolean)
          .join(', ')}. ${t('station.location.resultCountPick')}`;

  return (
    <Stack
      spacing={1}
      onFocus={() => setFocused(true)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false);
      }}
    >
      {!searchable && <Alert severity="info">{t('station.location.noSearch')}</Alert>}

      {searchable && (
        <Autocomplete<GeocodeResult, false, false, true>
          freeSolo
          fullWidth
          size="small"
          disabled={disabled}
          options={results}
          // The server ranks and filters. The list shows its answer as it is.
          filterOptions={(options) => options}
          // Only hits from outside the region get a heading. The region's hits stand first, without one.
          groupBy={
            outsideCount > 0
              ? (option) => (option.origin === 'outside' ? t('station.location.widened') : '')
              : undefined
          }
          renderGroup={(params) =>
            params.group ? (
              <li key={params.key}>
                <ListSubheader component="div" sx={{ top: -8 }}>
                  {params.group}
                </ListSubheader>
                <Box component="ul" sx={{ p: 0 }}>
                  {params.children}
                </Box>
              </li>
            ) : (
              <React.Fragment key={params.key}>{params.children}</React.Fragment>
            )
          }
          getOptionLabel={(option) => (typeof option === 'string' ? option : option.displayName)}
          getOptionKey={(option) =>
            typeof option === 'string' ? option : `${option.lat},${option.lng},${option.displayName}`
          }
          value={null}
          inputValue={query}
          open={open && results.length > 0}
          onOpen={() => setOpen(results.length > 0)}
          onClose={() => setOpen(false)}
          onInputChange={(_, text, reason) => {
            if (reason === 'input' || reason === 'clear') handleInput(text);
          }}
          onChange={(_, picked) => {
            // A string is Enter without a highlighted hit: send the whole text.
            if (typeof picked === 'string') void handleSearch();
            else if (picked) pickResult(picked);
          }}
          clearOnBlur={false}
          selectOnFocus={false}
          handleHomeEndKeys
          forcePopupIcon={false}
          slotProps={{ listbox: { 'aria-label': t('station.location.results') } }}
          renderOption={({ key, ...optionProps }, result) => (
            <Box component="li" key={key} {...optionProps}>
              <ListItemIcon sx={{ minWidth: 32 }}>
                <PlaceIcon fontSize="small" color="action" />
              </ListItemIcon>
              <ListItemText primary={result.displayName} secondary={secondLine(result) || undefined} />
            </Box>
          )}
          renderInput={(params) => (
            <TextField
              {...params}
              placeholder={searchLabel}
              helperText={t(config?.typeahead ? 'station.location.hintTypeahead' : 'station.location.hintSearch')}
              slotProps={{
                inputLabel: params.slotProps.inputLabel,
                htmlInput: { ...params.slotProps.htmlInput, 'aria-label': searchLabel, enterKeyHint: 'search' },
                input: {
                  ...params.slotProps.input,
                  endAdornment: (
                    <InputAdornment position="end">
                      <IconButton
                        size="small"
                        aria-label={t('station.location.searchButton')}
                        onClick={() => void handleSearch()}
                        disabled={disabled || searching || !query.trim()}
                        edge="end"
                      >
                        {searching ? <CircularProgress size={16} /> : <SearchIcon fontSize="small" />}
                      </IconButton>
                    </InputAdornment>
                  ),
                },
              }}
            />
          )}
        />
      )}

      {/* A screen reader hears how many hits the list holds. The list itself is silent until entered. */}
      <Box role="status" aria-live="polite" sx={visuallyHidden}>
        {searchable && open && results.length > 0 ? countText : ''}
      </Box>

      {error && <Alert severity="warning">{error}</Alert>}

      {/* Red only after the creator left the field with a text that is no picked address.
          While they type, nothing is wrong yet, and the helper text says what to do. */}
      {searchable && pending && !focused && !error && (
        <Typography variant="caption" color="error" role="alert">
          {t('station.location.searchPending')}
        </Typography>
      )}

      <Box
        sx={{
          height: 280,
          borderRadius: 1.5,
          overflow: 'hidden',
          border: 1,
          borderColor: 'divider',
        }}
      >
        <MapContainer
          center={initialCentre}
          zoom={pin ? PIN_ZOOM : 13}
          zoomControl={false}
          style={{ height: '100%', width: '100%' }}
          scrollWheelZoom={!disabled}
          name={tCommon('aria.locationMap')}
        >
          {/* The tiles follow the config of the installation; until it has
              answered, no tile is requested, and when it failed none is:
              no map then shows tiles of a server nobody configured. */}
          {tiles && (
            <TileLayer url={tiles.url} attribution={tiles.attribution} />
          )}
          <MapControls />
          <StartAtCentre centre={pin ? null : start} />
          <RecentreOnPin pin={pin} />
          <ClickHandler onPick={(p) => void handlePinMove(p.lat, p.lng)} disabled={disabled} />
          {pin && (
            <Marker
              position={pin}
              draggable={!disabled}
              eventHandlers={{
                dragend: (e) => {
                  const m = e.target as L.Marker;
                  const ll = m.getLatLng();
                  void handlePinMove(ll.lat, ll.lng);
                },
              }}
            />
          )}
        </MapContainer>
      </Box>

      {/* The map's tiles ask for an operator contact within one click (FOSSGIS
          terms). The imprint link stands directly under the
          map. A plain anchor, because the picker also renders outside a
          router, in tests and standalone forms; in a new tab, so the click
          does not throw away the station form the creator is filling. */}
      <Link
        href="/legal/impressum"
        target="_blank"
        rel="noopener"
        variant="caption"
        color="text.secondary"
        underline="always"
        sx={{ alignSelf: 'flex-start' }}
      >
        {tCommon('legalLinks.impressum')}
      </Link>

      {config?.unknown && (
        <Typography variant="caption" color="text.secondary">
          {t('station.location.tilesUnavailable', {
            defaultValue: 'Die Karte ist gerade nicht verfügbar.',
          })}
        </Typography>
      )}

      <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
        {pin && (
          <>
            <MyLocationIcon fontSize="small" color="action" />
            <Typography variant="caption" color="text.secondary">
              {pin[0].toFixed(5)}, {pin[1].toFixed(5)}
            </Typography>
          </>
        )}
        {/* Without an address search there is nothing to attribute. */}
        {searchable && (
          <Typography variant="caption" color="text.disabled" sx={{ ml: 'auto' }}>
            {t('station.location.attribution', { services: services.length ? `, ${services.join(', ')}` : '' })}
          </Typography>
        )}
      </Stack>
    </Stack>
  );
};

/**
 * The map moves to the start point when it changes: the answer of the config
 * arrives after the map is drawn at the fallback. Never once a pin exists.
 */
const StartAtCentre: React.FC<{ centre: [number, number] | null }> = ({ centre }) => {
  const map = useMap();
  const lat = centre?.[0];
  const lng = centre?.[1];
  useEffect(() => {
    if (lat !== undefined && lng !== undefined) map.setView([lat, lng], map.getZoom(), { animate: false });
  }, [lat, lng, map]);
  return null;
};

const RecentreOnPin: React.FC<{ pin: [number, number] | null }> = ({ pin }) => {
  const map = useMap();
  useEffect(() => {
    // Close enough to tell one house from the next: the creator checks the pin against the building.
    if (pin) map.flyTo(pin, Math.max(map.getZoom(), PIN_ZOOM), { duration: 0.5 });
  }, [pin, map]);
  return null;
};

const ClickHandler: React.FC<{
  onPick: (p: { lat: number; lng: number }) => void;
  disabled: boolean;
}> = ({ onPick, disabled }) => {
  useMapEvents({
    click(e) {
      if (disabled) return;
      onPick({ lat: e.latlng.lat, lng: e.latlng.lng });
    },
  });
  return null;
};

export default MapPicker;
