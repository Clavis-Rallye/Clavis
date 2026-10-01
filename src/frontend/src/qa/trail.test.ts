import { describe, it, expect, afterEach } from 'vitest';
import { AxiosError, type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios';
import apiClient from '../api/client';
import { ApiError } from '../api/client';
import { TRAIL_MAX, TrailRecorder, redactMessage, redactText } from './trail';

const cleanups: Array<() => void> = [];

function startRecorder(): { recorder: TrailRecorder; bar: HTMLElement } {
  const bar = document.createElement('div');
  document.body.appendChild(bar);
  const recorder = new TrailRecorder();
  cleanups.push(recorder.start(bar));
  return { recorder, bar };
}

afterEach(() => {
  while (cleanups.length) cleanups.pop()!();
  document.body.innerHTML = '';
  apiClient.defaults.adapter = undefined;
});

/** An adapter that answers like the server would, without a network. */
function fakeAdapter(status: number): AxiosAdapter {
  return (config: InternalAxiosRequestConfig) => {
    const response = { data: { detail: 'nope' }, status, statusText: '', headers: {}, config };
    if (status < 400) return Promise.resolve(response);
    return Promise.reject(new AxiosError('failed', 'ERR_BAD_RESPONSE', config, null, response));
  };
}

describe('TrailRecorder', () => {
  it('records clicks with selector and text, but never inside the bar', () => {
    const { recorder, bar } = startRecorder();
    const save = document.createElement('button');
    save.setAttribute('data-testid', 'product-save');
    save.textContent = 'Speichern';
    document.body.appendChild(save);
    const own = document.createElement('button');
    own.textContent = 'OK';
    bar.appendChild(own);

    save.click();
    own.click();

    const entries = recorder.snapshot();
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({ t: 'click', sel: 'html>body>button[data-testid=product-save]', txt: 'Speichern' });
    expect(entries[0].at).toMatch(/^\d{4}-\d\d-\d\dT/);
  });

  function clickOn(html: string, selector: string, recorder: TrailRecorder) {
    const host = document.createElement('div');
    host.innerHTML = html;
    document.body.appendChild(host);
    (host.querySelector(selector) as HTMLElement).click();
    return recorder.snapshot().at(-1)!;
  }

  it('records a form field by its selector only, never a text or a value', () => {
    const { recorder } = startRecorder();
    const entry = clickOn('<input aria-label="E-Mail" value="geheim@example.com">', 'input', recorder);
    expect(entry).toMatchObject({ t: 'click', sel: expect.stringContaining('input') });
    expect(entry).not.toHaveProperty('txt');
    expect(JSON.stringify(recorder.snapshot())).not.toContain('geheim');
  });

  it('records no text from a click into editable content', () => {
    const { recorder } = startRecorder();
    const entry = clickOn(
      '<div contenteditable="true"><p><button type="button">Antwort des Teams Blau</button></p></div>',
      'button',
      recorder,
    );
    expect(entry.t).toBe('click');
    expect(entry).not.toHaveProperty('txt');
  });

  it('records no text from content that is not a control', () => {
    const { recorder } = startRecorder();
    const entry = clickOn('<p><span>Team Blau hat 40 Punkte</span></p>', 'span', recorder);
    expect(entry).not.toHaveProperty('txt');
  });

  it('records no text from a share-code chip, and redacts a code on a button', () => {
    const { recorder } = startRecorder();
    expect(clickOn('<div class="MuiChip-root"><span class="MuiChip-label">PP4Y8</span></div>', 'span', recorder)).not.toHaveProperty(
      'txt',
    );
    expect(clickOn('<button type="button">PP4Y8</button>', 'button', recorder)).toMatchObject({ txt: ':redacted' });
  });

  it('takes the name of a control from its aria-label, and redacts a token in it', () => {
    const { recorder } = startRecorder();
    expect(clickOn('<a href="#" aria-label="Station oeffnen">x</a>', 'a', recorder)).toMatchObject({ txt: 'Station oeffnen' });
    expect(clickOn('<button type="button">Link Ab3dEf6hIj9kLm2nOp5qRs8tUv kopieren</button>', 'button', recorder)).toMatchObject({
      txt: 'Link :redacted kopieren',
    });
  });

  it('records routes redacted', () => {
    const { recorder } = startRecorder();
    recorder.route('/reset-password', '?token=abc&lang=de');
    recorder.route('/api/invitations/Ab3dEf6hIj9kLm2nOp5qRs8tUv', '');
    expect(recorder.snapshot().map((e) => e.to)).toEqual([
      '/reset-password?token=:redacted&lang=de',
      '/api/invitations/:redacted',
    ]);
  });

  it('records a failed request by its redacted path', async () => {
    const { recorder } = startRecorder();
    apiClient.defaults.adapter = fakeAdapter(404);
    await apiClient.get('/api/invitations/Ab3dEf6hIj9kLm2nOp5qRs8tUv?token=x').catch(() => undefined);
    expect(recorder.snapshot()).toEqual([
      expect.objectContaining({ t: 'http', path: '/api/invitations/:redacted', status: 404 }),
    ]);
  });

  it('redacts addresses and tokens in an error message', () => {
    const { recorder } = startRecorder();
    window.dispatchEvent(
      new ErrorEvent('error', { message: 'GET /api/invitations/Ab3dEf6hIj9kLm2nOp5qRs8tUv?token=x failed with 404' }),
    );
    expect(recorder.snapshot()[0].msg).toBe('GET /api/invitations/:redacted?token=:redacted failed with 404');
  });

  it('records JavaScript errors and unhandled rejections', () => {
    const { recorder } = startRecorder();
    window.dispatchEvent(new ErrorEvent('error', { message: 'x is undefined' }));
    const rejection = new Event('unhandledrejection') as PromiseRejectionEvent;
    Object.defineProperty(rejection, 'reason', { value: new Error('boom') });
    window.dispatchEvent(rejection);
    expect(recorder.snapshot().map((e) => [e.t, e.msg])).toEqual([
      ['error', 'x is undefined'],
      ['error', 'unhandled: boom'],
    ]);
  });

  it('records a failed apiClient request and hands the error on unchanged', async () => {
    const { recorder } = startRecorder();
    apiClient.defaults.adapter = fakeAdapter(500);
    const error = await apiClient.get('/api/orgs/demo/runs?page=2').catch((e: unknown) => e);
    // The App's own interceptor still turns it into its ApiError.
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(500);
    expect(recorder.snapshot()).toEqual([
      expect.objectContaining({ t: 'http', method: 'GET', path: '/api/orgs/demo/runs', status: 500 }),
    ]);

    apiClient.defaults.adapter = fakeAdapter(200);
    const ok = await apiClient.get('/api/me');
    expect(ok.status).toBe(200);
    expect(recorder.snapshot()).toHaveLength(1);
  });

  it('keeps the newest 30 entries and clears on demand', () => {
    const { recorder } = startRecorder();
    for (let i = 0; i < TRAIL_MAX + 5; i += 1) recorder.route(`/p${i}`, '');
    const entries = recorder.snapshot();
    expect(entries).toHaveLength(TRAIL_MAX);
    expect(entries[0].to).toBe('/p5');
    recorder.clear();
    expect(recorder.snapshot()).toEqual([]);
  });

  it('stops listening and ejects its interceptor', async () => {
    const handlers = (apiClient.interceptors.request as unknown as { handlers: unknown[] }).handlers;
    const live = () => handlers.filter((h) => h !== null).length;
    const before = live();
    const bar = document.createElement('div');
    const recorder = new TrailRecorder();
    const stop = recorder.start(bar);
    expect(live()).toBe(before + 1);
    stop();
    document.body.click();
    recorder.route('/x', '');
    apiClient.defaults.adapter = fakeAdapter(500);
    await apiClient.get('/api/me').catch(() => undefined);
    expect(recorder.snapshot()).toEqual([]);
    expect(live()).toBe(before);
  });
});

describe('redactText and redactMessage', () => {
  it('replaces a whole code and token-like words, and keeps plain words', () => {
    expect(redactText('PP4Y8')).toBe(':redacted');
    expect(redactText('AB12CD34EF')).toBe(':redacted');
    expect(redactText('OK')).toBe('OK');
    expect(redactText('Speichern')).toBe('Speichern');
    expect(redactText('clavis-demo-augsburger-altstadt-2026')).toBe('clavis-demo-augsburger-altstadt-2026');
  });

  it('redacts every address in a message', () => {
    expect(redactMessage('fetch https://stand.example/reset?code=1#qa-join=k and /join?invite=abc, 3/4 done')).toBe(
      'fetch https://stand.example/reset?code=:redacted and /join?invite=:redacted, 3/4 done',
    );
  });
});

describe('redactText inside a name', () => {
  it('redacts a share code inside a copy button label and keeps the words', () => {
    expect(redactText('PP4Y8 — Code kopieren')).toBe(':redacted — Code kopieren');
    expect(redactText('Beitreten mit 6C5BNK')).toBe('Beitreten mit :redacted');
    expect(redactText('Speichern')).toBe('Speichern');
  });
});

describe('redactMessage and codes', () => {
  it('redacts a join code inside an error message', () => {
    expect(redactMessage('Join failed for code 6C5BNK')).toBe('Join failed for code :redacted');
  });
});
