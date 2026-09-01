import { useSyncExternalStore } from 'react';
import i18n from '../i18n';

/**
 * Instance-wide configuration from `GET /api/public/config`. `main.tsx`
 * fetches it once before the first render, so headlines, the document title
 * and route gates (`/register`, onboarding's organization step) never flash a
 * wrong value.
 *
 * Until the fetch resolves, `getInstanceConfig()` answers the defaults. When
 * offline (this is a PWA), on a timeout or on a non-200 answer the app renders
 * with the defaults, and the server-side gates stay authoritative.
 */
interface InstanceConfig {
  brand_name: string;
  bootstrap_open: boolean;
  single_org_slug: string | null;
  /** The feedback module for test sessions. Off by default. */
  qa_feedback: boolean;
}

export const DEFAULT_INSTANCE_CONFIG: InstanceConfig = {
  brand_name: 'Clavis',
  bootstrap_open: false,
  single_org_slug: null,
  qa_feedback: false,
};

const BOOT_TIMEOUT_MS = 3000;

let current: InstanceConfig = DEFAULT_INSTANCE_CONFIG;
let fetchStarted = false;
const listeners = new Set<() => void>();

function apply(config: InstanceConfig): void {
  current = config;
  // The plain brand name until the first route is known. `DocumentTitle`
  // adds the page name from there on and re-writes the title when this brand
  // changes.
  document.title = config.brand_name;
  // i18next merges `interpolation.defaultVariables` into every translation,
  // so locale strings use `{{brand}}` without each call site passing it.
  // `i18n/index.ts` seeds the default brand, and this replaces it.
  const interpolation = i18n.options.interpolation;
  if (interpolation) {
    interpolation.defaultVariables = {
      ...interpolation.defaultVariables,
      brand: config.brand_name,
    };
  }
  for (const listener of listeners) listener();
}

/**
 * Fetch the instance config once. Never rejects: any failure, a malformed
 * payload included, keeps the defaults, so `main.tsx` can always render.
 */
export async function loadInstanceConfig(): Promise<InstanceConfig> {
  if (fetchStarted) return current;
  fetchStarted = true;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), BOOT_TIMEOUT_MS);
  try {
    const response = await fetch('/api/public/config', {
      signal: controller.signal,
    });
    if (response.ok) {
      const body = (await response.json()) as Partial<InstanceConfig>;
      apply({
        brand_name:
          typeof body.brand_name === 'string' && body.brand_name.trim()
            ? body.brand_name
            : DEFAULT_INSTANCE_CONFIG.brand_name,
        // Only an explicit `true` opens the bootstrap. Fail closed: the API
        // gate (403 ORG_BOOTSTRAP_CLOSED) decides anyway.
        bootstrap_open: body.bootstrap_open === true,
        single_org_slug:
          typeof body.single_org_slug === 'string' ? body.single_org_slug : null,
        // Same fail-closed rule as the bootstrap gate: only an explicit
        // `true` loads the feedback bar.
        qa_feedback: body.qa_feedback === true,
      });
    }
  } catch {
    // Offline, timed out or unparsable — keep the defaults.
  } finally {
    clearTimeout(timeoutId);
  }
  return current;
}

/** The config as resolved so far — the defaults until boot has finished. */
export function getInstanceConfig(): InstanceConfig {
  return current;
}

function subscribe(onStoreChange: () => void): () => void {
  listeners.add(onStoreChange);
  return () => {
    listeners.delete(onStoreChange);
  };
}

/**
 * React binding. The first snapshot is final in practice, because the boot
 * fetch resolves before the first render. The subscription covers a config
 * that arrives later, such as a slow first fetch racing a route change.
 */
export function useInstanceConfig(): InstanceConfig {
  return useSyncExternalStore(subscribe, getInstanceConfig);
}

/**
 * Test-only: reset the module singleton between cases, because vitest keeps
 * module state per file. Applies `config` like a boot fetch.
 */
export function __resetInstanceConfigForTests(
  config: Omit<InstanceConfig, 'qa_feedback'> & Partial<Pick<InstanceConfig, 'qa_feedback'>> = DEFAULT_INSTANCE_CONFIG,
): void {
  fetchStarted = false;
  // A caller may omit `qa_feedback`. It then stays off.
  apply({ qa_feedback: false, ...config });
}
