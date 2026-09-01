import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  DEFAULT_INSTANCE_CONFIG,
  __resetInstanceConfigForTests,
  getInstanceConfig,
  loadInstanceConfig,
} from './instanceConfig';
import i18n from '../i18n';

// Eager glob of every locale file. The grep test walks the same
// trees the bundler ships, so a new namespace with the literal cannot slip in.
const localeFiles = import.meta.glob('../i18n/locales/*/*.json', {
  eager: true,
}) as Record<string, { default: unknown }>;

function stringValues(node: unknown, path: string, out: string[]): void {
  if (typeof node === 'string') {
    out.push(`${path}: ${node}`);
    return;
  }
  if (Array.isArray(node)) {
    node.forEach((item, i) => stringValues(item, `${path}[${i}]`, out));
    return;
  }
  if (node && typeof node === 'object') {
    for (const [key, value] of Object.entries(node)) {
      stringValues(value, `${path}.${key}`, out);
    }
  }
}

// jsdom has no Response constructor — answer with the two members the
// loader reads (`ok`, `json`).
const jsonResponse = (body: unknown, status = 200): Response =>
  ({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) }) as Response;

describe('instanceConfig', () => {
  beforeEach(() => {
    __resetInstanceConfigForTests();
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('fetches /api/public/config once and caches the answer', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({
        brand_name: 'Stadtrallye Musterstadt',
        bootstrap_open: true,
        single_org_slug: 'musterstadt',
      }),
    );

    const first = await loadInstanceConfig();
    const second = await loadInstanceConfig();

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith(
      '/api/public/config',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(first).toEqual(second);
    expect(getInstanceConfig()).toEqual({
      brand_name: 'Stadtrallye Musterstadt',
      bootstrap_open: true,
      single_org_slug: 'musterstadt',
      qa_feedback: false,
    });
  });

  it('switches the feedback module on only for an explicit true', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ brand_name: 'Clavis', bootstrap_open: false, single_org_slug: null, qa_feedback: true }),
    );
    expect((await loadInstanceConfig()).qa_feedback).toBe(true);

    __resetInstanceConfigForTests();
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ brand_name: 'Clavis', bootstrap_open: false, single_org_slug: null, qa_feedback: 'yes' }),
    );
    expect((await loadInstanceConfig()).qa_feedback).toBe(false);
  });

  it('sets the document title and the i18n brand variable to the configured brand', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({
        brand_name: 'Stadtrallye Musterstadt',
        bootstrap_open: false,
        single_org_slug: null,
      }),
    );

    await loadInstanceConfig();

    expect(document.title).toBe('Stadtrallye Musterstadt');
    expect(i18n.options.interpolation?.defaultVariables).toMatchObject({
      brand: 'Stadtrallye Musterstadt',
    });
    // The interpolation reaches real translations. Headline keys keep their
    // <brand> markup and interpolate the configured value.
    expect(i18n.t('welcome:headline')).toContain('Stadtrallye Musterstadt');
  });

  it('keeps the defaults when the endpoint answers non-200', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: 'slow down' }, 503));

    const config = await loadInstanceConfig();

    expect(config).toEqual(DEFAULT_INSTANCE_CONFIG);
    expect(document.title).toBe(DEFAULT_INSTANCE_CONFIG.brand_name);
  });

  it('keeps the defaults when the request fails (offline / timeout)', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('Failed to fetch'));

    const config = await loadInstanceConfig();

    expect(config).toEqual(DEFAULT_INSTANCE_CONFIG);
    expect(config.bootstrap_open).toBe(false);
    expect(config.single_org_slug).toBeNull();
  });

  it('falls back field-wise on a malformed payload', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ brand_name: '   ' }));

    const config = await loadInstanceConfig();

    expect(config).toEqual(DEFAULT_INSTANCE_CONFIG);
  });
});

describe('locale files carry no literal brand', () => {
  it('no string in either locale tree contains "Stadtrallye"', () => {
    const offenders: string[] = [];
    for (const [file, module] of Object.entries(localeFiles)) {
      // The legal templates are operator content, not UI strings. Their
      // imprint names the kind of software ("Software für Stadtrallyes") as a
      // common noun, and that is no brand of an installation.
      if (file.endsWith('/legalTemplates.json')) continue;
      const values: string[] = [];
      stringValues(module.default, file, values);
      offenders.push(...values.filter((value) => value.includes('Stadtrallye')));
    }
    expect(offenders).toEqual([]);
  });
});
