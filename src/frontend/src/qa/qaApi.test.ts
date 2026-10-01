import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  HIDDEN_KEY,
  KEY_STORAGE_KEY,
  OUTBOX_KEY,
  QA_NOTES_URL,
  QaHttpError,
  applyQaParam,
  buildContext,
  clampNote,
  clearKey,
  enqueue,
  readSession,
  redactUrl,
  writeSession,
  SESSION_STORAGE_KEY,
  fetchState,
  flushOutbox,
  normaliseState,
  readKey,
  readOutbox,
  submitNote,
  takeJoinHash,
  writeKey,
  type QaNoteInput,
} from './qaApi';

// jsdom has no Response constructor — answer with the members the module reads.
const jsonResponse = (body: unknown, status = 200): Response =>
  ({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) }) as Response;

const KEY = 'join-key-1';

const JOINED = {
  joined: true,
  session_id: 's-1',
  current: 'A-03',
  index: 2,
  count: 108,
  card: { id: 'A-03', block: 'A', block_title: 'Betreiber', title: 'Anmelden', do: null, expect: null, needs: null, role: 'operator', device: 'desktop' },
  blocks: [{ id: 'A', title: 'Betreiber', first: 'A-01', count: 10 }],
  hidden: false,
  message: null,
  session: '2026-10-31',
  updated_at: '2026-10-31T12:00:00Z',
};

function note(overrides: Partial<QaNoteInput> = {}): QaNoteInput {
  return {
    client_id: 'c-1',
    session_id: 's-1',
    kind: 'note',
    step: 'A-01',
    target: null,
    text: 'Der Knopf ist zu klein',
    route: '/login',
    selector: null,
    element_text: null,
    answers_message: null,
    context: { role: null, org: null, lang: 'de', viewport: '390x844', user_agent: 'test' },
    trail: [],
    ...overrides,
  };
}

const stored = (id = 'n-1', extra: Record<string, unknown> = {}) =>
  jsonResponse({ id, seq: 1, created_at: '2026-10-31T12:00:00Z', duplicate: false, moved: false, state: JOINED, ...extra }, 201);

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal('fetch', vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the join key', () => {
  it('takes the key out of the fragment and keeps the rest of it', () => {
    expect(takeJoinHash('#qa-join=abc')).toEqual({ key: 'abc', rest: '' });
    expect(takeJoinHash('#tab=2&qa-join=abc')).toEqual({ key: 'abc', rest: '#tab=2' });
    expect(takeJoinHash('#top')).toEqual({ key: null, rest: '#top' });
    expect(takeJoinHash('')).toEqual({ key: null, rest: '' });
    expect(takeJoinHash('#qa-join=')).toEqual({ key: null, rest: '' });
  });

  it('stores, reads and clears the key together with the session', () => {
    expect(readKey()).toBeNull();
    writeKey(KEY);
    writeSession('s-1');
    expect(localStorage.getItem(KEY_STORAGE_KEY)).toBe(KEY);
    expect(localStorage.getItem(SESSION_STORAGE_KEY)).toBe('s-1');
    expect(readKey()).toBe(KEY);
    clearKey();
    expect(readKey()).toBeNull();
    expect(readSession()).toBeNull();
  });
});

describe('redactUrl', () => {
  it('replaces a token-like path segment', () => {
    expect(redactUrl('/api/invitations/Ab3dEf6hIj9kLm2nOp5qRs8tUv')).toBe('/api/invitations/:redacted');
  });

  it('replaces the value of a secret parameter and keeps the others', () => {
    expect(redactUrl('/reset-password?token=abc&lang=de')).toBe('/reset-password?token=:redacted&lang=de');
    expect(redactUrl('/x?Code=1&page=2&qa-join=k&PASSWORD=p')).toBe('/x?Code=:redacted&page=2&qa-join=:redacted&PASSWORD=:redacted');
  });

  it('drops the fragment', () => {
    expect(redactUrl('/accept-invite?invite=x#qa-join=y')).toBe('/accept-invite?invite=:redacted');
    expect(redactUrl('/join#qa-join=y')).toBe('/join');
  });

  it('keeps lowercase slugs and UUIDs readable', () => {
    expect(redactUrl('/stations/clavis-demo-augsburger-altstadt-2026')).toBe('/stations/clavis-demo-augsburger-altstadt-2026');
    expect(redactUrl('/runs/0198c3a2-7b4e-7f00-9c1a-2b3c4d5e6f70/participant')).toBe(
      '/runs/0198c3a2-7b4e-7f00-9c1a-2b3c4d5e6f70/participant',
    );
  });

  it('handles an absolute address', () => {
    expect(redactUrl('https://stand.example/reset/Ab3dEf6hIj9kLm2nOp5qRs8tUv?token=1#x')).toBe(
      'https://stand.example/reset/:redacted?token=:redacted',
    );
  });
});

describe('applyQaParam', () => {
  it('stores off and on, and ignores everything else', () => {
    expect(applyQaParam('?qa=off')).toBe(true);
    expect(localStorage.getItem(HIDDEN_KEY)).toBe('1');
    expect(applyQaParam('?x=1')).toBeNull();
    expect(applyQaParam('?qa=maybe')).toBeNull();
    expect(localStorage.getItem(HIDDEN_KEY)).toBe('1');
    expect(applyQaParam('?qa=on')).toBe(false);
    expect(localStorage.getItem(HIDDEN_KEY)).toBeNull();
  });
});

describe('fetchState', () => {
  it('sends the key, never credentials, and normalises the joined state', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ ...JOINED, hidden: 'yes', blocks: [{ id: 'B' }, ...JOINED.blocks] }));
    const state = await fetchState(KEY);
    expect(state).toMatchObject({ session_id: 's-1', current: 'A-03', index: 2, count: 108, hidden: false, message: null });
    expect(state?.card).toMatchObject({ id: 'A-03', role: 'operator', needs: null });
    expect(state?.blocks).toEqual([{ id: 'A', title: 'Betreiber', first: 'A-01', count: 10 }]);
    expect(fetch).toHaveBeenCalledWith(
      '/api/qa/state',
      expect.objectContaining({ credentials: 'omit', headers: { 'X-QA-Key': KEY } }),
    );
  });

  it('answers null when the browser is not joined', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ joined: false }));
    expect(await fetchState(KEY)).toBeNull();
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ detail: 'no' }, 401));
    expect(await fetchState(KEY)).toBeNull();
    expect(normaliseState({ joined: true })).toMatchObject({ current: null, card: null, blocks: [] });
  });

  it('throws the status of a refused answer', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: 'Not Found' }, 404));
    await expect(fetchState(KEY)).rejects.toEqual(new QaHttpError(404));
  });
});

describe('submitNote', () => {
  it('posts JSON with the key and without credentials, and answers the state', async () => {
    vi.mocked(fetch).mockResolvedValue(stored('n-1', { moved: true }));
    const result = await submitNote(note(), KEY);
    expect(result).toEqual({
      status: 'stored',
      note: expect.objectContaining({ id: 'n-1', moved: true, state: expect.objectContaining({ current: 'A-03' }) }),
    });
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe(QA_NOTES_URL);
    expect(init).toMatchObject({
      method: 'POST',
      credentials: 'omit',
      headers: { 'Content-Type': 'application/json', 'X-QA-Key': KEY },
    });
    expect(JSON.parse(init!.body as string)).toMatchObject({ kind: 'note', step: 'A-01', client_id: 'c-1', trail: [] });
    expect(readOutbox()).toEqual([]);
  });

  it('answers ended on 409 and queues nothing', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: 'session ended' }, 409));
    expect(await submitNote(note(), KEY)).toEqual({ status: 'ended' });
    expect(readOutbox()).toEqual([]);
  });

  it('redacts the route before it leaves the browser', async () => {
    vi.mocked(fetch).mockResolvedValue(stored());
    await submitNote(note({ route: '/reset-password?token=abc#qa-join=k' }), KEY);
    const body = JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string) as { route: string };
    expect(body.route).toBe('/reset-password?token=:redacted');
  });

  it('answers unjoined on 401 and queues nothing', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: 'invalid key' }, 401));
    expect(await submitNote(note(), KEY)).toEqual({ status: 'unjoined' });
    expect(readOutbox()).toEqual([]);
  });

  it('queues a note when the network fails, and on 429 and 5xx, with its original step', async () => {
    vi.mocked(fetch)
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(jsonResponse({}, 429))
      .mockResolvedValueOnce(jsonResponse({}, 502));
    expect(await submitNote(note({ client_id: 'a', kind: 'ok', step: 'A-03' }), KEY)).toEqual({ status: 'queued' });
    expect(await submitNote(note({ client_id: 'b', kind: 'ok', step: 'A-03' }), KEY)).toEqual({ status: 'queued' });
    expect(await submitNote(note({ client_id: 'c' }), KEY)).toEqual({ status: 'queued' });
    expect(readOutbox().map((n) => [n.client_id, n.step])).toEqual([
      ['a', 'A-03'],
      ['b', 'A-03'],
      ['c', 'A-01'],
    ]);
  });

  it('does not queue a note the API refuses for good', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: 'invalid' }, 422));
    expect(await submitNote(note(), KEY)).toEqual({ status: 'failed', httpStatus: 422 });
    expect(readOutbox()).toEqual([]);
  });

  it('cuts every field to the API limits', () => {
    const trail = Array.from({ length: 40 }, (_, i) => ({ t: 'click' as const, sel: `b${i}`, at: 'x' }));
    const long = clampNote(
      note({
        text: 'x'.repeat(5000),
        element_text: 'y'.repeat(400),
        route: '/' + 'z'.repeat(600),
        target: 'T'.repeat(40),
        answers_message: 'm'.repeat(2500),
        trail,
      }),
    );
    expect(long.text).toHaveLength(4000);
    expect(long.element_text).toHaveLength(300);
    expect(long.route).toHaveLength(500);
    expect(long.target).toHaveLength(32);
    expect(long.answers_message).toHaveLength(2000);
    expect(long.trail).toHaveLength(30);
    expect(long.trail[0].sel).toBe('b10');
  });
});

describe('flushOutbox', () => {
  it('sends in order with the given key, stops at a retryable failure and keeps the rest', async () => {
    enqueue(note({ client_id: 'a' }));
    enqueue(note({ client_id: 'b' }));
    vi.mocked(fetch).mockResolvedValueOnce(stored('n-a')).mockResolvedValueOnce(jsonResponse({}, 503));
    expect(await flushOutbox(KEY)).toBe(1);
    expect(readOutbox().map((n) => n.client_id)).toEqual(['b']);
    expect(vi.mocked(fetch).mock.calls[0][1]).toMatchObject({ headers: { 'X-QA-Key': KEY } });
  });

  it('drops a note the API refuses for good, also an ended key or session, silently', async () => {
    enqueue(note({ client_id: 'a' }));
    enqueue(note({ client_id: 'b' }));
    enqueue(note({ client_id: 'c' }));
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse({}, 422))
      .mockResolvedValueOnce(jsonResponse({}, 401))
      .mockResolvedValueOnce(jsonResponse({}, 409));
    expect(await flushOutbox(KEY)).toBe(0);
    expect(localStorage.getItem(OUTBOX_KEY)).toBeNull();
  });

  it('sends a queued note with the session it was made in, not the current one', async () => {
    enqueue(note({ client_id: 'a', session_id: 's-1' }));
    writeSession('s-2');
    vi.mocked(fetch).mockResolvedValue(stored());
    await flushOutbox(KEY);
    const body = JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string) as { session_id: string };
    expect(body.session_id).toBe('s-1');
  });

  it('sends nothing without a key and keeps the outbox', async () => {
    enqueue(note({ client_id: 'a' }));
    expect(await flushOutbox(null)).toBe(1);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('runs once for concurrent calls, so no note goes out twice', async () => {
    enqueue(note({ client_id: 'a' }));
    vi.mocked(fetch).mockResolvedValue(stored());
    await Promise.all([flushOutbox(KEY), flushOutbox(KEY), flushOutbox(KEY)]);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('keeps at most 200 notes, the newest', () => {
    for (let i = 0; i < 205; i += 1) enqueue(note({ client_id: `n${i}` }));
    const items = readOutbox();
    expect(items).toHaveLength(200);
    expect(items[0].client_id).toBe('n5');
    expect(items[199].client_id).toBe('n204');
  });

  it('still sends a note queued after an empty flush', async () => {
    expect(await flushOutbox(KEY)).toBe(0);
    enqueue(note({ client_id: 'late' }));
    vi.mocked(fetch).mockResolvedValue(stored());
    expect(await flushOutbox(KEY)).toBe(0);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

describe('buildContext', () => {
  it('carries role, organization, language, viewport and user agent', () => {
    const context = buildContext('coordinator', 'stand-augsburg');
    expect(context).toMatchObject({ role: 'coordinator', org: 'stand-augsburg', lang: 'de' });
    expect(context.viewport).toMatch(/^\d+x\d+$/);
    expect(context.user_agent?.length).toBeLessThanOrEqual(200);
  });
});
