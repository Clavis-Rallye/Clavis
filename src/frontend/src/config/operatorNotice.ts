import { useSyncExternalStore } from 'react';

/**
 * The operator notice: optional logos, a short text and links that an
 * operator shows on the pages outside a running rally, for example the note
 * of a funding programme. The web container serves an operator-mounted folder
 * at `/operator/`, and the file `/operator/notice.json` describes the notice
 * (contract version 1, spec 095).
 *
 * The file is optional. A missing file, a failed request and an invalid file
 * all mean no notice, never an error. One broken rule anywhere makes the whole
 * file invalid, so a half-valid notice never shows. The operator answers for
 * the content. The app only checks its shape: plain text, logos from
 * `/operator/` only, links with `https` or `http`.
 *
 * `main.tsx` starts the load and does not wait for it. A notice that arrives
 * after the first render reaches the page through `useOperatorNotice()`.
 */

export interface OperatorNoticeLogo {
  /** A file name inside `/operator/`. */
  src: string;
  /** A file name inside `/operator/` for the dark theme. */
  src_dark?: string;
  /** Language code to alt text. */
  alt: Record<string, string>;
  /** CSS pixels, 16 to 200. */
  height: number;
}

export interface OperatorNoticeLink {
  label: string;
  /** An absolute `https` or `http` URL. */
  url: string;
}

export interface OperatorNotice {
  version: 1;
  logos: OperatorNoticeLogo[];
  /** Language code to plain text. */
  text: Record<string, string>;
  /** When set, this text shows for every interface language. */
  text_lang?: string;
  /** Always a list here, empty when the file has none. */
  links: OperatorNoticeLink[];
}

// A relative path keeps the request on the app's own origin (C13).
const NOTICE_URL = '/operator/notice.json';
const LOAD_TIMEOUT_MS = 5000;
// The contract holds at most a few kilobytes. A larger body is no notice.
const MAX_BODY_BYTES = 65536;
const MAX_LOGOS = 8;
const MAX_LINKS = 8;
const MAX_TEXT_CHARS = 2000;
const MAX_LABEL_CHARS = 200;
const MIN_HEIGHT = 16;
const MAX_HEIGHT = 200;

// The character classes leave no room for a slash, a colon or a leading dot,
// so a file name cannot leave `/operator/` or carry a scheme.
const FILE_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*\.(?:svg|png|jpg|jpeg|webp)$/i;
const LANGUAGE_CODE = /^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$/;

type Json = Record<string, unknown>;

function isObject(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Counts code points, so an emoji or an accent counts as one character. */
function charCount(value: string): number {
  return Array.from(value).length;
}

function hasText(value: string | undefined): value is string {
  return typeof value === 'string' && value.trim() !== '';
}

function isFileName(value: unknown): value is string {
  // The pattern alone allows `a..svg`. The contract forbids `..` anywhere.
  return typeof value === 'string' && FILE_NAME.test(value) && !value.includes('..');
}

function isHttpUrl(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  try {
    // No base: a relative URL throws and is refused.
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:';
  } catch {
    return false;
  }
}

/**
 * A map from language code to text. Every key is a language code, every
 * value a string of at most `maxChars`, and at least one text is not empty.
 */
function parseLocalizedMap(value: unknown, maxChars?: number): Record<string, string> | null {
  if (!isObject(value)) return null;
  const out: Record<string, string> = {};
  let anyText = false;
  for (const [lang, text] of Object.entries(value)) {
    if (!LANGUAGE_CODE.test(lang) || typeof text !== 'string') return null;
    if (maxChars !== undefined && charCount(text) > maxChars) return null;
    if (hasText(text)) anyText = true;
    out[lang] = text;
  }
  return anyText ? out : null;
}

function parseLogo(value: unknown): OperatorNoticeLogo | null {
  if (!isObject(value)) return null;
  const { src, src_dark, alt, height } = value;
  if (!isFileName(src)) return null;
  // Only an absent `src_dark` is optional. `null` or any other value breaks
  // the rule, like every other wrong type in the file.
  if (src_dark !== undefined && !isFileName(src_dark)) return null;
  if (typeof height !== 'number' || !Number.isInteger(height)) return null;
  if (height < MIN_HEIGHT || height > MAX_HEIGHT) return null;
  const altMap = parseLocalizedMap(alt);
  if (!altMap) return null;
  const logo: OperatorNoticeLogo = { src, alt: altMap, height };
  if (src_dark !== undefined) logo.src_dark = src_dark;
  return logo;
}

function parseLink(value: unknown): OperatorNoticeLink | null {
  if (!isObject(value)) return null;
  const { label, url } = value;
  if (typeof label !== 'string' || !hasText(label) || charCount(label) > MAX_LABEL_CHARS) {
    return null;
  }
  if (!isHttpUrl(url)) return null;
  return { label, url };
}

/** Parses each entry of a list of at most `max`. One bad entry fails the list. */
function parseList<T>(value: unknown, max: number, parseItem: (item: unknown) => T | null): T[] | null {
  if (!Array.isArray(value) || value.length > max) return null;
  const out: T[] = [];
  for (const item of value) {
    const parsed = parseItem(item);
    if (parsed === null) return null;
    out.push(parsed);
  }
  return out;
}

/**
 * Checks a parsed `notice.json` against contract version 1. Gives a new
 * object with only the known fields, or `null` when any rule breaks. Never
 * throws.
 */
export function parseOperatorNotice(value: unknown): OperatorNotice | null {
  try {
    if (!isObject(value) || value.version !== 1) return null;

    const logos = parseList(value.logos, MAX_LOGOS, parseLogo);
    if (!logos) return null;

    const text = parseLocalizedMap(value.text, MAX_TEXT_CHARS);
    if (!text) return null;

    const { text_lang } = value;
    if (text_lang !== undefined) {
      if (typeof text_lang !== 'string' || !LANGUAGE_CODE.test(text_lang)) return null;
      // A forced language must hold a text, or the notice would show nothing.
      if (!hasText(text[text_lang])) return null;
    }

    const links = value.links === undefined ? [] : parseList(value.links, MAX_LINKS, parseLink);
    if (!links) return null;

    const notice: OperatorNotice = { version: 1, logos, text, links };
    if (text_lang !== undefined) notice.text_lang = text_lang;
    return notice;
  } catch {
    // A getter or proxy in a hand-built value. JSON input never gets here.
    return null;
  }
}

/** Finds a key without regard to case, as language tags are case-insensitive. */
function findEntry(map: Record<string, string>, lang: string): { lang: string; value: string } | null {
  const wanted = lang.toLowerCase();
  for (const [key, value] of Object.entries(map)) {
    if (key.toLowerCase() === wanted && hasText(value)) return { lang: key, value };
  }
  return null;
}

/**
 * Picks the text to show from a map of language code to text, by the
 * language rules of the contract: `forced` when given, else the interface
 * language, else its base language (`de` for `de-AT`), else `en`, else `de`,
 * else the first entry with a text. An empty text never wins. `lang` is the
 * key of the entry that shows, for the `lang` attribute of its element.
 */
export function pickLocalized(
  map: Record<string, string>,
  language: string,
  forced?: string,
): { lang: string; value: string } | null {
  const candidates: string[] = [];
  if (forced) candidates.push(forced);
  if (language) {
    candidates.push(language);
    const base = language.split('-')[0];
    if (base !== language) candidates.push(base);
  }
  candidates.push('en', 'de');
  for (const candidate of candidates) {
    const entry = findEntry(map, candidate);
    if (entry) return entry;
  }
  for (const [lang, value] of Object.entries(map)) {
    if (hasText(value)) return { lang, value };
  }
  return null;
}

let current: OperatorNotice | null = null;
let loading: Promise<void> | null = null;
const listeners = new Set<() => void>();

function apply(notice: OperatorNotice | null): void {
  current = notice;
  for (const listener of listeners) listener();
}

/**
 * Reads the body of the answer with a cap of `MAX_BODY_BYTES`. Never
 * buffers a large answer: a `Content-Length` above the cap is refused
 * without reading, and a streamed body stops the request as soon as the
 * cap passes. `null` means no notice. Without a readable stream it falls
 * back to `response.text()` and checks the length afterwards.
 */
async function readBodyCapped(response: Response, controller: AbortController): Promise<string | null> {
  const length = Number(response.headers?.get?.('Content-Length'));
  if (Number.isFinite(length) && length > MAX_BODY_BYTES) return null;
  if (response.body) {
    const reader = response.body.getReader();
    try {
      const chunks: Uint8Array[] = [];
      let received = 0;
      const decoder = new TextDecoder();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        received += value.byteLength;
        if (received > MAX_BODY_BYTES) {
          // The cap passed: stop the request, not just this reader.
          await reader.cancel();
          controller.abort();
          return null;
        }
        chunks.push(value);
      }
      return decoder.decode(concatBytes(chunks));
    } finally {
      reader.releaseLock?.();
    }
  }
  const body = await response.text();
  if (body.length > MAX_BODY_BYTES) return null;
  return body;
}

/** Joins byte chunks in one copy, for a single decode. */
function concatBytes(chunks: Uint8Array[]): Uint8Array {
  let total = 0;
  for (const chunk of chunks) total += chunk.byteLength;
  const out = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return out;
}

async function fetchNotice(signal: AbortSignal, controller: AbortController): Promise<OperatorNotice | null> {
  const response = await fetch(NOTICE_URL, { cache: 'no-cache', signal });
  if (response.status !== 200) return null;
  const body = await readBodyCapped(response, controller);
  if (body === null) return null;
  // A dev server answers an unknown path with its `index.html`. That throws
  // here and ends as no notice.
  return parseOperatorNotice(JSON.parse(body));
}

/**
 * Fetches the notice file and its logos again after the first visit's load,
 * so the service worker stores them in `operator-notice-cache`. Never
 * delays the first render: the load calls it without awaiting. Never
 * changes what shows: the results are ignored. Runs at most once per page
 * load, and not at all when a worker already controlled the page before
 * the first request of the notice started.
 */
async function warmServiceWorkerCache(notice: OperatorNotice, wasControlled: boolean): Promise<void> {
  try {
    const container = navigator.serviceWorker;
    if (!container || wasControlled) return;
    await container.ready;
    // `ready` can resolve before `clients.claim()` takes effect, so the page
    // may still be uncontrolled. A fetch now would go past the worker again.
    // One listener, removed after it fires. Detached: the load never waits
    // on the warm-up, and a change that never comes leaves nothing behind.
    if (!container.controller) {
      await new Promise<void>((resolve) => {
        const onChange = () => {
          container.removeEventListener('controllerchange', onChange);
          resolve();
        };
        container.addEventListener('controllerchange', onChange);
      });
    }
    const urls = [NOTICE_URL];
    for (const logo of notice.logos) {
      urls.push(`/operator/${logo.src}`);
      if (logo.src_dark) urls.push(`/operator/${logo.src_dark}`);
    }
    // Each fetch stands alone: one failure must not skip the rest.
    for (const url of urls) {
      try {
        await fetch(url, { cache: 'no-cache' });
      } catch {
        // The warm-up is best effort.
      }
    }
  } catch {
    // A missing worker or a `ready` that never settles: the first visit
    // stays unaffected, a later offline visit simply has no cache yet.
  }
}

/**
 * Loads `/operator/notice.json` once. A second call shares the first load.
 * Never rejects and ends after at most 5 seconds: any failure keeps `null`.
 */
export function loadOperatorNotice(): Promise<void> {
  if (loading) return loading;
  loading = (async () => {
    const container = navigator.serviceWorker;
    // Captured before the first request starts: a worker that claims the
    // page while the answer is on its way must not suppress the warm-up,
    // because the first request went past the worker.
    const wasControlled = Boolean(container?.controller);
    const controller = new AbortController();
    let timeoutId: ReturnType<typeof setTimeout> | undefined;
    // The abort ends a real fetch and its body. The race also ends the load
    // when something under the fetch ignores the signal.
    const timeout = new Promise<null>((resolve) => {
      timeoutId = setTimeout(() => {
        controller.abort();
        resolve(null);
      }, LOAD_TIMEOUT_MS);
    });
    try {
      const notice = await Promise.race([fetchNotice(controller.signal, controller), timeout]);
      if (notice) {
        apply(notice);
        // Not awaited: the first render never waits on the worker.
        void warmServiceWorkerCache(notice, wasControlled);
      }
    } catch {
      // No file, offline, aborted, no JSON: no notice.
    } finally {
      clearTimeout(timeoutId);
    }
  })();
  return loading;
}

/** The notice as loaded so far: `null` until a valid file has arrived. */
export function getOperatorNotice(): OperatorNotice | null {
  return current;
}

export function subscribeOperatorNotice(onStoreChange: () => void): () => void {
  listeners.add(onStoreChange);
  return () => {
    listeners.delete(onStoreChange);
  };
}

/**
 * React binding. The first render sees `null`, and the component renders
 * again when the notice arrives, without a reload.
 */
export function useOperatorNotice(): OperatorNotice | null {
  return useSyncExternalStore(subscribeOperatorNotice, getOperatorNotice);
}

/**
 * Test-only: reset the module singleton between cases, because vitest keeps
 * module state per file. Sets `notice` like a finished load.
 */
export function __resetOperatorNoticeForTests(notice: OperatorNotice | null = null): void {
  loading = null;
  apply(notice);
}
