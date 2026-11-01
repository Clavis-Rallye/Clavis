import type { StationTips } from '../api/types';
import { isRichTextEmpty } from './richtext';

/** What a station's tips still lack before the server takes them. */
export type TipsProblem = 'header' | 'links' | 'label' | 'url' | 'headerTooLong' | 'labelTooLong';

/**
 * The server's bounds (routers/schemas.py). The heading is rich text, so its
 * bound counts the markup too.
 */
const TIPS_HEADER_MAX_LENGTH = 2000;
export const TIPS_LABEL_MAX_LENGTH = 200;

/**
 * The first thing missing in a station's tips, or null when they can be saved.
 *
 * The tips section opens with an empty heading and no link. The server refuses
 * that state, and its answer reaches the user as "Ungültige Eingabe" only. So
 * the form checks first and names the part. Mirrors `StationTips` in the
 * backend's routers/schemas.py.
 */
export function tipsProblem(tips: StationTips | null): TipsProblem | null {
  if (!tips) return null;
  const header = tips.header ?? {};
  const links = tips.links ?? [];
  if (!Object.values(header).some((v) => !isRichTextEmpty(v))) return 'header';
  if (links.length === 0) return 'links';
  if (links.some((link) => !Object.values(link.label ?? {}).some((v) => v.trim()))) return 'label';
  // The server takes http and https only, so "augsburg.de" is not an address yet.
  if (links.some((link) => !/^https?:\/\/\S+$/i.test(link.url.trim()))) return 'url';
  if (Object.values(header).some((v) => v.length > TIPS_HEADER_MAX_LENGTH)) return 'headerTooLong';
  if (links.some((link) => Object.values(link.label ?? {}).some((v) => v.length > TIPS_LABEL_MAX_LENGTH))) {
    return 'labelTooLong';
  }
  return null;
}

/**
 * The tips as the server takes them: a language whose text was typed and
 * deleted again is left out. The server refuses a blank text in any language.
 */
export function cleanTips(tips: StationTips | null): StationTips | null {
  if (!tips) return null;
  const filled = (map: Record<string, string>, isEmpty: (v: string) => boolean) =>
    Object.fromEntries(Object.entries(map ?? {}).filter(([, v]) => !isEmpty(v)));
  return {
    header: filled(tips.header, (v) => isRichTextEmpty(v)),
    links: (tips.links ?? []).map((link) => ({
      label: filled(link.label, (v) => !v.trim()),
      url: link.url.trim(),
    })),
  };
}
