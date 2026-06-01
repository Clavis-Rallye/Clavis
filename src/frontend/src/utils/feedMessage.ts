// The readable message of a feed event's `content` payload, which comes in
// two shapes:
//  - composer events: { text } (announcement, countdown, reminder, alert)
//  - system events: { de, en, ... } (such as coordinator help requests), and
//    sometimes { message }, { body } or { title }.
// Without the localized shape, a system event would render with an empty body.

interface FeedContentShape {
  message?: string;
  text?: string;
  body?: string;
  title?: string;
  de?: string;
  en?: string;
  [key: string]: unknown;
}

export function feedMessage(content: unknown, lang?: string): string {
  const c = (content ?? {}) as FeedContentShape;
  if (typeof c.message === 'string') return c.message;
  if (typeof c.text === 'string') return c.text;
  if (typeof c.body === 'string') return c.body;
  if (typeof c.title === 'string') return c.title;
  // Localized shape (system events, multilingual content).
  const primary = lang?.startsWith('de') ? 'de' : 'en';
  const fallback = primary === 'de' ? 'en' : 'de';
  if (typeof c[primary] === 'string') return c[primary] as string;
  if (typeof c[fallback] === 'string') return c[fallback] as string;
  return '';
}
