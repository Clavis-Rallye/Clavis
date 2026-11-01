import { describe, it, expect } from 'vitest';
import { MARKER_GAP, MARKER_SIZE, MARKER_TARGET, numberedStationIcon, spreadMarkers, type ScreenPoint } from './stationMarker';

const render = (n: number, allDone: boolean, doneName = 'Station abgeschlossen') => {
  const host = document.createElement('div');
  host.innerHTML = numberedStationIcon(n, allDone, doneName).options.html as string;
  return host;
};

// The map takes the blue and the green of the theme, and a finished station keeps its number.
describe('numberedStationIcon', () => {
  it('draws an open station as its number on the primary blue, without a badge', () => {
    const host = render(2, false);
    const marker = host.firstElementChild as HTMLElement;

    expect(marker.textContent?.trim()).toBe('2');
    expect(marker.style.background).toBe('rgb(29, 71, 164)');
    expect(host.querySelector('[data-testid="station-done-badge"]')).toBeNull();
    expect(host.innerHTML).not.toContain('#1976d2');
  });

  it('draws a finished station as its number on success green, with a named check badge', () => {
    const host = render(1, true);
    const marker = host.firstElementChild as HTMLElement;

    expect(marker.textContent?.trim()).toBe('1');
    expect(marker.style.background).toBe('rgb(46, 125, 50)');
    const badge = host.querySelector('[data-testid="station-done-badge"]') as SVGElement;
    expect(badge).toHaveAttribute('role', 'img');
    expect(badge).toHaveAttribute('aria-label', 'Station abgeschlossen');
    // White ring around the badge, so it stands off the green marker.
    expect(badge.style.border).toBe('2px solid rgb(255, 255, 255)');
  });

  it('escapes the name, because it lands in raw HTML', () => {
    const host = render(1, true, '"><img src=x onerror=alert(1)>');

    expect(host.querySelector('img')).toBeNull();
    expect(host.querySelector('[data-testid="station-done-badge"]')).toHaveAttribute(
      'aria-label',
      '"><img src=x onerror=alert(1)>',
    );
  });
});

describe('numberedStationIcon as a touch target', () => {
  it('puts the 30 px circle in the middle of a 44 px square that a finger can hit', () => {
    const icon = numberedStationIcon(4, false, 'erledigt');
    expect(MARKER_TARGET).toBeGreaterThanOrEqual(44);
    expect(icon.options.iconSize).toEqual([MARKER_TARGET, MARKER_TARGET]);
    expect(icon.options.iconAnchor).toEqual([MARKER_TARGET / 2, MARKER_TARGET / 2]);
    const host = document.createElement('div');
    host.innerHTML = icon.options.html as string;
    const circle = host.firstElementChild as HTMLElement;
    expect(circle.style.width).toBe(`${MARKER_SIZE}px`);
    expect(circle.style.margin).toBe(`${(MARKER_TARGET - MARKER_SIZE) / 2}px`);
  });

  it('keeps the tap squares of two markers apart', () => {
    expect(MARKER_GAP).toBeGreaterThanOrEqual(MARKER_TARGET);
  });
});

describe('numberedStationIcon with a name', () => {
  it('gives a screen reader the station and hides the bare number from it', () => {
    const host = document.createElement('div');
    host.innerHTML = numberedStationIcon(3, false, 'erledigt', 'Station 3: Rathaus <b>').options.html as string;
    const marker = host.firstElementChild as HTMLElement;

    expect(marker.querySelector('[aria-hidden="true"]')?.textContent).toBe('3');
    expect(marker.textContent).toContain('Station 3: Rathaus <b>');
    expect(host.querySelector('b')).toBeNull();
  });

  it('says the state of a finished station after its name, and leaves the badge to the eye', () => {
    const host = document.createElement('div');
    host.innerHTML = numberedStationIcon(2, true, 'Station abgeschlossen', 'Station 2: Perlachturm').options
      .html as string;

    expect(host.textContent).toContain('Station 2: Perlachturm, Station abgeschlossen');
    expect(host.querySelector('[data-testid="station-done-badge"]')).toHaveAttribute('aria-hidden', 'true');
  });
});

const least = (points: Array<{ x: number; y: number }>) => {
  let distance = Infinity;
  points.forEach((a, i) =>
    points.slice(i + 1).forEach((b) => {
      distance = Math.min(distance, Math.hypot(a.x - b.x, a.y - b.y));
    }),
  );
  return distance;
};

const at = (id: string, x: number, y: number): ScreenPoint => ({ id, x, y });

// Markers that sit on top of each other must spread apart, so that every number
// stays readable.
describe('spreadMarkers', () => {
  it('leaves markers that are far enough apart on their stations', () => {
    const points = [at('1', 0, 0), at('2', MARKER_GAP, 0), at('3', 0, 200)];
    expect(spreadMarkers(points)).toEqual(points.map((point) => ({ ...point, moved: false })));
  });

  it('moves two close markers apart, each to its own side, around their middle', () => {
    const [left, right] = spreadMarkers([at('a', 96, 100), at('b', 104, 100)]);
    expect(Math.hypot(left.x - right.x, left.y - right.y)).toBeCloseTo(MARKER_GAP);
    expect((left.x + right.x) / 2).toBeCloseTo(100);
    expect(left.x).toBeLessThan(right.x);
    expect(left.y).toBeCloseTo(100);
    expect(left.moved && right.moved).toBe(true);
  });

  it('separates the four stacked markers of the stand and leaves the others alone', () => {
    const points = [
      at('1', 100, 100),
      at('2', 104, 96),
      at('3', 97, 108),
      at('4', 108, 105),
      at('5', 300, 100),
      at('8', 100, 320),
    ];
    const placed = spreadMarkers(points);
    expect(least(placed)).toBeGreaterThanOrEqual(MARKER_GAP - 0.5);
    expect(placed.filter((point) => point.moved).map((point) => point.id)).toEqual(['1', '2', '3', '4']);
    expect(placed.map((point) => point.id)).toEqual(points.map((point) => point.id));
  });

  it('takes in a neighbour that the ring would reach', () => {
    // The ring of the three markers has a radius of about 23 px, and the fourth sits 50 px away.
    const placed = spreadMarkers([at('a', 100, 100), at('b', 102, 100), at('c', 100, 102), at('d', 150, 101)]);
    expect(least(placed)).toBeGreaterThanOrEqual(MARKER_GAP - 0.5);
  });

  it('keeps twenty stations of one square apart, within three rings', () => {
    const points = Array.from({ length: 20 }, (_, index) => at(String(index + 1), 200 + (index % 5), 200 + (index % 4)));
    const placed = spreadMarkers(points);
    expect(least(placed)).toBeGreaterThanOrEqual(MARKER_GAP - 0.5);
    expect(new Set(placed.map((point) => point.id)).size).toBe(20);
    const reach = Math.max(...placed.map((point) => Math.hypot(point.x - 202, point.y - 201.5)));
    expect(reach).toBeLessThanOrEqual(3 * MARKER_GAP + 1);
  });

  it('keeps every count from 2 to 40 on one point apart, and up to twelve on one ring', () => {
    for (let count = 2; count <= 40; count += 1) {
      const placed = spreadMarkers(Array.from({ length: count }, (_, index) => at(String(index), 300, 300)));
      expect(least(placed)).toBeGreaterThanOrEqual(MARKER_GAP - 0.5);
      if (count <= 12) {
        const radii = placed.map((point) => Math.hypot(point.x - 300, point.y - 300));
        expect(Math.max(...radii) - Math.min(...radii)).toBeLessThan(0.01);
      }
    }
  });

  it('handles stations on the very same point, one station and none', () => {
    const same = spreadMarkers([at('a', 50, 50), at('b', 50, 50), at('c', 50, 50)]);
    expect(least(same)).toBeGreaterThanOrEqual(MARKER_GAP - 0.5);
    expect(spreadMarkers([at('only', 5, 5)])).toEqual([{ id: 'only', x: 5, y: 5, moved: false }]);
    expect(spreadMarkers([])).toEqual([]);
  });

  it('gives the same result for the same input', () => {
    const points = [at('1', 100, 100), at('2', 104, 96), at('3', 97, 108)];
    expect(spreadMarkers(points)).toEqual(spreadMarkers(points));
  });
});
