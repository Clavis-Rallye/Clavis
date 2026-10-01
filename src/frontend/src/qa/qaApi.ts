/**
 * Transport of the feedback bar.
 *
 * Plain `fetch` with `credentials: 'omit'`, never the App's `apiClient`: the
 * App client redirects on 401 and carries the session cookie, and the bar must
 * neither move the tester nor touch a session.
 *
 * A browser takes part only after it joined a session through a join link
 * (`<origin>/#qa-join=<key>`). The key sits in the fragment, so it never
 * reaches a server log. It lives in `localStorage` and travels as `X-QA-Key`.
 * Without a key the bar sends no request at all.
 *
 * Notes that cannot be sent wait in an outbox and go out on the next poll.
 * Each keeps the step and the session it was made in, so the server can refuse
 * a stale move or a note from an ended session.
 *
 * Every address that leaves the browser goes through `redactUrl` first: no
 * fragment, no value of a secret-looking parameter, no token-like path segment.
 */
import i18n from '../i18n';
import type { TrailEntry } from './trail';

export type QaKind = 'ok' | 'skip' | 'note' | 'back' | 'goto';

export interface QaCard {
  id: string;
  block: string | null;
  block_title: string | null;
  title: string | null;
  do: string | null;
  expect: string | null;
  needs: string | null;
  role: string | null;
  device: string | null;
}

export interface QaBlock {
  id: string;
  title: string | null;
  first: string;
  count: number;
}

/** The state of a joined browser. A browser that is not joined gets `null`. */
export interface QaState {
  session_id: string | null;
  current: string | null;
  index: number;
  count: number;
  card: QaCard | null;
  blocks: QaBlock[];
  hidden: boolean;
  message: string | null;
  session: string | null;
  updated_at: string | null;
}

export interface QaContext {
  role: string | null;
  org: string | null;
  lang: string | null;
  viewport: string | null;
  user_agent: string | null;
}

export interface QaNoteInput {
  client_id: string;
  session_id: string | null;
  kind: QaKind;
  step: string | null;
  target: string | null;
  text: string | null;
  route: string;
  selector: string | null;
  element_text: string | null;
  answers_message: string | null;
  context: QaContext;
  trail: TrailEntry[];
}

interface QaNoteStored {
  id: string;
  seq: number;
  created_at: string;
  duplicate: boolean;
  moved: boolean;
  /** The state after the event, `null` when the answer said the browser is no longer joined. */
  state: QaState | null;
}

type SubmitResult =
  | { status: 'stored'; note: QaNoteStored }
  | { status: 'queued' }
  | { status: 'unjoined' }
  | { status: 'ended' }
  | { status: 'failed'; httpStatus: number };

const QA_STATE_URL = '/api/qa/state';
export const QA_NOTES_URL = '/api/qa/notes';
export const OUTBOX_KEY = 'clavis.qa.outbox';
export const HIDDEN_KEY = 'clavis.qa.hidden';
const DOCK_KEY = 'clavis.qa.dock';
export const KEY_STORAGE_KEY = 'clavis.qa.key';
export const SESSION_STORAGE_KEY = 'clavis.qa.session';
export const JOIN_PARAM = 'qa-join';
const KEY_HEADER = 'X-QA-Key';

// The API's field limits. Cutting here keeps a long note from being refused as a whole with 422.
const LIMITS = {
  client_id: 64,
  session_id: 64,
  step: 32,
  target: 32,
  text: 4000,
  route: 500,
  selector: 500,
  element_text: 300,
  answers_message: 2000,
  role: 32,
  org: 100,
  lang: 16,
  viewport: 20,
  user_agent: 200,
  trail: 30,
} as const;

// An outbox that grows without bound would fill localStorage on a phone that
// stays offline. The oldest notes go first.
const OUTBOX_MAX = 200;

/** An HTTP answer the API gave, as opposed to no answer at all. */
export class QaHttpError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`QA API answered ${status}`);
    this.name = 'QaHttpError';
    this.status = status;
  }
}

/**
 * Network failures, rate limits and server errors are worth a retry. A 4xx
 * other than 408 and 429 (validation, an ended key with 401, an ended session
 * with 409, the module switched off) never heals.
 */
function isRetryable(error: unknown): boolean {
  if (error instanceof QaHttpError) {
    return error.status === 408 || error.status === 429 || error.status >= 500;
  }
  return true;
}

function cut(value: string | null | undefined, max: number): string | null {
  if (value === null || value === undefined) return null;
  return value.length > max ? value.slice(0, max) : value;
}

function text(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

// ------------------------------------------------------------------- key ---

export function readKey(): string | null {
  try {
    const key = localStorage.getItem(KEY_STORAGE_KEY);
    return key && key.trim() ? key : null;
  } catch {
    return null;
  }
}

export function writeKey(key: string): void {
  try {
    localStorage.setItem(KEY_STORAGE_KEY, key);
  } catch {
    // Blocked storage: the bar works for this page only.
  }
}

export function clearKey(): void {
  try {
    localStorage.removeItem(KEY_STORAGE_KEY);
    localStorage.removeItem(SESSION_STORAGE_KEY);
  } catch {
    // Nothing stored, nothing to clear.
  }
}

export function readSession(): string | null {
  try {
    return localStorage.getItem(SESSION_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function writeSession(sessionId: string | null): void {
  try {
    if (sessionId) localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
    else localStorage.removeItem(SESSION_STORAGE_KEY);
  } catch {
    // Blocked storage: the bar sends the session of the state it shows.
  }
}

/**
 * Read `#qa-join=<key>`. Answers the key, or null, and the
 * fragment without that one entry, so the address bar never keeps the key.
 * The query string never joins: a key there would reach the server's logs.
 */
export function takeJoinHash(hash: string): { key: string | null; rest: string } {
  const params = new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : hash);
  const raw = params.get(JOIN_PARAM);
  if (raw === null) return { key: null, rest: hash };
  params.delete(JOIN_PARAM);
  const rest = params.toString();
  const key = raw.trim();
  return { key: key || null, rest: rest ? `#${rest}` : '' };
}

// Parameter names whose values never leave the browser.
const SECRET_PARAM = /^(token|code|key|secret|password|pass|otp|totp|qa-join|invite|reset)$/i;
// A path segment that looks like a token: at least 20 characters of the URL-safe
// alphabet with an uppercase letter, a lowercase letter and a digit. Lowercase
// slugs and UUIDs stay readable.
const TOKEN_SEGMENT = /^[A-Za-z0-9_-]{20,}$/;
export const REDACTED = ':redacted';

function redactSegment(segment: string): string {
  if (TOKEN_SEGMENT.test(segment) && /[A-Z]/.test(segment) && /[a-z]/.test(segment) && /\d/.test(segment)) {
    return REDACTED;
  }
  return segment;
}

function redactQuery(query: string): string {
  return query
    .split('&')
    .map((part) => {
      const eq = part.indexOf('=');
      const rawName = eq >= 0 ? part.slice(0, eq) : part;
      let name = rawName;
      try {
        name = decodeURIComponent(rawName.replace(/\+/g, ' '));
      } catch {
        // A malformed escape: judge the raw name.
      }
      return SECRET_PARAM.test(name) && eq >= 0 ? `${rawName}=${REDACTED}` : part;
    })
    .join('&');
}

/**
 * An address as it may leave the browser: no fragment, the value of every
 * secret-looking parameter replaced, and every token-like path segment replaced.
 * Takes a path with an optional query, or an absolute URL.
 */
export function redactUrl(input: string): string {
  let rest = input;
  let origin = '';
  const scheme = /^[a-z][a-z0-9+.-]*:\/\/[^/?#]*/i.exec(rest);
  if (scheme) {
    origin = scheme[0];
    rest = rest.slice(origin.length);
  }
  const hashAt = rest.indexOf('#');
  if (hashAt >= 0) rest = rest.slice(0, hashAt);
  const queryAt = rest.indexOf('?');
  const path = queryAt >= 0 ? rest.slice(0, queryAt) : rest;
  const query = queryAt >= 0 ? rest.slice(queryAt + 1) : null;
  const safePath = path.split('/').map(redactSegment).join('/');
  return origin + safePath + (query !== null ? `?${redactQuery(query)}` : '');
}

// ------------------------------------------------------------------ state ---

function normaliseCard(raw: unknown): QaCard | null {
  if (!raw || typeof raw !== 'object') return null;
  const card = raw as Record<string, unknown>;
  if (typeof card.id !== 'string') return null;
  return {
    id: card.id,
    block: text(card.block),
    block_title: text(card.block_title),
    title: text(card.title),
    do: text(card.do),
    expect: text(card.expect),
    needs: text(card.needs),
    role: text(card.role),
    device: text(card.device),
  };
}

function normaliseBlocks(raw: unknown): QaBlock[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const block = item as Record<string, unknown>;
    if (typeof block.id !== 'string' || typeof block.first !== 'string') return [];
    return [
      {
        id: block.id,
        title: text(block.title),
        first: block.first,
        count: typeof block.count === 'number' ? block.count : 0,
      },
    ];
  });
}

/** The state of a joined browser, or `null` when the answer says it is not joined. */
export function normaliseState(raw: unknown): QaState | null {
  if (!raw || typeof raw !== 'object') return null;
  const body = raw as Record<string, unknown>;
  if (body.joined !== true) return null;
  return {
    session_id: text(body.session_id),
    current: text(body.current),
    index: typeof body.index === 'number' ? body.index : 0,
    count: typeof body.count === 'number' ? body.count : 0,
    card: normaliseCard(body.card),
    blocks: normaliseBlocks(body.blocks),
    hidden: body.hidden === true,
    message: text(body.message),
    session: text(body.session),
    updated_at: text(body.updated_at),
  };
}

export async function fetchState(key: string): Promise<QaState | null> {
  const response = await fetch(QA_STATE_URL, {
    credentials: 'omit',
    cache: 'no-store',
    headers: { [KEY_HEADER]: key },
  });
  if (response.status === 401) return null;
  if (!response.ok) throw new QaHttpError(response.status);
  return normaliseState(await response.json());
}

async function postNote(note: QaNoteInput, key: string): Promise<QaNoteStored> {
  const response = await fetch(QA_NOTES_URL, {
    method: 'POST',
    credentials: 'omit',
    headers: { 'Content-Type': 'application/json', [KEY_HEADER]: key },
    body: JSON.stringify(note),
  });
  if (!response.ok) throw new QaHttpError(response.status);
  const body = (await response.json()) as Record<string, unknown>;
  return {
    id: String(body.id ?? ''),
    seq: typeof body.seq === 'number' ? body.seq : 0,
    created_at: String(body.created_at ?? ''),
    duplicate: body.duplicate === true,
    moved: body.moved === true,
    state: normaliseState(body.state),
  };
}

export function newClientId(): string {
  // randomUUID exists only in secure contexts. A plain-http LAN stand still
  // needs an id that is unique enough for one tester's notes.
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `qa-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export function buildContext(role: string | null, org: string | null): QaContext {
  return {
    role: cut(role, LIMITS.role),
    org: cut(org, LIMITS.org),
    lang: cut(i18n.resolvedLanguage ?? i18n.language ?? null, LIMITS.lang),
    viewport: cut(`${window.innerWidth}x${window.innerHeight}`, LIMITS.viewport),
    user_agent: cut(navigator.userAgent, LIMITS.user_agent),
  };
}

/** Bring every field inside the API's limits. */
export function clampNote(note: QaNoteInput): QaNoteInput {
  return {
    ...note,
    client_id: note.client_id.slice(0, LIMITS.client_id),
    session_id: cut(note.session_id, LIMITS.session_id),
    step: cut(note.step, LIMITS.step),
    target: cut(note.target, LIMITS.target),
    text: cut(note.text, LIMITS.text),
    route: redactUrl(note.route).slice(0, LIMITS.route),
    selector: cut(note.selector, LIMITS.selector),
    element_text: cut(note.element_text, LIMITS.element_text),
    answers_message: cut(note.answers_message, LIMITS.answers_message),
    trail: (note.trail ?? []).slice(-LIMITS.trail),
  };
}

// ----------------------------------------------------------------- outbox ---

export function readOutbox(): QaNoteInput[] {
  try {
    const raw = localStorage.getItem(OUTBOX_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as QaNoteInput[]) : [];
  } catch {
    return [];
  }
}

function writeOutbox(items: QaNoteInput[]): void {
  try {
    if (items.length === 0) localStorage.removeItem(OUTBOX_KEY);
    else localStorage.setItem(OUTBOX_KEY, JSON.stringify(items.slice(-OUTBOX_MAX)));
  } catch {
    // Storage full or blocked: the note is lost, the toast already said "queued".
  }
}

export function enqueue(note: QaNoteInput): void {
  const items = readOutbox().filter((item) => item.client_id !== note.client_id);
  items.push(note);
  writeOutbox(items);
}

let flushing: Promise<number> | null = null;

/**
 * Send the queued notes in order, with the key the browser holds now. Each
 * note keeps the session it was made in. Stops at the first retryable failure
 * and keeps the rest. Drops a note the API refuses for good, silently: 401 for
 * an ended key, 409 for an ended session. Answers how many notes are still
 * queued. Concurrent calls share one run.
 */
export function flushOutbox(key: string | null = readKey()): Promise<number> {
  if (flushing) return flushing;
  const run = (async () => {
    if (!key) return readOutbox().length;
    for (const note of readOutbox()) {
      try {
        await postNote(note, key);
      } catch (error) {
        if (isRetryable(error)) break;
      }
      writeOutbox(readOutbox().filter((item) => item.client_id !== note.client_id));
    }
    return readOutbox().length;
  })();
  // Clear the guard after the run, never inside it: with an empty outbox the
  // body finishes synchronously, before `flushing` is even assigned, and a
  // `finally` inside would leave a settled promise in the guard for good.
  flushing = run;
  const clear = () => {
    if (flushing === run) flushing = null;
  };
  run.then(clear, clear);
  return run;
}

// ------------------------------------------------------------- observer ---

/**
 * The observer channel: the external CDP observer journals every console line
 * that starts with `[OBS]` and takes a screenshot for `t: 'feedback'`.
 */
function emitObserverLine(note: QaNoteInput, stored: boolean, noteId: string | null): void {
  try {
    console.debug(
      '[OBS]' +
        JSON.stringify({
          t: 'feedback',
          kind: note.kind,
          step: note.step,
          target: note.target,
          text: note.text?.slice(0, 200) ?? null,
          sel: note.selector,
          el: note.element_text,
          note_id: noteId,
          client_id: note.client_id,
          stored,
        }),
    );
  } catch {
    // Never let the observer channel break the bar.
  }
}

/** Store a note, or queue it when the network or the server fails. */
export async function submitNote(input: QaNoteInput, key: string): Promise<SubmitResult> {
  const note = clampNote(input);
  try {
    const stored = await postNote(note, key);
    emitObserverLine(note, true, stored.id);
    return { status: 'stored', note: stored };
  } catch (error) {
    if (error instanceof QaHttpError && error.status === 401) return { status: 'unjoined' };
    if (error instanceof QaHttpError && error.status === 409) return { status: 'ended' };
    if (isRetryable(error)) {
      enqueue(note);
      emitObserverLine(note, false, null);
      return { status: 'queued' };
    }
    return { status: 'failed', httpStatus: error instanceof QaHttpError ? error.status : 0 };
  }
}

// --------------------------------------------------------- local hiding ---

export function readLocalHidden(): boolean {
  try {
    return localStorage.getItem(HIDDEN_KEY) === '1';
  } catch {
    return false;
  }
}

export function writeLocalHidden(hidden: boolean): void {
  try {
    if (hidden) localStorage.setItem(HIDDEN_KEY, '1');
    else localStorage.removeItem(HIDDEN_KEY);
  } catch {
    // Blocked storage: the choice holds for this page only.
  }
}

/**
 * Read `?qa=off` and `?qa=on`. Stores the choice and answers it, or
 * answers null when the parameter is absent or unknown.
 */
export function applyQaParam(search: string): boolean | null {
  const value = new URLSearchParams(search).get('qa');
  if (value === 'off') {
    writeLocalHidden(true);
    return true;
  }
  if (value === 'on') {
    writeLocalHidden(false);
    return false;
  }
  return null;
}

export type QaDock = 'top' | 'bottom';

export function readDock(): QaDock {
  try {
    return localStorage.getItem(DOCK_KEY) === 'top' ? 'top' : 'bottom';
  } catch {
    return 'bottom';
  }
}

export function writeDock(dock: QaDock): void {
  try {
    localStorage.setItem(DOCK_KEY, dock);
  } catch {
    // Blocked storage: the dock holds for this page only.
  }
}
