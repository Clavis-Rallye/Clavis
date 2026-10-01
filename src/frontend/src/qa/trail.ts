/**
 * The action trail of the feedback bar: what happened on this device since the
 * current step began, sent with every event as evidence. It stands in for the
 * external CDP observer's journal where no observer runs, above all on a phone.
 *
 * Recorded: clicks (selector, and the name of a button, link, tab, menu item
 * or label), route changes, JavaScript errors, and failed API answers of the
 * App's `apiClient`. Never recorded: typed values, text from form fields or
 * editable content, clicks inside the bar, fragments, and secrets in
 * addresses or texts (`redactUrl`, `redactText`).
 *
 * Failed requests are read by wrapping the adapter of each `apiClient`
 * request, because the App's own response interceptor turns every error into
 * an `ApiError` without method or path before any later interceptor runs. The
 * wrapper hands back the adapter's own response or error unchanged.
 */
import axios, { type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios';
import apiClient from '../api/client';
import { resolveTarget, selectorOf } from './pointer';
import { REDACTED, redactUrl } from './qaApi';

export interface TrailEntry {
  t: 'click' | 'route' | 'error' | 'http';
  sel?: string;
  txt?: string;
  to?: string;
  msg?: string;
  method?: string;
  path?: string;
  status?: number;
  at: string;
}

export const TRAIL_MAX = 30;
const SEL_MAX = 200;
const TXT_MAX = 80;
const TO_MAX = 200;
const MSG_MAX = 200;
const PATH_MAX = 200;

function squash(value: string | null | undefined, max: number): string {
  return (value ?? '').replace(/\s+/g, ' ').trim().slice(0, max);
}

// Only a control's name is safe to record. Content can be a participant's answer,
// a team name or a code.
const NAMED_CONTROL = 'button,a,[role=button],[role=link],[role=tab],[role=menuitem],label';
const EDITABLE = '[contenteditable]:not([contenteditable="false"])';
// A whole text that looks like a join or share code.
const CODE_LIKE = /^[A-Z0-9]{4,10}$/;
// A join or share code inside a longer name, such as a copy button's "PP4Y8 — Code kopieren".
const CODE_WORD = /\b[A-Z0-9]{5,10}\b/g;
// A word that looks like a token: 20+ URL-safe characters with an upper, a lower and a digit.
const TOKEN_WORD = /[A-Za-z0-9_-]{20,}/g;
// An address inside a message: absolute, or a path from the root.
const URL_IN_TEXT = /https?:\/\/[^\s'"<>()]+|(?<![\w/.])\/[A-Za-z0-9_\-./]+(?:\?[^\s'"<>()]*)?/g;

function redactTokens(text: string): string {
  return text.replace(TOKEN_WORD, (word) =>
    /[A-Z]/.test(word) && /[a-z]/.test(word) && /\d/.test(word) ? REDACTED : word,
  );
}

/** A recorded text: a code-like text disappears whole, a token-like word inside it is replaced. */
export function redactText(text: string): string {
  if (CODE_LIKE.test(text)) return REDACTED;
  return redactTokens(text).replace(CODE_WORD, REDACTED);
}

/** An error message: every address in it is redacted, and every token-like word. */
export function redactMessage(text: string): string {
  return redactTokens(
    text.replace(URL_IN_TEXT, (address) => {
      // Sentence punctuation after an address belongs to the sentence.
      const tail = /[.,;:!?]+$/.exec(address)?.[0] ?? '';
      return redactUrl(tail ? address.slice(0, -tail.length) : address) + tail;
    }),
  ).replace(CODE_WORD, REDACTED);
}

/**
 * The name of a clicked control, or nothing. Form fields and editable content
 * give nothing. Of all other elements only buttons, links, tabs, menu items and
 * labels give a name: their `aria-label`, else their own text.
 */
function clickText(target: EventTarget | null, element: Element | null): string | undefined {
  if (!element) return undefined;
  const raw = target instanceof Element ? target : element;
  if (raw.closest(EDITABLE) || element.closest(EDITABLE)) return undefined;
  if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement) {
    return undefined;
  }
  if (!element.matches(NAMED_CONTROL)) return undefined;
  const own = element instanceof HTMLElement ? (element.innerText ?? element.textContent) : element.textContent;
  const name = squash(element.getAttribute('aria-label') || own, TXT_MAX);
  return name ? redactText(name) : undefined;
}

/** The path of a request URL, without query and fragment, and redacted. */
function pathOf(url: string | undefined, baseURL: string | undefined): string {
  if (!url) return '';
  let path: string;
  try {
    path = new URL(url, baseURL || window.location.origin).pathname;
  } catch {
    path = url.split(/[?#]/)[0];
  }
  return redactUrl(path).slice(0, PATH_MAX);
}

export class TrailRecorder {
  private entries: TrailEntry[] = [];
  private active = false;

  push(entry: Omit<TrailEntry, 'at'>): void {
    if (!this.active) return;
    this.entries.push({ ...entry, at: new Date().toISOString() });
    if (this.entries.length > TRAIL_MAX) this.entries.splice(0, this.entries.length - TRAIL_MAX);
  }

  clear(): void {
    this.entries = [];
  }

  snapshot(): TrailEntry[] {
    return this.entries.map((entry) => ({ ...entry }));
  }

  /** A route change, redacted: no fragment, no secret in the query or the path. */
  route(pathname: string, search: string): void {
    this.push({ t: 'route', to: redactUrl(pathname + search).slice(0, TO_MAX) });
  }

  /**
   * Start listening. Clicks inside `exclude` (the bar's container) are not
   * recorded. Answers the function that stops everything again.
   */
  start(exclude: HTMLElement): () => void {
    this.active = true;

    const onClick = (event: MouseEvent) => {
      if (event.target instanceof Node && exclude.contains(event.target)) return;
      const element = resolveTarget(event.target);
      const txt = clickText(event.target, element);
      this.push({ t: 'click', sel: selectorOf(element).slice(0, SEL_MAX), ...(txt ? { txt } : {}) });
    };
    const onError = (event: ErrorEvent) => {
      this.push({ t: 'error', msg: squash(redactMessage(String(event.message || event.error || 'error')), MSG_MAX) });
    };
    const onRejection = (event: PromiseRejectionEvent) => {
      const reason = event.reason as { message?: unknown } | undefined;
      const msg = reason && typeof reason.message === 'string' ? reason.message : String(event.reason);
      this.push({ t: 'error', msg: squash(redactMessage(`unhandled: ${msg}`), MSG_MAX) });
    };

    const record = (config: InternalAxiosRequestConfig, status: number) => {
      this.push({
        t: 'http',
        method: (config.method ?? 'get').toUpperCase(),
        path: pathOf(config.url, config.baseURL),
        status,
      });
    };
    const interceptor = apiClient.interceptors.request.use((config) => {
      const inner = axios.getAdapter(config.adapter ?? apiClient.defaults.adapter);
      const wrapped: AxiosAdapter = async (requestConfig) => {
        try {
          const response = await inner(requestConfig);
          if (response.status >= 400) record(requestConfig, response.status);
          return response;
        } catch (error) {
          const status = axios.isAxiosError(error) ? (error.response?.status ?? 0) : 0;
          record(requestConfig, status);
          throw error;
        }
      };
      config.adapter = wrapped;
      return config;
    });

    document.addEventListener('click', onClick, true);
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);
    return () => {
      this.active = false;
      document.removeEventListener('click', onClick, true);
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onRejection);
      apiClient.interceptors.request.eject(interceptor);
    };
  }
}
