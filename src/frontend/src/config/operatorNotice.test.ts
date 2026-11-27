import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  __resetOperatorNoticeForTests,
  getOperatorNotice,
  loadOperatorNotice,
  parseOperatorNotice,
  pickLocalized,
  subscribeOperatorNotice,
} from './operatorNotice';

// The example of contract version 1. Each case below breaks one rule of a
// fresh copy, so a `null` result points at that rule alone.
function example(): Record<string, unknown> {
  return {
    version: 1,
    logos: [
      {
        src: 'programme.svg',
        src_dark: 'programme-negative.svg',
        alt: { de: 'Logo des Programms', en: 'Logo of the programme' },
        height: 48,
      },
    ],
    text: { de: 'Dieses Angebot wird gefoerdert.', en: 'This service is funded.' },
    text_lang: 'de',
    links: [{ label: 'example.org/programme', url: 'https://example.org/programme' }],
  };
}

type Logo = Record<string, unknown>;
const firstLogo = (file: Record<string, unknown>): Logo => (file.logos as Logo[])[0];
const firstLink = (file: Record<string, unknown>): Logo => (file.links as Logo[])[0];

describe('parseOperatorNotice', () => {
  it('accepts the example of the contract and gives the typed notice', () => {
    expect(parseOperatorNotice(example())).toEqual({
      version: 1,
      logos: [
        {
          src: 'programme.svg',
          src_dark: 'programme-negative.svg',
          alt: { de: 'Logo des Programms', en: 'Logo of the programme' },
          height: 48,
        },
      ],
      text: { de: 'Dieses Angebot wird gefoerdert.', en: 'This service is funded.' },
      text_lang: 'de',
      links: [{ label: 'example.org/programme', url: 'https://example.org/programme' }],
    });
  });

  it('accepts a minimal file: no logos, no links, no text_lang', () => {
    expect(parseOperatorNotice({ version: 1, logos: [], text: { en: 'Funded.' } })).toEqual({
      version: 1,
      logos: [],
      text: { en: 'Funded.' },
      links: [],
    });
  });

  it('ignores unknown fields and copies only the known ones', () => {
    const file = example();
    file.colour = 'red';
    firstLogo(file).onclick = 'alert(1)';
    firstLink(file).target = '_self';
    const notice = parseOperatorNotice(file);
    expect(notice).not.toBeNull();
    expect(notice).not.toHaveProperty('colour');
    expect(notice!.logos[0]).not.toHaveProperty('onclick');
    expect(notice!.links[0]).not.toHaveProperty('target');
    // A new object: a later change to the input does not reach the notice.
    firstLogo(file).src = 'other.svg';
    expect(notice!.logos[0].src).toBe('programme.svg');
  });

  it('accepts every allowed image ending, without regard to case', () => {
    for (const src of ['a.svg', 'A.PNG', 'b.jpg', 'c.JPEG', 'd.webp', 'logo_2-x.v1.Svg']) {
      const file = example();
      firstLogo(file).src = src;
      expect(parseOperatorNotice(file), src).not.toBeNull();
    }
  });

  it('accepts an http link and the bounds of the ranges', () => {
    const file = example();
    firstLink(file).url = 'http://example.org/';
    firstLogo(file).height = 16;
    file.logos = Array.from({ length: 8 }, () => ({ ...firstLogo(file), height: 200 }));
    file.links = Array.from({ length: 8 }, () => firstLink(file));
    file.text = { en: 'x'.repeat(2000) };
    file.text_lang = 'en';
    expect(parseOperatorNotice(file)).not.toBeNull();
  });

  it('accepts region subtags in language codes', () => {
    const file = example();
    file.text = { 'de-AT': 'Gefoerdert.', 'zh-Hant-TW': 'x' };
    file.text_lang = 'de-AT';
    expect(parseOperatorNotice(file)).not.toBeNull();
  });

  const broken: Array<[string, (file: Record<string, unknown>) => unknown]> = [
    ['a wrong version', (f) => { f.version = 2; }],
    ['a version as a string', (f) => { f.version = '1'; }],
    ['src with a slash', (f) => { firstLogo(f).src = 'logos/programme.svg'; }],
    ['src with a backslash', (f) => { firstLogo(f).src = 'logos\\programme.svg'; }],
    ['src with a scheme', (f) => { firstLogo(f).src = 'https://example.org/programme.svg'; }],
    ['src with a data scheme', (f) => { firstLogo(f).src = 'data:image/svg+xml,programme.svg'; }],
    ['src that starts with a dot', (f) => { firstLogo(f).src = '.programme.svg'; }],
    ['src with ..', (f) => { firstLogo(f).src = 'programme..svg'; }],
    ['src with a wrong ending', (f) => { firstLogo(f).src = 'programme.gif'; }],
    ['src missing', (f) => { delete firstLogo(f).src; }],
    ['src_dark with a slash', (f) => { firstLogo(f).src_dark = 'dark/programme.svg'; }],
    ['src_dark as null', (f) => { firstLogo(f).src_dark = null; }],
    ['a height that is no integer', (f) => { firstLogo(f).height = 48.5; }],
    ['a height as a string', (f) => { firstLogo(f).height = '48'; }],
    ['a height below 16', (f) => { firstLogo(f).height = 15; }],
    ['a height above 200', (f) => { firstLogo(f).height = 201; }],
    ['a height missing', (f) => { delete firstLogo(f).height; }],
    ['an empty alt', (f) => { firstLogo(f).alt = { de: '', en: '  ' }; }],
    ['an alt with no entry', (f) => { firstLogo(f).alt = {}; }],
    ['an alt as a string', (f) => { firstLogo(f).alt = 'Logo'; }],
    ['an alt with a value that is no string', (f) => { firstLogo(f).alt = { de: 'Logo', en: 1 }; }],
    ['a logo that is no object', (f) => { f.logos = ['programme.svg']; }],
    ['more than 8 logos', (f) => { f.logos = Array.from({ length: 9 }, () => firstLogo(f)); }],
    ['logos as an object', (f) => { f.logos = firstLogo(f); }],
    ['logos missing', (f) => { delete f.logos; }],
    ['a text that is a string', (f) => { f.text = 'This service is funded.'; }],
    ['a text with only empty entries', (f) => { f.text = { de: '', en: ' ' }; delete f.text_lang; }],
    ['a text above 2000 characters', (f) => { f.text = { en: 'x'.repeat(2001) }; delete f.text_lang; }],
    ['text missing', (f) => { delete f.text; }],
    ['a text_lang without its text', (f) => { f.text_lang = 'fr'; }],
    ['a text_lang whose text is empty', (f) => { f.text = { de: '', en: 'Funded.' }; }],
    ['a text_lang as a number', (f) => { f.text_lang = 1; }],
    ['a language code DE_de as text_lang', (f) => { f.text = { DE_de: 'x' }; f.text_lang = 'DE_de'; }],
    ['a language code DE_de as a key of text', (f) => { f.text = { DE_de: 'x' }; delete f.text_lang; }],
    ['a language code DE_de as a key of alt', (f) => { firstLogo(f).alt = { DE_de: 'Logo' }; }],
    ['a link with the scheme javascript:', (f) => { firstLink(f).url = 'javascript:alert(1)'; }],
    ['a link with the scheme ftp:', (f) => { firstLink(f).url = 'ftp://example.org/'; }],
    ['a link url that is relative', (f) => { firstLink(f).url = '/programme'; }],
    ['a link url missing', (f) => { delete firstLink(f).url; }],
    ['a link label that is empty', (f) => { firstLink(f).label = ''; }],
    ['a link label above 200 characters', (f) => { firstLink(f).label = 'x'.repeat(201); }],
    ['a link label missing', (f) => { delete firstLink(f).label; }],
    ['more than 8 links', (f) => { f.links = Array.from({ length: 9 }, () => firstLink(f)); }],
    ['links as an object', (f) => { f.links = firstLink(f); }],
  ];

  it.each(broken)('refuses %s', (_name, breakRule) => {
    const file = example();
    breakRule(file);
    expect(parseOperatorNotice(file)).toBeNull();
  });

  it.each([
    ['null', null],
    ['an array', [example()]],
    ['a number', 1],
    ['a string', '{"version":1}'],
    ['undefined', undefined],
  ])('refuses %s as the whole file', (_name, value) => {
    expect(parseOperatorNotice(value)).toBeNull();
  });
});

describe('pickLocalized', () => {
  const map = { de: 'Deutsch', en: 'English', fr: 'Français' };

  it('takes the forced language for every interface language', () => {
    expect(pickLocalized(map, 'en', 'de')).toEqual({ lang: 'de', value: 'Deutsch' });
    expect(pickLocalized(map, 'fr', 'de')).toEqual({ lang: 'de', value: 'Deutsch' });
  });

  it('takes the interface language', () => {
    expect(pickLocalized(map, 'fr')).toEqual({ lang: 'fr', value: 'Français' });
    expect(pickLocalized(map, 'en')).toEqual({ lang: 'en', value: 'English' });
  });

  it('takes the base language of a regional interface language', () => {
    expect(pickLocalized(map, 'de-AT')).toEqual({ lang: 'de', value: 'Deutsch' });
  });

  it('prefers an exact regional entry over its base language', () => {
    expect(pickLocalized({ de: 'Deutsch', 'de-AT': 'Österreich' }, 'de-AT')).toEqual({
      lang: 'de-AT',
      value: 'Österreich',
    });
  });

  it('falls back to en, then de, then the first entry with a text', () => {
    expect(pickLocalized(map, 'it')).toEqual({ lang: 'en', value: 'English' });
    expect(pickLocalized({ fr: 'Français', de: 'Deutsch' }, 'it')).toEqual({
      lang: 'de',
      value: 'Deutsch',
    });
    expect(pickLocalized({ fr: '', nl: 'Nederlands' }, 'it')).toEqual({
      lang: 'nl',
      value: 'Nederlands',
    });
  });

  it('skips an empty text of the interface language', () => {
    expect(pickLocalized({ fr: ' ', en: 'English' }, 'fr')).toEqual({ lang: 'en', value: 'English' });
  });

  it('gives null when no entry has a text', () => {
    expect(pickLocalized({ de: '' }, 'de')).toBeNull();
  });
});

// jsdom has no Response constructor. Answer with the members the loader
// reads (`ok`, `status`, `text`).
const textResponse = (body: string, status = 200): Response =>
  ({ ok: status >= 200 && status < 300, status, text: () => Promise.resolve(body) }) as Response;

describe('loadOperatorNotice', () => {
  beforeEach(() => {
    __resetOperatorNoticeForTests();
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('fetches the relative path /operator/notice.json once and keeps the notice', async () => {
    vi.mocked(fetch).mockResolvedValue(textResponse(JSON.stringify(example())));

    await loadOperatorNotice();
    await loadOperatorNotice();

    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith(
      '/operator/notice.json',
      expect.objectContaining({ cache: 'no-cache', signal: expect.any(AbortSignal) }),
    );
    expect(getOperatorNotice()).toEqual(parseOperatorNotice(example()));
  });

  it('gives no notice on a 404', async () => {
    vi.mocked(fetch).mockResolvedValue(textResponse(JSON.stringify(example()), 404));
    await loadOperatorNotice();
    expect(getOperatorNotice()).toBeNull();
  });

  it('reads only a 200 answer', async () => {
    vi.mocked(fetch).mockResolvedValue(textResponse(JSON.stringify(example()), 204));
    await loadOperatorNotice();
    expect(getOperatorNotice()).toBeNull();
  });

  it('gives no notice on a body of HTML, such as the index.html of a dev server', async () => {
    vi.mocked(fetch).mockResolvedValue(
      textResponse('<!doctype html><html><body><div id="root"></div></body></html>'),
    );
    await expect(loadOperatorNotice()).resolves.toBeUndefined();
    expect(getOperatorNotice()).toBeNull();
  });

  it('gives no notice on an invalid file', async () => {
    vi.mocked(fetch).mockResolvedValue(textResponse(JSON.stringify({ ...example(), version: 2 })));
    await loadOperatorNotice();
    expect(getOperatorNotice()).toBeNull();
  });

  it('refuses a body above 65536 characters', async () => {
    const file = example();
    file.padding = ' '.repeat(65536);
    vi.mocked(fetch).mockResolvedValue(textResponse(JSON.stringify(file)));
    await loadOperatorNotice();
    expect(getOperatorNotice()).toBeNull();
  });

  it('gives no notice on a rejected fetch', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(loadOperatorNotice()).resolves.toBeUndefined();
    expect(getOperatorNotice()).toBeNull();
  });

  it('gives no notice when reading the body fails', async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      text: () => Promise.reject(new TypeError('network error')),
    } as Response);
    await expect(loadOperatorNotice()).resolves.toBeUndefined();
    expect(getOperatorNotice()).toBeNull();
  });

  it('ends after 5 seconds when the fetch never answers', async () => {
    vi.useFakeTimers();
    let signal: AbortSignal | undefined;
    // A fetch that ignores its signal: the load must end on its own.
    vi.mocked(fetch).mockImplementation((_input, init) => {
      signal = init?.signal ?? undefined;
      return new Promise<Response>(() => {});
    });

    let settled = false;
    const load = loadOperatorNotice().then(() => {
      settled = true;
    });

    await vi.advanceTimersByTimeAsync(4999);
    expect(settled).toBe(false);
    expect(signal?.aborted).toBe(false);

    await vi.advanceTimersByTimeAsync(1);
    await load;
    expect(settled).toBe(true);
    expect(signal?.aborted).toBe(true);
    expect(getOperatorNotice()).toBeNull();
  });

  it('calls a subscriber when the notice arrives', async () => {
    let answer: (response: Response) => void = () => {};
    vi.mocked(fetch).mockReturnValue(
      new Promise<Response>((resolve) => {
        answer = resolve;
      }),
    );
    const listener = vi.fn();
    const unsubscribe = subscribeOperatorNotice(listener);

    const load = loadOperatorNotice();
    expect(listener).not.toHaveBeenCalled();
    answer(textResponse(JSON.stringify(example())));
    await load;

    expect(listener).toHaveBeenCalledTimes(1);
    expect(getOperatorNotice()?.logos[0].src).toBe('programme.svg');

    unsubscribe();
    __resetOperatorNoticeForTests();
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('does not call a subscriber when no notice arrives', async () => {
    vi.mocked(fetch).mockResolvedValue(textResponse('', 404));
    const listener = vi.fn();
    const unsubscribe = subscribeOperatorNotice(listener);
    await loadOperatorNotice();
    expect(listener).not.toHaveBeenCalled();
    unsubscribe();
  });

  it('lets a test set a notice through the reset', () => {
    const notice = parseOperatorNotice(example());
    __resetOperatorNoticeForTests(notice);
    expect(getOperatorNotice()).toBe(notice);
    __resetOperatorNoticeForTests();
    expect(getOperatorNotice()).toBeNull();
  });
});
