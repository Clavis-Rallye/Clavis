import { afterEach, describe, expect, it, vi } from 'vitest';
import { API_CACHE_NAME, clearApiCache } from './apiCache';

// jsdom has no Cache Storage API, so the tests stub it the way the browser
// provides it: as a global with delete().
describe('clearApiCache', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('deletes the api-cache through the Cache Storage API', async () => {
    const deleteCache = vi.fn().mockResolvedValue(true);
    vi.stubGlobal('caches', { delete: deleteCache });

    await clearApiCache();

    expect(deleteCache).toHaveBeenCalledTimes(1);
    expect(deleteCache).toHaveBeenCalledWith(API_CACHE_NAME);
  });

  it('is the cache name the service worker rule in vite.config.ts uses', () => {
    expect(API_CACHE_NAME).toBe('api-cache');
  });

  it('does nothing where the Cache Storage API is missing', async () => {
    vi.stubGlobal('caches', undefined);

    await expect(clearApiCache()).resolves.toBeUndefined();
  });
});

// Workbox answers a request with
// the FIRST rule that matches it, so a private path must meet its
// NetworkOnly rule before any rule that stores. The navigation rule
// (pages-cache) is included, because a direct visit of /api/auth/me is a
// navigation request. The test reads the rule order from vite.config.ts.
describe('service worker rule order (vite.config.ts)', () => {
  type Rule = {
    urlPattern: RegExp | ((ctx: { request: { mode: string }; url: URL }) => boolean);
    handler: string;
  };

  const matches = (rule: Rule, url: string, mode: string): boolean => {
    const ctx = { request: { mode }, url: new URL(url) };
    return typeof rule.urlPattern === 'function'
      ? rule.urlPattern(ctx)
      : rule.urlPattern.test(url);
  };

  const origin = 'https://clavis.example';
  const privatePaths = [
    '/api/auth/me',
    '/api/auth/ws-token',
    '/api/instance/accounts',
    '/api/instance/settings',
    '/api/orgs/acme/users',
    '/api/orgs/acme/users/42',
    '/api/orgs/acme/invitations',
    '/api/orgs/acme/admin/logs',
    '/api/orgs/acme/admin/logs?format=csv',
    '/api/orgs/acme/admin/settings',
    '/api/orgs/acme/analytics',
    '/api/orgs/acme/analytics/overview?from=2026-01-01',
  ];

  for (const mode of ['navigate', 'cors']) {
    for (const path of privatePaths) {
      it(`${path} (${mode}) meets NetworkOnly before any rule that stores`, async () => {
        const { runtimeCaching } = await import('../../vite.config');
        const first = (runtimeCaching as unknown as Rule[]).find((rule) =>
          matches(rule, origin + path, mode),
        );
        expect(first?.handler).toBe('NetworkOnly');
      });
    }
  }

  it("still caches a run's stations for the offline participant", async () => {
    const { runtimeCaching } = await import('../../vite.config');
    const first = (runtimeCaching as unknown as Rule[]).find((rule) =>
      matches(rule, origin + '/api/runs/7/stations', 'cors'),
    );
    expect(first?.handler).toBe('NetworkFirst');
  });
});
