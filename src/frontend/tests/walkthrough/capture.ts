import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { outDir, type SeedAccount } from './seed';

/**
 * Walkthrough capture: the helpers every walk (staff, participant, guest) uses
 * to log in and to record evidence. capture() never fails on a UI finding. It
 * writes what it sees to out/<project>/<lang>/<persona>/<NN>-<step>/:
 *   screen.png    full page (css scale)
 *   viewport.png  viewport only
 *   aria.yml      ariaSnapshot of <body>
 *   axe.json      AxeBuilder result, WCAG 2.1 A+AA, informational only
 *   metrics.json  everything measurable, from ONE page.evaluate
 *   console.json  console errors/warnings since the previous capture,
 *                 plus walk notes (a step that could not find its element)
 *
 * NN is a two-digit running number per persona and viewport, so the folders
 * sort in walk order.
 */

/** WALK_LANG: 'de' (default) or 'en'. Anything else falls back to 'de'. */
export function walkLang(): 'de' | 'en' {
  return process.env.WALK_LANG === 'en' ? 'en' : 'de';
}

/**
 * The one place a step name becomes a folder name. Every capture goes through
 * it, so a step built from a domain value (a challenge type such as `qr_scan`)
 * still yields a kebab-case folder. Underscores and spaces become hyphens.
 */
export function kebabStep(step: string): string {
  return step
    .trim()
    .toLowerCase()
    .replace(/[\s_]+/g, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^-|-$/g, '');
}

interface WalkContext {
  project: string;
  lang: string;
  persona: string;
}

let current: WalkContext | null = null;
const stepCounters = new Map<string, number>();

/** Tell capture() which out/<project>/<lang>/<persona>/ subtree is active. */
export function setCaptureContext(ctx: WalkContext): void {
  current = ctx;
}

interface ConsoleEntry {
  type: string;
  text: string;
}

const consoleBuffer = new WeakMap<Page, ConsoleEntry[]>();

/** Collect console errors/warnings and page errors from now on. */
export function watchConsole(page: Page): void {
  if (consoleBuffer.has(page)) return;
  const entries: ConsoleEntry[] = [];
  consoleBuffer.set(page, entries);
  page.on('console', (msg) => {
    if (msg.type() === 'error' || msg.type() === 'warning') {
      entries.push({ type: msg.type(), text: msg.text() });
    }
  });
  page.on('pageerror', (err) => {
    entries.push({ type: 'error', text: `pageerror: ${err.message}` });
  });
}

/** Walk notes queued for the next capture's console.json. */
const pendingNotes: string[] = [];

/**
 * Record why a step could not do what it wanted (missing element, …). The
 * note lands in the console.json of the next capture. Findings never fail the walk.
 */
export function note(reason: string): void {
  pendingNotes.push(reason);
}

/**
 * Log in through the API so the access_token cookie lands in the browser
 * context, and pin clavis_language before the first navigation. A failed
 * login throws: that is one of the two things a walk legitimately fails on.
 */
export async function loginAs(page: Page, account: SeedAccount): Promise<void> {
  const resp = await page.request.post('/api/auth/login', {
    data: { email: account.email, password: account.password },
  });
  if (!resp.ok()) {
    throw new Error(`loginAs: /api/auth/login -> ${resp.status()}: ${await resp.text()}`);
  }
  const body = (await resp.json()) as { status?: string };
  if (body.status !== 'success') {
    throw new Error(`loginAs: login status ${JSON.stringify(body.status)} for ${account.email}`);
  }
  // Set before the first navigation so i18next reads the walk language, not
  // the browser locale.
  await page.addInitScript((lang) => localStorage.setItem('clavis_language', lang), walkLang());
}

/** Everything measurable about the page, from one page.evaluate. */
function metricsEval() {
  const doc = document.documentElement;
  const round1 = (n: number) => Math.round(n * 10) / 10;
  const visible = (el: Element) => {
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const nameOf = (el: Element) => {
    // A simplified accessible-name computation, in the order of the checks below.
    const clip = (text: string) => text.replace(/\s+/g, ' ').trim().slice(0, 120);
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel?.trim()) return clip(ariaLabel);
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      const text = labelledby
        .split(/\s+/)
        .map((id) => document.getElementById(id)?.textContent ?? '')
        .join(' ')
        .trim();
      if (text) return clip(text);
    }
    if (el.id) {
      const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      const text = label?.textContent ?? '';
      if (text.trim()) return clip(text);
    }
    const wrappingLabel = el.closest('label');
    if (wrappingLabel?.textContent?.trim()) return clip(wrappingLabel.textContent);
    if (el instanceof HTMLImageElement && el.alt.trim()) return clip(el.alt);
    const title = el.getAttribute('title');
    if (title?.trim()) return clip(title);
    const placeholder = el.getAttribute('placeholder');
    if (placeholder?.trim()) return clip(placeholder);
    return clip(el.textContent ?? '');
  };

  const INTERACTIVE =
    'a, button, [role=button], input, select, textarea, [role=switch], [role=tab], [role=menuitem]';
  const smallTargets: Array<Record<string, unknown>> = [];
  for (const el of Array.from(document.querySelectorAll(INTERACTIVE))) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 44 || r.height < 44) {
      smallTargets.push({
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute('role') || '',
        name: nameOf(el),
        width: round1(r.width),
        height: round1(r.height),
      });
    }
  }

  const smallText: Array<Record<string, unknown>> = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node: Node | null;
  while ((node = walker.nextNode()) && smallText.length < 20) {
    const text = (node.textContent || '').trim();
    if (!text) continue;
    const parent = node.parentElement;
    if (!parent || !visible(parent)) continue;
    const fontSize = parseFloat(getComputedStyle(parent).fontSize);
    if (fontSize < 12) {
      smallText.push({ fontSize: round1(fontSize), text: text.slice(0, 120) });
    }
  }

  const headings: Record<string, number> = {};
  for (const h of Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6'))) {
    const level = h.tagName.toLowerCase();
    headings[level] = (headings[level] || 0) + 1;
  }

  let largest: Record<string, unknown> | null = null;
  let largestArea = 0;
  for (const img of Array.from(document.querySelectorAll('img'))) {
    if (!img.naturalWidth || !img.naturalHeight) continue;
    const area = img.naturalWidth * img.naturalHeight;
    if (!largest || area > largestArea) {
      const r = img.getBoundingClientRect();
      largest = {
        naturalWidth: img.naturalWidth,
        naturalHeight: img.naturalHeight,
        renderedWidth: round1(r.width),
        renderedHeight: round1(r.height),
      };
      largestArea = area;
    }
  }

  const active = document.activeElement;
  const hasFocus = !!active && active !== document.body && active !== doc;

  return {
    url: location.href,
    title: document.title,
    lang: doc.getAttribute('lang'),
    documentScrollWidth: doc.scrollWidth,
    clientWidth: doc.clientWidth,
    horizontalOverflow: doc.scrollWidth > doc.clientWidth,
    smallTargetCount: smallTargets.length,
    smallTargets,
    smallText,
    headings,
    firstH1: document.querySelector('h1')?.textContent?.trim().slice(0, 200) || null,
    liveRegionCount: document.querySelectorAll('[aria-live], [role=status]').length,
    hasFocus,
    focusName: hasFocus && active ? nameOf(active) : null,
    largestImage: largest,
    viewport: { width: window.innerWidth, height: window.innerHeight },
    capturedAt: new Date().toISOString(),
  };
}

/**
 * Wait for the page to settle, then write all evidence for this step.
 * Returns the capture folder (relative to tests/walkthrough/out/).
 */
export async function capture(page: Page, step: string): Promise<string> {
  if (!current) {
    throw new Error('capture: no capture context — call setCaptureContext() first');
  }
  watchConsole(page); // defensive: never lose console output

  await page.waitForLoadState('networkidle', { timeout: 5_000 }).catch(() => {});
  await Promise.race([
    page.evaluate(() => document.fonts.ready),
    new Promise((resolve) => setTimeout(resolve, 5_000)),
  ]);

  const { project, lang, persona } = current;
  const key = `${project}/${lang}/${persona}`;
  const nn = String((stepCounters.get(key) ?? 0) + 1).padStart(2, '0');
  stepCounters.set(key, Number(nn));

  const name = kebabStep(step);
  const folder = path.join(project, lang, persona, `${nn}-${name}`);
  const abs = path.join(outDir(), folder);
  await mkdir(abs, { recursive: true });

  await page.screenshot({ path: path.join(abs, 'screen.png'), fullPage: true, scale: 'css' });
  await page.screenshot({ path: path.join(abs, 'viewport.png'), scale: 'css' });

  await writeFile(path.join(abs, 'aria.yml'), await page.locator('body').ariaSnapshot(), 'utf8');

  let axe: unknown;
  try {
    axe = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();
  } catch (err) {
    // axe is evidence, not a gate. A broken run is recorded, never thrown.
    axe = { error: String(err) };
  }
  await writeFile(path.join(abs, 'axe.json'), JSON.stringify(axe, null, 2));

  const metrics = await page.evaluate(metricsEval);
  await writeFile(path.join(abs, 'metrics.json'), JSON.stringify(metrics, null, 2));

  const entries = consoleBuffer.get(page) ?? [];
  const drained = entries.splice(0, entries.length);
  const notes = pendingNotes.splice(0, pendingNotes.length);
  const consoleJson = {
    step: `${nn}-${name}`,
    errors: drained.filter((e) => e.type === 'error'),
    warnings: drained.filter((e) => e.type === 'warning'),
    notes: notes.map((text) => ({ type: 'note', text })),
  };
  await writeFile(path.join(abs, 'console.json'), JSON.stringify(consoleJson, null, 2));

  return folder;
}
