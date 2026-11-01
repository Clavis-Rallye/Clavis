import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { ZoomControl } from './Leaflet';
import { useMap } from './leafletHooks';

/**
 * The controls of every map in the app. It goes inside a `MapContainer` with
 * `zoomControl={false}`, because it brings its own zoom buttons.
 *
 * - The zoom buttons carry names in the interface language. Leaflet names
 *   them "Zoom in" and "Zoom out" in English on every page.
 * - The attribution loses Leaflet's prefix, a link to leafletjs.com with a
 *   flag. Leaflet's BSD-2-Clause licence asks for its copyright notice in the
 *   source and in the materials of a distribution, never for a credit on the
 *   map. The OpenStreetMap attribution of the tile layer stays: the ODbL and
 *   the tile usage policy require it.
 */
const MapControls = () => {
  const map = useMap();
  const { t, i18n } = useTranslation('common');
  useEffect(() => {
    map.attributionControl?.setPrefix(false);
  }, [map]);
  // Leaflet reads the names once, so a new language builds new buttons.
  return <ZoomControl key={i18n.language} zoomInTitle={t('map.zoomIn')} zoomOutTitle={t('map.zoomOut')} />;
};

export default MapControls;
