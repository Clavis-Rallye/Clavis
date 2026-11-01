/**
 * The hooks and contexts of the thin React layer over Leaflet (see Leaflet.tsx).
 * They live apart from the components, so that fast refresh keeps working.
 */
import { createContext, useContext, useEffect, useRef, useState } from 'react';
import type L from 'leaflet';

export const MapContext = createContext<L.Map | null>(null);
/** The layer that a `Popup` inside it binds to. */
export const LayerContext = createContext<L.Layer | null>(null);

/** The map of the `MapContainer` around the caller. */
export function useMap(): L.Map {
  const map = useContext(MapContext);
  if (!map) throw new Error('useMap needs a MapContainer around it');
  return map;
}

export type Handlers = L.LeafletEventHandlerFnMap;

/**
 * Listen to events of a map or a layer. The handlers may be new on every
 * render: the listeners stay, and they call the latest handlers.
 */
export function useEvents(target: L.Evented | null, handlers: Handlers | undefined): void {
  const latest = useRef(handlers);
  useEffect(() => {
    latest.current = handlers;
  });
  const names = Object.keys(handlers ?? {}).sort().join(' ');
  useEffect(() => {
    if (!target || !names) return undefined;
    const listeners = Object.fromEntries(
      names.split(' ').map((name) => [
        name,
        (event: L.LeafletEvent) => (latest.current?.[name as keyof Handlers] as L.LeafletEventHandlerFn | undefined)?.(event),
      ]),
    );
    target.on(listeners);
    return () => {
      target.off(listeners);
    };
  }, [target, names]);
}

/** Listen to events of the map, for example `click` or `zoomend`. Returns the map. */
export function useMapEvents(handlers: Handlers): L.Map {
  const map = useMap();
  useEvents(map, handlers);
  return map;
}

/** Add a layer to the map while the component is mounted. Props change it through setters. */
export function useLayer<T extends L.Layer>(create: () => T, map: L.Map): T {
  const [layer] = useState(create);
  useEffect(() => {
    layer.addTo(map);
    return () => {
      layer.remove();
    };
  }, [layer, map]);
  return layer;
}
