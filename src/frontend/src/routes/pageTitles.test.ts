import { describe, it, expect } from 'vitest';
import appSource from '../App.tsx?raw';
import deCommon from '../i18n/locales/de/common.json';
import enCommon from '../i18n/locales/en/common.json';
import { PAGE_TITLE_KEYS, findPageTitleKey } from './pageTitles';

/**
 * Routes of `App.tsx` that only redirect (or that are a layout whose index
 * redirects): they never show a page of their own, so they get no page name.
 */
const REDIRECT_ROUTES = [
  '/join',
  '/join/:code/team',
  '/runs/:runId/participant',
  '/runs/:runId/participant/team',
  '/runs/:runId/participant/chat',
  '/runs/:runId/participant/scoreboard',
  '/settings',
  '*',
];

/**
 * Every `path` attribute of a `<Route>` in the source text, with the path of
 * its parent routes in front. Path-less layouts pass the parent through.
 */
function collectRoutePaths(source: string): string[] {
  const text = source.replace(/\{\/\*[\s\S]*?\*\/\}/g, '');
  const paths: string[] = [];
  const stack: string[] = [];
  const tag = /<Route\b|<\/Route>/g;
  let match: RegExpExecArray | null;
  while ((match = tag.exec(text)) !== null) {
    if (match[0] === '</Route>') {
      stack.pop();
      continue;
    }
    // Read the opening tag up to its `>` outside any `{…}` expression.
    let depth = 0;
    let i = match.index + match[0].length;
    let attributes = '';
    for (; i < text.length; i += 1) {
      const ch = text[i];
      if (ch === '{') depth += 1;
      else if (ch === '}') depth -= 1;
      else if (ch === '>' && depth === 0) break;
      else if (depth === 0) attributes += ch;
    }
    const selfClosing = attributes.trimEnd().endsWith('/');
    const own = /\bpath="([^"]*)"/.exec(attributes)?.[1];
    const parent = stack.length > 0 ? stack[stack.length - 1] : '';
    let full = parent;
    if (own !== undefined) {
      full = own.startsWith('/') || own === '*' ? own : `${parent.replace(/\/$/, '')}/${own}`;
      paths.push(full);
    }
    tag.lastIndex = i;
    if (!selfClosing) stack.push(full);
  }
  return paths;
}

function lookup(tree: unknown, key: string): unknown {
  return key.split('.').reduce<unknown>(
    (node, part) => (node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined),
    tree,
  );
}

describe('pageTitles', () => {
  const routePaths = collectRoutePaths(appSource);

  it('reads the routes of App.tsx (guards the reader itself)', () => {
    expect(routePaths).toContain('/login');
    expect(routePaths).toContain('/runs/:runId/participant/stations/:stationId/challenges/:challengeId');
    expect(routePaths).toContain('/runs/new');
    expect(routePaths).toContain('/rallyes/:rallyeId');
    expect(routePaths).toContain('*');
  });

  it('gives every route path of App.tsx an entry or a redirect mark', () => {
    const missing = routePaths.filter(
      (path) => !(path in PAGE_TITLE_KEYS) && !REDIRECT_ROUTES.includes(path),
    );
    expect(missing).toEqual([]);
  });

  it('has no entry for a path that does not exist, and no redirect that also has a name', () => {
    expect(Object.keys(PAGE_TITLE_KEYS).filter((p) => !routePaths.includes(p))).toEqual([]);
    expect(REDIRECT_ROUTES.filter((p) => !routePaths.includes(p))).toEqual([]);
    expect(REDIRECT_ROUTES.filter((p) => p in PAGE_TITLE_KEYS)).toEqual([]);
  });

  it('points every entry at a pageTitle key that both languages translate', () => {
    for (const key of Object.values(PAGE_TITLE_KEYS)) {
      expect(key.startsWith('pageTitle.')).toBe(true);
      expect(typeof lookup(deCommon, key), `de ${key}`).toBe('string');
      expect(typeof lookup(enCommon, key), `en ${key}`).toBe('string');
    }
  });

  it('gives a list, its create form and its detail page three names', () => {
    for (const base of ['/rallyes', '/stations', '/challenges', '/runs']) {
      const keys = [PAGE_TITLE_KEYS[base], PAGE_TITLE_KEYS[`${base}/new`], PAGE_TITLE_KEYS[`${base}/:${
        { '/rallyes': 'rallyeId', '/stations': 'stationId', '/challenges': 'challengeId', '/runs': 'runId' }[base]
      }`]];
      expect(new Set(keys).size, base).toBe(3);
      for (const lang of [deCommon, enCommon]) {
        const names = keys.map((k) => lookup(lang, k));
        expect(new Set(names).size, `${base} ${names.join('|')}`).toBe(3);
      }
    }
  });

  it('calls a run "Lauf", as the product does everywhere else', () => {
    expect(lookup(deCommon, PAGE_TITLE_KEYS['/runs/:runId'])).toBe('Lauf');
    const titles = Object.values(PAGE_TITLE_KEYS).map((k) => String(lookup(deCommon, k)));
    expect(titles.filter((title) => /Durchlauf/.test(title))).toEqual([]);
    expect(lookup(enCommon, PAGE_TITLE_KEYS['/runs/:runId'])).toBe('Run');
  });

  describe('findPageTitleKey', () => {
    it('takes the most specific pattern', () => {
      expect(findPageTitleKey('/runs/new')).toBe(PAGE_TITLE_KEYS['/runs/new']);
      expect(findPageTitleKey('/runs/42')).toBe(PAGE_TITLE_KEYS['/runs/:runId']);
      expect(findPageTitleKey('/runs/42/submissions')).toBe(PAGE_TITLE_KEYS['/runs/:runId/submissions']);
      expect(findPageTitleKey('/stations/new')).toBe(PAGE_TITLE_KEYS['/stations/new']);
    });

    it('answers null for a path without an entry', () => {
      expect(findPageTitleKey('/nowhere/at/all')).toBeNull();
      expect(findPageTitleKey('/join')).toBeNull();
    });
  });
});
