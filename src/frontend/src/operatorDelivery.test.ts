/**
 * How the web container delivers the operator's notice folder.
 *
 * The operator mounts a folder at /usr/share/nginx/html/operator and changes
 * its files without a new build. So nginx must answer from that folder alone,
 * never cache it for long, and never run a script of it. The service worker
 * must fetch it fresh while online and keep the last copy for offline use.
 * The web manifest moved to the API, which names the app after BRAND_NAME.
 *
 * The test reads the files as text, so it pins what text can pin.
 */
import { describe, expect, it } from 'vitest';
import nginxConf from '../nginx.conf?raw';
import indexHtml from '../index.html?raw';
import dockerfile from '../Dockerfile?raw';

type Rule = {
  urlPattern: RegExp | ((ctx: { request: { mode: string }; url: URL }) => boolean);
  handler: string;
  options?: {
    cacheName?: string;
    expiration?: { maxEntries?: number; maxAgeSeconds?: number };
    cacheableResponse?: { statuses?: number[] };
  };
};

const origin = 'https://clavis.example';

const matches = (rule: Rule, url: string, mode: string): boolean => {
  const ctx = { request: { mode }, url: new URL(url) };
  return typeof rule.urlPattern === 'function' ? rule.urlPattern(ctx) : rule.urlPattern.test(url);
};

async function rules(): Promise<Rule[]> {
  const { runtimeCaching } = await import('../vite.config');
  return runtimeCaching as unknown as Rule[];
}

/**
 * The body of the first `location <spec> { ... }` block, empty when there is
 * none. The blocks hold no nested braces.
 */
function location(spec: string): string {
  const start = nginxConf.indexOf(`location ${spec} {`);
  if (start < 0) return '';
  const open = nginxConf.indexOf('{', start);
  return nginxConf.slice(open + 1, nginxConf.indexOf('}', open));
}

/** The directives of a block, without its comments. */
function directives(block: string): string {
  return block
    .split('\n')
    .map((line) => line.replace(/#.*$/, '').trim())
    .filter(Boolean)
    .join('\n');
}

const SECURITY_HEADERS = [
  'add_header X-Content-Type-Options "nosniff" always;',
  'add_header X-Frame-Options "DENY" always;',
  'add_header Referrer-Policy "strict-origin-when-cross-origin" always;',
];

describe('nginx serves the operator folder (C1, C2)', () => {
  const block = directives(location('^~ /operator/'));

  it('answers a missing file with 404, never with the app shell', () => {
    expect(block).toContain('try_files $uri =404;');
    expect(block).not.toContain('index.html');
  });

  it('wins over the regex location of the static files', () => {
    // Only ^~ stops nginx from trying the regex locations, which set expires 1y.
    expect(nginxConf).toMatch(/location \^~ \/operator\/ \{/);
    expect(block).not.toContain('expires');
    expect(block).not.toContain('immutable');
  });

  it('asks the browser to check every file again', () => {
    expect(block).toContain('add_header Cache-Control "no-cache" always;');
  });

  it('carries the security headers of the app', () => {
    for (const header of SECURITY_HEADERS) {
      expect(block).toContain(header);
      // The same Referrer-Policy and the same plain headers as the server level.
      expect(nginxConf).toContain(header);
    }
  });

  it('gives every answer a policy that runs no script', () => {
    expect(block).toContain(
      `add_header Content-Security-Policy "default-src 'none'; style-src 'unsafe-inline'; sandbox" always;`,
    );
    expect(block).not.toContain('$csp_header');
  });

  it('keeps the policy of the app document', () => {
    expect(nginxConf).toContain('add_header Content-Security-Policy $csp_header always;');
    const appPolicy = /map \$host \$csp_header \{\s*default "([^"]+)";/.exec(nginxConf)?.[1] ?? '';
    expect(appPolicy).toContain("default-src 'self'; script-src 'self';");
    expect(appPolicy).toContain("manifest-src 'self'");
    expect(appPolicy).not.toContain('sandbox');
  });

  it('ships the folder in the image, empty', () => {
    const copy = dockerfile.indexOf('COPY --from=builder /app/dist /usr/share/nginx/html');
    const mkdir = dockerfile.indexOf('RUN mkdir -p /usr/share/nginx/html/operator');
    expect(copy).toBeGreaterThanOrEqual(0);
    expect(mkdir).toBeGreaterThan(copy);
  });
});

describe('the manifest comes from the API (C5)', () => {
  it('is linked once, at the API path', () => {
    const links = indexHtml.match(/<link\s+rel="manifest"[^>]*>/g) ?? [];
    expect(links).toHaveLength(1);
    expect(links[0]).toContain('href="/api/public/manifest.webmanifest"');
  });

  it('redirects the old path, which an app installed from 0.9.2 still asks for', () => {
    const block = directives(location('= /manifest.webmanifest'));
    expect(block).toContain('return 302 /api/public/manifest.webmanifest;');
    // Behind the TLS proxy an absolute Location would name http and port 80.
    expect(block).toContain('absolute_redirect off;');
    for (const header of SECURITY_HEADERS) {
      expect(block).toContain(header);
    }
  });

  it('is neither built nor precached', async () => {
    const { pwaOptions } = await import('../vite.config');
    expect(pwaOptions.manifest).toBe(false);
    expect(pwaOptions.workbox?.globPatterns?.join(' ')).not.toContain('webmanifest');
  });
});

describe('the service worker and the operator folder (C3)', () => {
  it('precaches nothing under /operator/', async () => {
    const { pwaOptions } = await import('../vite.config');
    expect(pwaOptions.workbox?.globIgnores).toContain('operator/**');
  });

  it('answers no navigation under /operator/ or to the old manifest with the app shell', async () => {
    const { pwaOptions } = await import('../vite.config');
    const denylist = pwaOptions.workbox?.navigateFallbackDenylist ?? [];
    for (const path of ['/operator/', '/operator/notice.json', '/operator/programme.svg', '/manifest.webmanifest']) {
      expect(denylist.some((pattern) => pattern.test(path)), path).toBe(true);
    }
  });

  it('asks the network first and keeps the last copy in its own cache', async () => {
    const rule = (await rules()).find((candidate) => candidate.options?.cacheName === 'operator-notice-cache');
    expect(rule).toBeDefined();
    expect(rule?.handler).toBe('NetworkFirst');
    expect(rule?.options?.expiration).toEqual({ maxEntries: 20, maxAgeSeconds: 60 * 60 * 24 * 30 });
    expect(rule?.options?.cacheableResponse).toEqual({ statuses: [200] });
    // No timeout: the cache answers only when the network fails.
    expect(rule?.options).not.toHaveProperty('networkTimeoutSeconds');
  });

  it('stands before the image rule and the navigation rule', async () => {
    const all = await rules();
    const operator = all.findIndex((rule) => rule.options?.cacheName === 'operator-notice-cache');
    const images = all.findIndex((rule) => rule.options?.cacheName === 'image-cache');
    const pages = all.findIndex((rule) => rule.options?.cacheName === 'pages-cache');
    expect(operator).toBeGreaterThanOrEqual(0);
    expect(operator).toBeLessThan(images);
    expect(operator).toBeLessThan(pages);
  });

  it('matches only paths under /operator/', async () => {
    const rule = (await rules()).find((candidate) => candidate.options?.cacheName === 'operator-notice-cache');
    expect(rule).toBeDefined();
    if (!rule) return;
    expect(matches(rule, origin + '/operator/notice.json', 'cors')).toBe(true);
    expect(matches(rule, origin + '/operator/programme.svg', 'no-cors')).toBe(true);
    expect(matches(rule, origin + '/logo.svg', 'no-cors')).toBe(false);
    expect(matches(rule, origin + '/api/operator/notice.json', 'cors')).toBe(false);
    expect(matches(rule, origin + '/', 'navigate')).toBe(false);
  });
});
