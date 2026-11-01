import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import L from 'leaflet';
import { CircleMarker, MapContainer, Marker, Polyline, Popup, TileLayer, ZoomControl } from './Leaflet';

const icon = (label: string) => L.divIcon({ className: '', html: `<span>${label}</span>`, iconSize: [44, 44] });

interface SceneProps {
  position?: [number, number];
  onClick?: () => void;
  zoomIn?: string;
  label?: L.DivIcon;
  name?: string;
}

const Scene: React.FC<SceneProps> = ({ position = [48.3688, 10.8986], onClick = () => {}, zoomIn = 'Vergrößern', label, name }) => (
  <MapContainer center={[48.3688, 10.8986]} zoom={15} zoomControl={false} style={{ height: 300, width: 300 }} name={name}>
    <TileLayer url="https://tile.test/{z}/{x}/{y}.png" attribution="&copy; OSM" />
    <ZoomControl zoomInTitle={zoomIn} zoomOutTitle="Verkleinern" />
    <Polyline positions={[[48.36, 10.89], position]} pathOptions={{ color: '#49454F' }} interactive={false} />
    <CircleMarker center={[48.36, 10.89]} radius={3} interactive={false} />
    <Marker position={position} icon={label} eventHandlers={{ click: onClick }}>
      <Popup>
        <p>Rathaus, Rathausplatz 2</p>
      </Popup>
    </Marker>
  </MapContainer>
);

// The thin layer over Leaflet takes the place of react-leaflet (Hippocratic License 2.1).
describe('Leaflet layer', () => {
  it('builds one map in strict mode, with the tiles, the controls, the marker and the paths', () => {
    const { container } = render(
      <React.StrictMode>
        <Scene label={icon('1')} />
      </React.StrictMode>,
    );
    expect(container.querySelectorAll('.leaflet-container')).toHaveLength(1);
    expect(container.querySelectorAll('.leaflet-marker-icon')).toHaveLength(1);
    expect(container.querySelectorAll('.leaflet-overlay-pane path')).toHaveLength(2);
    expect(container.querySelector('.leaflet-control-attribution')?.textContent).toContain('© OSM');
    expect(screen.getByRole('button', { name: 'Vergrößern' })).toBeInTheDocument();
  });

  it('moves the marker, swaps its icon and calls the latest click handler', () => {
    const first = vi.fn();
    const second = vi.fn();
    const { container, rerender } = render(<Scene onClick={first} label={icon('1')} />);
    // Without 3D transforms, as in jsdom, Leaflet places a marker with left and top.
    const place = (el: HTMLElement) => `${el.style.transform}|${el.style.left}|${el.style.top}`;
    const before = place(container.querySelector('.leaflet-marker-icon') as HTMLElement);

    rerender(<Scene onClick={second} position={[48.37, 10.9]} label={icon('2')} />);
    const marker = container.querySelector('.leaflet-marker-icon') as HTMLElement;
    expect(place(marker)).not.toBe(before);
    expect(marker.textContent).toBe('2');

    fireEvent.click(marker);
    expect(second).toHaveBeenCalledTimes(1);
    expect(first).not.toHaveBeenCalled();
  });

  it('shows the React content of the popup when the marker opens it', () => {
    const { container } = render(<Scene label={icon('1')} />);
    fireEvent.click(container.querySelector('.leaflet-marker-icon') as HTMLElement);
    expect(container.querySelector('.leaflet-popup-content')?.textContent).toBe('Rathaus, Rathausplatz 2');
  });

  it('names the zoom buttons anew when the names change', () => {
    const { rerender } = render(<Scene label={icon('1')} />);
    rerender(<Scene label={icon('1')} zoomIn="Zoom in" />);
    expect(screen.getByRole('button', { name: 'Zoom in' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Vergrößern' })).not.toBeInTheDocument();
  });

  it('removes the map when the page leaves', () => {
    const remove = vi.spyOn(L.Map.prototype, 'remove');
    const { unmount } = render(<Scene label={icon('1')} />);
    unmount();
    expect(remove).toHaveBeenCalledTimes(1);
    remove.mockRestore();
  });

  // The container takes the focus (Leaflet gives it
  // tabindex 0) and must be a named region when a name is passed.
  it('gives the container the role region and the passed name', () => {
    render(<Scene label={icon('1')} name="Karte der Stationen" />);
    const region = screen.getByRole('region', { name: 'Karte der Stationen' });
    expect(region).toHaveClass('leaflet-container');
  });
});
