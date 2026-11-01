import React, { useEffect, useMemo, useState } from 'react';
import L from 'leaflet';
import { CircleMarker, Marker, Polyline, Popup } from '../../components/Leaflet';
import { useMap, useMapEvents } from '../../components/leafletHooks';
import { STATION_MARKER_COLOR } from '../../utils/answerState';
import { MARKER_TARGET, numberedStationIcon, spreadMarkers } from './stationMarker';

interface StationMapItem {
  id: string;
  /** The route number on the marker. */
  number: number;
  lat: number;
  lng: number;
  allDone: boolean;
  /** What a screen reader says for the marker: "Station 3: Rathaus". */
  name: string;
  popup: React.ReactNode;
}

interface StationMarkersProps {
  items: StationMapItem[];
  /** The screen reader name of the check badge of a finished station. */
  doneName: string;
  onOpen: (id: string) => void;
}

/** Room between a marker's centre and the edge of the map: half a tap target and a margin. */
const EDGE = MARKER_TARGET / 2 + 8;
/** Closer than this the map shows single houses, and a rallye needs no more. */
const FIT_MAX_ZOOM = 17;
const SINGLE_STATION_ZOOM = 16;

interface Place {
  position: [number, number];
  moved: boolean;
}

/** Where each marker stands at `zoom`: on its station, or moved aside onto a ring. */
const layout = (map: L.Map, items: StationMapItem[], zoom: number): Map<string, Place> => {
  const spread = spreadMarkers(
    items.map((item) => {
      const point = map.project([item.lat, item.lng], zoom);
      return { id: item.id, x: point.x, y: point.y };
    }),
  );
  return new Map(
    spread.map((point) => {
      const latLng = map.unproject([point.x, point.y], zoom);
      return [point.id, { position: [latLng.lat, latLng.lng] as [number, number], moved: point.moved }];
    }),
  );
};

/**
 * Fit the map to the stations. A ring of moved markers can reach past the
 * edge, so the map then fits the markers as they stand, at most twice more.
 */
const fitStations = (map: L.Map, items: StationMapItem[]) => {
  const size = map.getSize();
  // A hidden map has no size, and a fit would compute a zoom from nothing.
  if (items.length === 0 || size.x === 0 || size.y === 0) return;
  if (items.length === 1) {
    map.setView([items[0].lat, items[0].lng], SINGLE_STATION_ZOOM, { animate: false });
    return;
  }
  const options: L.FitBoundsOptions = { padding: [EDGE, EDGE], maxZoom: FIT_MAX_ZOOM, animate: false };
  const stations = items.map((item) => L.latLng(item.lat, item.lng));
  map.fitBounds(L.latLngBounds(stations), options);
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const markers = [...layout(map, items, map.getZoom()).values()].map((place) => L.latLng(place.position));
    const inside = markers.every((marker) => {
      const point = map.latLngToContainerPoint(marker);
      return point.x >= EDGE && point.y >= EDGE && point.x <= size.x - EDGE && point.y <= size.y - EDGE;
    });
    if (inside) return;
    map.fitBounds(L.latLngBounds([...stations, ...markers]), options);
  }
};

const StationMarker: React.FC<{
  item: StationMapItem;
  position: [number, number];
  doneName: string;
  onOpen: (id: string) => void;
}> = ({ item, position, doneName, onOpen }) => {
  // A new icon replaces the marker's element. The 15 s refresh must not do
  // that, or a keyboard user loses the focus on the marker.
  const icon = useMemo(
    () => numberedStationIcon(item.number, item.allDone, doneName, item.name),
    [item.number, item.allDone, doneName, item.name],
  );
  return (
    <Marker
      position={position}
      icon={icon}
      eventHandlers={{
        click: () => onOpen(item.id),
        // Leaflet answers Enter on a focused marker with the popup only, and
        // the popup holds no link. A button opens with Enter and Space.
        keydown: (event) => {
          const key = (event.originalEvent as KeyboardEvent).key;
          if (key !== 'Enter' && key !== ' ') return;
          event.originalEvent.preventDefault();
          onOpen(item.id);
        },
      }}
    >
      <Popup>{item.popup}</Popup>
    </Marker>
  );
};

/**
 * The station markers of the participant's map.
 *
 * The map first fits itself to all stations. Markers that would still overlap
 * at the zoom of the moment move onto a ring, and a thin line joins each to a
 * dot on its station (see `spreadMarkers`). After every zoom the layout runs
 * again, so markers return to their stations as soon as there is room. The
 * map fits itself only when the stations change, never on a refresh, so it
 * does not take the zoom away from the participant.
 *
 * The markers are buttons in route order, each named "Station 3: Rathaus".
 * The list view stays the full twin of the map for a screen reader.
 */
const StationMarkers: React.FC<StationMarkersProps> = ({ items, doneName, onOpen }) => {
  const map = useMap();
  const [zoom, setZoom] = useState(() => map.getZoom());
  useMapEvents({ zoomend: () => setZoom(map.getZoom()) });

  // The key holds every position. `items` is a new array on each refresh.
  const positionsKey = items.map((item) => `${item.id}:${item.lat},${item.lng}`).join('|');
  // A fit that changes the zoom fires `zoomend`, and the handler above takes the new zoom.
  useEffect(() => {
    fitStations(map, items);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [positionsKey, map]);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const placed = useMemo(() => layout(map, items, zoom), [positionsKey, zoom, map]);

  return (
    <>
      {items.map((item) => {
        const place = placed.get(item.id);
        if (!place?.moved) return null;
        return (
          <React.Fragment key={`line-${item.id}`}>
            <Polyline
              positions={[[item.lat, item.lng], place.position]}
              pathOptions={{ color: STATION_MARKER_COLOR.leader, weight: 1.5, opacity: 0.9 }}
              interactive={false}
            />
            <CircleMarker
              center={[item.lat, item.lng]}
              radius={3}
              pathOptions={{ color: '#fff', weight: 1, fillColor: STATION_MARKER_COLOR.leader, fillOpacity: 1 }}
              interactive={false}
            />
          </React.Fragment>
        );
      })}
      {items.map((item) => (
        <StationMarker
          key={item.id}
          item={item}
          position={placed.get(item.id)?.position ?? [item.lat, item.lng]}
          doneName={doneName}
          onOpen={onOpen}
        />
      ))}
    </>
  );
};

export default StationMarkers;
