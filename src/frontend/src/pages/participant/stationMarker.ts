import L from 'leaflet';
import { STATION_MARKER_COLOR } from '../../utils/answerState';

// Numbered circle markers. The number is the position of the station in the
// fixed route order, so the map matches the numbered list. A `divIcon` replaces
// the stock Leaflet pin. A finished station keeps its number, turns green and
// gets a white-ringed check badge at the top right, so the colour does not
// carry the state alone. `doneName` is the screen reader name of the badge. It
// passes through `escapeHtml`, because it lands in raw HTML.
const escapeHtml = (text: string) =>
  text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

const HIDDEN = 'position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;';

/** The diameter of the visible circle in pixels. */
export const MARKER_SIZE = 30;
/**
 * The side of the square a finger can hit, in pixels. Every touch target of
 * the app is at least 44 x 44 px, so that a finger hits it reliably on a
 * phone. The circle stays 30 px and sits in the middle of a clear 44 px square.
 */
export const MARKER_TARGET = 44;
/**
 * The least distance between the centres of two markers, in pixels. At 44 the
 * tap squares do not overlap, and the circles keep 14 px between them, so
 * each number reads and each marker is its own target.
 */
export const MARKER_GAP = MARKER_TARGET;

const INSET = (MARKER_TARGET - MARKER_SIZE) / 2;

/**
 * `name` is what a screen reader says for the marker, for example
 * "Station 3: Rathaus". With it the bare number is hidden from the screen
 * reader, because "3" alone names nothing. A finished station then says
 * "Station 3: Rathaus, Station abgeschlossen" in one text, and the badge is
 * only a picture. Two names side by side would run together without a space.
 */
export const numberedStationIcon = (n: number, allDone: boolean, doneName: string, name?: string) =>
  L.divIcon({
    className: '', // suppress the default divIcon white box
    html: `<div style="
      position: relative; box-sizing: border-box; margin: ${INSET}px;
      width: ${MARKER_SIZE}px; height: ${MARKER_SIZE}px; border-radius: 50%;
      background: ${allDone ? STATION_MARKER_COLOR.done : STATION_MARKER_COLOR.open};
      color: #fff; font: 700 14px/26px Roboto, sans-serif; text-align: center;
      border: 2px solid #fff; box-shadow: 0 1px 4px rgba(0,0,0,0.4);
    ">${
      name
        ? `<span aria-hidden="true">${n}</span><span style="${HIDDEN}">${escapeHtml(
            allDone ? `${name}, ${doneName}` : name,
          )}</span>`
        : n
    }${
      allDone
        ? `<svg data-testid="station-done-badge" ${
            name ? 'aria-hidden="true"' : `role="img" aria-label="${escapeHtml(doneName)}"`
          }
            viewBox="0 0 24 24" width="12" height="12" style="
              position: absolute; top: -8px; right: -8px; box-sizing: content-box;
              padding: 1px; border-radius: 50%; border: 2px solid #fff;
              background: ${STATION_MARKER_COLOR.done}; fill: #fff;
            "><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/></svg>`
        : ''
    }</div>`,
    iconSize: [MARKER_TARGET, MARKER_TARGET],
    iconAnchor: [MARKER_TARGET / 2, MARKER_TARGET / 2],
    popupAnchor: [0, -MARKER_SIZE / 2 - 1],
  });

export interface ScreenPoint {
  id: string;
  x: number;
  y: number;
}

interface SpreadPoint extends ScreenPoint {
  /** The marker does not sit on its station. A line joins the two. */
  moved: boolean;
}

/**
 * Places for `count` markers around a centre, each `gap` away from the next.
 * The first place lies in the direction `start`, in radians.
 */
function ringOffsets(count: number, gap: number, start: number): Array<{ x: number; y: number }> {
  const at = (radius: number, slots: number, slot: number) => {
    const angle = start + (2 * Math.PI * slot) / slots;
    return { x: radius * Math.cos(angle), y: radius * Math.sin(angle) };
  };
  // Up to twelve go onto one ring that is as small as the gap allows. Its
  // radius stays below that of two rings.
  if (count <= 12) {
    const radius = gap / (2 * Math.sin(Math.PI / count));
    return Array.from({ length: count }, (_, slot) => at(radius, count, slot));
  }
  // More go onto rings one gap apart. Ring k holds up to 6k markers, and each
  // ring takes its share of the markers, so the outer ring is not half empty.
  let rings = 1;
  while (3 * rings * (rings + 1) < count) rings += 1;
  const capacity = 3 * rings * (rings + 1);
  const offsets: Array<{ x: number; y: number }> = [];
  for (let ring = 1; ring <= rings; ring += 1) {
    const share = Math.min(6 * ring, Math.ceil((count * 6 * ring) / capacity));
    const slots = ring === rings ? count - offsets.length : share;
    for (let slot = 0; slot < slots; slot += 1) offsets.push(at(ring * gap, slots, slot));
  }
  return offsets;
}

/**
 * Screen positions for markers so that no two are closer than `gap`.
 *
 * Markers that are closer form a group. The group goes onto a ring around its
 * middle. The ring turns towards the marker farthest from the middle, and each
 * place takes the nearest free marker, so every marker moves out on the side
 * it came from and the lines stay short. A lone marker stays on its
 * station. When a ring reaches another marker, the two groups become one and
 * the layout runs again, so the result has no overlap left.
 */
export function spreadMarkers(points: ScreenPoint[], gap: number = MARKER_GAP): SpreadPoint[] {
  const group = points.map((_, index) => index);
  const find = (index: number): number => (group[index] === index ? index : (group[index] = find(group[index])));
  const near = (a: { x: number; y: number }, b: { x: number; y: number }) =>
    Math.hypot(a.x - b.x, a.y - b.y) < gap - 0.5;

  let placed: SpreadPoint[] = points.map((point) => ({ ...point, moved: false }));
  // Each round joins at least two groups, so it ends after at most one round per point.
  for (let round = 0; round <= points.length; round += 1) {
    let joined = false;
    for (let a = 0; a < placed.length; a += 1) {
      for (let b = a + 1; b < placed.length; b += 1) {
        if (find(a) !== find(b) && near(placed[a], placed[b])) {
          group[find(b)] = find(a);
          joined = true;
        }
      }
    }
    if (!joined && round > 0) break;

    const members = new Map<number, number[]>();
    points.forEach((_, index) => members.set(find(index), [...(members.get(find(index)) ?? []), index]));
    placed = points.map((point) => ({ ...point, moved: false }));
    for (const indexes of members.values()) {
      if (indexes.length === 1) continue;
      const middle = {
        x: indexes.reduce((sum, index) => sum + points[index].x, 0) / indexes.length,
        y: indexes.reduce((sum, index) => sum + points[index].y, 0) / indexes.length,
      };
      let start = -Math.PI / 2;
      let farthest = 0;
      for (const index of indexes) {
        const distance = Math.hypot(points[index].x - middle.x, points[index].y - middle.y);
        if (distance > farthest + 1e-9) {
          farthest = distance;
          start = Math.atan2(points[index].y - middle.y, points[index].x - middle.x);
        }
      }
      const offsets = ringOffsets(indexes.length, gap, start);
      const free = new Set(indexes);
      for (const offset of offsets) {
        const target = { x: middle.x + offset.x, y: middle.y + offset.y };
        let best = -1;
        for (const index of free) {
          if (
            best === -1 ||
            Math.hypot(points[index].x - target.x, points[index].y - target.y) <
              Math.hypot(points[best].x - target.x, points[best].y - target.y)
          ) {
            best = index;
          }
        }
        free.delete(best);
        placed[best] = { ...points[best], ...target, moved: true };
      }
    }
    if (!joined) break;
  }
  return placed;
}
