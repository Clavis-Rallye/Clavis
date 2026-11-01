/**
 * A thin React layer over Leaflet, for every map of the app.
 *
 * It replaces react-leaflet. That package ships under the Hippocratic
 * License 2.1, which conflicts with the licence requirement of the project:
 * free reuse, and no dependency that blocks redistribution. Leaflet itself is
 * BSD-2-Clause.
 *
 * The layer holds only what the maps use, with the names and props of
 * react-leaflet, so a page reads the same. The hooks `useMap` and
 * `useMapEvents` are in leafletHooks.ts. Options of `MapContainer` count at
 * creation only, as in react-leaflet. Every other prop follows its changes.
 */
import React, { useCallback, useContext, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import L from 'leaflet';
import { LayerContext, MapContext, useEvents, useLayer, useMap, type Handlers } from './leafletHooks';

interface MapContainerProps extends L.MapOptions {
  center: L.LatLngExpression;
  zoom: number;
  style?: React.CSSProperties;
  className?: string;
  /** The accessible name. Leaflet makes the container focusable, so it becomes a named region. */
  name?: string;
  children?: React.ReactNode;
}

/** The map. Its options count at creation, and `children` see it through `useMap`. */
export const MapContainer: React.FC<MapContainerProps> = ({ center, zoom, style, className, name, children, ...options }) => {
  const [map, setMap] = useState<L.Map | null>(null);
  const initial = useRef({ center, zoom, options });
  const created = useRef<L.Map | null>(null);
  // A callback ref creates the map as soon as its element exists. React may
  // call it twice in strict mode, and Leaflet refuses a second map on one element.
  const attach = useCallback((node: HTMLDivElement | null) => {
    if (!node || created.current) return;
    const { center: start, zoom: startZoom, options: startOptions } = initial.current;
    created.current = L.map(node, { ...startOptions, center: start, zoom: startZoom });
    setMap(created.current);
  }, []);
  useEffect(
    () => () => {
      map?.remove();
    },
    [map],
  );
  return (
    <div ref={attach} style={style} className={className} role={name ? 'region' : undefined} aria-label={name}>
      {map && <MapContext.Provider value={map}>{children}</MapContext.Provider>}
    </div>
  );
};

/** A tile layer, for example the OpenStreetMap tiles with their attribution. */
export const TileLayer: React.FC<{ url: string; attribution?: string }> = ({ url, attribution }) => {
  const map = useMap();
  useEffect(() => {
    const layer = L.tileLayer(url, { attribution }).addTo(map);
    return () => {
      layer.remove();
    };
  }, [map, url, attribution]);
  return null;
};

/** The zoom buttons. New names build new buttons, because Leaflet reads them once. */
export const ZoomControl: React.FC<L.Control.ZoomOptions> = ({ position, zoomInTitle, zoomOutTitle, zoomInText, zoomOutText }) => {
  const map = useMap();
  useEffect(() => {
    // An option given as undefined would hide Leaflet's default, so only given ones pass.
    const given = Object.entries({ position, zoomInTitle, zoomOutTitle, zoomInText, zoomOutText }).filter(
      ([, value]) => value !== undefined,
    );
    const control = L.control.zoom(Object.fromEntries(given) as L.Control.ZoomOptions).addTo(map);
    return () => {
      control.remove();
    };
  }, [map, position, zoomInTitle, zoomOutTitle, zoomInText, zoomOutText]);
  return null;
};

interface MarkerProps {
  position: L.LatLngExpression;
  /** Without an icon the marker takes Leaflet's default icon. */
  icon?: L.Icon | L.DivIcon;
  draggable?: boolean;
  eventHandlers?: Handlers;
  children?: React.ReactNode;
}

/**
 * A marker. A new `icon` replaces the marker's element, as in Leaflet, so a
 * caller that keeps the keyboard focus on a marker keeps its icon stable.
 */
export const Marker: React.FC<MarkerProps> = ({ position, icon, draggable = false, eventHandlers, children }) => {
  const map = useMap();
  const marker = useLayer(() => L.marker(position, { ...(icon ? { icon } : {}), draggable }), map);
  const [lat, lng] = toPair(position);
  useEffect(() => {
    marker.setLatLng([lat, lng]);
  }, [marker, lat, lng]);
  useEffect(() => {
    if (icon && marker.options.icon !== icon) marker.setIcon(icon);
  }, [marker, icon]);
  useEffect(() => {
    if (!marker.dragging) return;
    if (draggable) marker.dragging.enable();
    else marker.dragging.disable();
  }, [marker, draggable]);
  useEvents(marker, eventHandlers);
  return children ? <LayerContext.Provider value={marker}>{children}</LayerContext.Provider> : null;
};

/**
 * The popup of the layer around it. Its React content renders into an element
 * of its own, and Leaflet shows that element when the popup opens.
 */
export const Popup: React.FC<{ children?: React.ReactNode }> = ({ children }) => {
  const layer = useContext(LayerContext);
  const [content] = useState(() => document.createElement('div'));
  useEffect(() => {
    if (!layer) return undefined;
    layer.bindPopup(content);
    return () => {
      layer.unbindPopup();
    };
  }, [layer, content]);
  return createPortal(children, content);
};

interface PathProps {
  pathOptions?: L.PathOptions;
  interactive?: boolean;
}

/** A line through `positions`. */
export const Polyline: React.FC<PathProps & { positions: L.LatLngExpression[] }> = ({
  positions,
  pathOptions,
  interactive = true,
}) => {
  const map = useMap();
  const line = useLayer(() => L.polyline(positions, { ...pathOptions, interactive }), map);
  const key = JSON.stringify(positions.map(toPair));
  useEffect(() => {
    line.setLatLngs(JSON.parse(key) as Array<[number, number]>);
  }, [line, key]);
  const style = JSON.stringify(pathOptions ?? {});
  useEffect(() => {
    line.setStyle(JSON.parse(style) as L.PathOptions);
  }, [line, style]);
  return null;
};

/** A circle of a fixed size in pixels, at `center`. */
export const CircleMarker: React.FC<PathProps & { center: L.LatLngExpression; radius?: number }> = ({
  center,
  radius = 10,
  pathOptions,
  interactive = true,
}) => {
  const map = useMap();
  const circle = useLayer(() => L.circleMarker(center, { ...pathOptions, radius, interactive }), map);
  const [lat, lng] = toPair(center);
  useEffect(() => {
    circle.setLatLng([lat, lng]);
  }, [circle, lat, lng]);
  useEffect(() => {
    circle.setRadius(radius);
  }, [circle, radius]);
  const style = JSON.stringify(pathOptions ?? {});
  useEffect(() => {
    circle.setStyle(JSON.parse(style) as L.PathOptions);
  }, [circle, style]);
  return null;
};

/** Latitude and longitude of any form Leaflet accepts, as two numbers for effect dependencies. */
function toPair(position: L.LatLngExpression): [number, number] {
  const point = L.latLng(position);
  return [point.lat, point.lng];
}
