/**
 * The service worker's runtime cache for /api/ answers. The NetworkFirst rule
 * in vite.config.ts writes it so a run keeps working offline. Every session
 * change drops it, so no private answer outlives its session in the browser.
 */

/** The cache name of the worker's /api/ rule. Keep it in sync with vite.config.ts. */
export const API_CACHE_NAME = 'api-cache';

/**
 * Deletes the service worker's /api/ cache. Where the Cache Storage API is
 * missing (jsdom, older browsers) there is nothing to delete. A failed deletion
 * never rejects, because a session change must not fail on cache housekeeping.
 */
export async function clearApiCache(): Promise<void> {
  if (typeof caches === 'undefined') return;
  try {
    await caches.delete(API_CACHE_NAME);
  } catch {
    /* best-effort */
  }
}
