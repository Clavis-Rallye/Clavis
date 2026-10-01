/**
 * Clickable links in the feedback bar's card. The kit's cards
 * name addresses such as the stand's join page or Mailpit, and the tester
 * should not have to copy them.
 *
 * Only `http` and `https` become links. Text stays text: React elements, never
 * HTML. A same-origin link navigates inside the App, in the same tab. Another
 * host opens in a new tab without an opener.
 */
import type { ReactNode } from 'react';
import { Link as RouterLink } from 'react-router';
import Link from '@mui/material/Link';

const URL_RE = /https?:\/\/[^\s<>"'`]+/g;
// Sentence punctuation after an address belongs to the sentence.
const TRAILING_RE = /[.,;:!?'"»]+$/;
const CLOSERS: Record<string, string> = { ')': '(', ']': '[', '}': '{' };

function count(text: string, ch: string): number {
  return text.split(ch).length - 1;
}

/** Cut sentence punctuation and unbalanced closing brackets off the end of a match. */
export function trimUrl(raw: string): string {
  let url = raw;
  for (;;) {
    const before = url;
    url = url.replace(TRAILING_RE, '');
    const last = url.slice(-1);
    const opener = CLOSERS[last];
    if (opener && count(url, last) > count(url, opener)) url = url.slice(0, -1);
    if (url === before) return url;
  }
}

interface LinkPart {
  kind: 'text' | 'link';
  value: string;
}

/** Split a text into plain parts and http(s) addresses. */
export function splitLinks(text: string): LinkPart[] {
  const parts: LinkPart[] = [];
  let at = 0;
  for (const match of text.matchAll(URL_RE)) {
    const start = match.index ?? 0;
    const url = trimUrl(match[0]);
    if (!/^https?:\/\/[^/?#]+/.test(url)) continue;
    if (start > at) parts.push({ kind: 'text', value: text.slice(at, start) });
    parts.push({ kind: 'link', value: url });
    at = start + url.length;
  }
  if (at < text.length) parts.push({ kind: 'text', value: text.slice(at) });
  return parts;
}

function sameOriginPath(url: string): string | null {
  try {
    const parsed = new URL(url);
    return parsed.origin === window.location.origin ? parsed.pathname + parsed.search + parsed.hash : null;
  } catch {
    return null;
  }
}

/** The text with every http(s) address as a link. */
export function linkify(text: string | null | undefined): ReactNode {
  if (!text) return text ?? null;
  return splitLinks(text).map((part, index) => {
    if (part.kind === 'text') return part.value;
    const path = sameOriginPath(part.value);
    if (path !== null) {
      return (
        <Link key={index} component={RouterLink} to={path} data-testid="qa-bar-link" sx={{ overflowWrap: 'anywhere' }}>
          {part.value}
        </Link>
      );
    }
    return (
      <Link
        key={index}
        href={part.value}
        target="_blank"
        rel="noopener noreferrer"
        data-testid="qa-bar-link"
        sx={{ overflowWrap: 'anywhere' }}
      >
        {part.value}
      </Link>
    );
  });
}
