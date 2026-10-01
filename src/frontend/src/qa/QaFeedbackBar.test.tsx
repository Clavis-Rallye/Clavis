import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReactNode } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useLocation, useNavigate } from 'react-router';
import Dialog from '@mui/material/Dialog';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import QaFeedbackBar, { BOTTOM_BASE_PX, FAST_POLL_MS, POLL_MS } from './QaFeedbackBar';
import { HIDDEN_KEY, KEY_STORAGE_KEY, OUTBOX_KEY, SESSION_STORAGE_KEY } from './qaApi';

// jsdom has no Response constructor — answer with the members the module reads.
const jsonResponse = (body: unknown, status = 200): Response =>
  ({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) }) as Response;

const KEY = 'k-1';

interface CardShape {
  id: string;
  block: string | null;
  block_title: string | null;
  title: string | null;
  do: string | null;
  expect: string | null;
  needs: string | null;
  role: string | null;
  device: string | null;
}

const card = (id: string, extra: Partial<CardShape> = {}): CardShape => ({
  id,
  block: id.split('-')[0],
  block_title: null,
  title: 'Anmelden',
  do: 'Melde dich als Spielleitungin an.',
  expect: 'Das Dashboard erscheint.',
  needs: null,
  role: null,
  device: null,
  ...extra,
});

/** A joined state as the API answers it. */
const joined = (current: string, index: number, extra: Record<string, unknown> = {}) => ({
  joined: true,
  session_id: 's-1',
  current,
  index,
  count: 108,
  card: card(current),
  blocks: [
    { id: 'A', title: 'Betreiber', first: 'A-01', count: 10 },
    { id: 'E', title: 'Gast am Handy', first: 'E-01', count: 21 },
  ],
  hidden: false,
  message: null,
  session: '2026-10-31',
  updated_at: '2026-10-31T12:00:00Z',
  ...extra,
});

interface Api {
  state: Record<string, unknown>;
  /** Answers to POST /api/qa/notes, in order. The last one repeats. */
  posts: Array<() => Promise<Response>>;
}

let api: Api;

function installFetch(): void {
  let postIndex = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      if (url === '/api/qa/state') return Promise.resolve(jsonResponse(api.state));
      if (url === '/api/qa/notes' && init?.method === 'POST') {
        const answer = api.posts[Math.min(postIndex, api.posts.length - 1)];
        postIndex += 1;
        return answer();
      }
      return Promise.resolve(jsonResponse({ detail: 'Not Found' }, 404));
    }),
  );
}

/** A stored answer. By default the server moved nothing and the state stays as it is. */
const storedAnswer =
  (id = 'n-1', state?: Record<string, unknown>, moved = false) =>
  () =>
    Promise.resolve(
      jsonResponse({ id, seq: 1, created_at: '2026-10-31T12:00:00Z', duplicate: false, moved, state: state ?? api.state }, 201),
    );

function postedBodies(): Array<Record<string, unknown>> {
  return vi
    .mocked(fetch)
    .mock.calls.filter(([url, init]) => url === '/api/qa/notes' && init?.method === 'POST')
    .map(([, init]) => JSON.parse(init!.body as string) as Record<string, unknown>);
}

const stateCalls = () => vi.mocked(fetch).mock.calls.filter(([url]) => url === '/api/qa/state');

function NavigateButton({ to }: { to: string }) {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate(to)}>
      go
    </button>
  );
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search + location.hash}</output>;
}

function renderBar(path = '/login', page?: ReactNode) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      {page}
      <LocationProbe />
      <QaFeedbackBar />
    </MemoryRouter>,
  );
}

const barRoot = () => document.querySelector('[data-qa-bar-root]');

describe('QaFeedbackBar', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem(KEY_STORAGE_KEY, KEY);
    api = { state: joined('A-04', 3), posts: [storedAnswer()] };
    installFetch();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    Object.defineProperty(window, 'visualViewport', { value: undefined, configurable: true });
  });

  // ------------------------------------------------------------ joining ---

  it('sends nothing and renders nothing without a key', async () => {
    localStorage.clear();
    renderBar();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(fetch).not.toHaveBeenCalled();
    expect(barRoot()).toBeNull();
  });

  it('a join link in the fragment stores the key and leaves the address without it', async () => {
    localStorage.clear();
    renderBar('/join?code=X#qa-join=abc');
    expect(await screen.findByTestId('qa-bar-step')).toHaveTextContent('A-04');
    expect(localStorage.getItem(KEY_STORAGE_KEY)).toBe('abc');
    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/join?code=X'));
    expect(stateCalls()[0][1]).toMatchObject({ headers: { 'X-QA-Key': 'abc' }, credentials: 'omit' });
  });

  it('a key in the query string joins nothing', async () => {
    localStorage.clear();
    renderBar('/?qa-join=abc');
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(fetch).not.toHaveBeenCalled();
    expect(localStorage.getItem(KEY_STORAGE_KEY)).toBeNull();
  });

  it('a join link undoes an earlier local hide and forgets an old session', async () => {
    localStorage.setItem(HIDDEN_KEY, '1');
    localStorage.setItem(SESSION_STORAGE_KEY, 's-old');
    api.state = joined('A-04', 3, { session_id: null });
    renderBar('/#qa-join=new-key');
    expect(await screen.findByTestId('qa-bar-step')).toBeInTheDocument();
    expect(localStorage.getItem(HIDDEN_KEY)).toBeNull();
    expect(localStorage.getItem(SESSION_STORAGE_KEY)).toBeNull();
  });

  it('keeps the session the server names, also when it changes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    renderBar();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(localStorage.getItem(SESSION_STORAGE_KEY)).toBe('s-1');
    api.state = joined('A-01', 0, { session_id: 's-2' });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS);
    });
    expect(localStorage.getItem(SESSION_STORAGE_KEY)).toBe('s-2');
    expect(localStorage.getItem(KEY_STORAGE_KEY)).toBe(KEY);
  });

  it('says the session has ended when the server answers 409', async () => {
    const user = userEvent.setup();
    api.posts = [() => Promise.resolve(jsonResponse({ detail: 'session ended' }, 409))];
    renderBar();
    await user.click(await screen.findByTestId('qa-bar-ok'));
    expect(await screen.findByText('Die Sitzung ist beendet')).toBeInTheDocument();
    expect(localStorage.getItem(OUTBOX_KEY)).toBeNull();
    expect(localStorage.getItem(KEY_STORAGE_KEY)).toBe(KEY);
  });

  it('an answer of joined:false drops the key and the bar', async () => {
    api.state = { joined: false };
    renderBar();
    await waitFor(() => expect(localStorage.getItem(KEY_STORAGE_KEY)).toBeNull());
    expect(barRoot()).toBeNull();
  });

  it('a 401 on an event drops the key and the bar', async () => {
    const user = userEvent.setup();
    api.posts = [() => Promise.resolve(jsonResponse({ detail: 'session ended' }, 401))];
    renderBar();
    await user.click(await screen.findByTestId('qa-bar-ok'));
    await waitFor(() => expect(barRoot()).toBeNull());
    expect(localStorage.getItem(KEY_STORAGE_KEY)).toBeNull();
  });

  // --------------------------------------------------------------- card ---

  it('shows the step, its position and title, and the card when expanded', async () => {
    const user = userEvent.setup();
    api.state = joined('A-04', 3, { card: card('A-04', { needs: 'Angemeldet als Betreiber' }) });
    renderBar();
    expect(await screen.findByTestId('qa-bar-step')).toHaveTextContent('A-04');
    expect(screen.getByTestId('qa-bar-position')).toHaveTextContent('4 / 108');
    expect(screen.getByTestId('qa-bar-title')).toHaveTextContent('Anmelden');
    expect(screen.getByRole('region', { name: 'Feedback-Leiste für die Testsitzung' })).toBeInTheDocument();
    expect(screen.queryByTestId('qa-bar-do')).not.toBeInTheDocument();

    await user.click(screen.getByTestId('qa-bar-expand'));
    expect(screen.getByTestId('qa-bar-needs')).toHaveTextContent('Vorher: Angemeldet als Betreiber');
    expect(screen.getByTestId('qa-bar-do')).toHaveTextContent('Melde dich als Spielleitungin an.');
    expect(screen.getByTestId('qa-bar-expect')).toHaveTextContent('Das Dashboard erscheint.');
  });

  it('keeps the line breaks of a card of numbered lines', async () => {
    const user = userEvent.setup();
    api.state = joined('A-04', 3, { card: card('A-04', { do: '1. Oeffne die Seite.\n2. Tippe auf Weiter.' }) });
    renderBar();
    await user.click(await screen.findByTestId('qa-bar-expand'));
    const section = screen.getByTestId('qa-bar-do');
    expect(section.textContent).toContain('1. Oeffne die Seite.\n2. Tippe auf Weiter.');
    expect(getComputedStyle(section).whiteSpace).toBe('pre-line');
  });

  it('a tap on the title expands the card', async () => {
    const user = userEvent.setup();
    renderBar();
    await user.click(await screen.findByTestId('qa-bar-headline'));
    expect(screen.getByTestId('qa-bar-do')).toBeInTheDocument();
  });

  it('addresses in the card are links, another host opens in a new tab', async () => {
    const user = userEvent.setup();
    api.state = joined('A-04', 3, { card: card('A-04', { do: 'Oeffne http://mail.example:8025 und lies die Mail.' }) });
    renderBar();
    await user.click(await screen.findByTestId('qa-bar-expand'));
    const link = within(screen.getByTestId('qa-bar-do')).getByRole('link', { name: 'http://mail.example:8025' });
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  // ---------------------------------------------------------- the move ---

  it('ok sends the shown step and nothing else, with the key and without credentials', async () => {
    const user = userEvent.setup();
    renderBar('/login?from=mail');
    await user.click(await screen.findByTestId('qa-bar-ok'));

    await waitFor(() => expect(postedBodies()).toHaveLength(1));
    const [body] = postedBodies();
    expect(body).toMatchObject({
      kind: 'ok',
      session_id: 's-1',
      step: 'A-04',
      target: null,
      text: null,
      route: '/login?from=mail',
      selector: null,
      element_text: null,
      answers_message: null,
      context: { role: null, org: null, lang: 'de' },
    });
    expect(Array.isArray(body.trail)).toBe(true);
    const postInit = vi.mocked(fetch).mock.calls.find(([url]) => url === '/api/qa/notes')![1];
    expect(postInit).toMatchObject({ credentials: 'omit', headers: { 'X-QA-Key': KEY } });
    expect(await screen.findByTestId('qa-bar-toast')).toHaveTextContent('OK gespeichert');
  });

  it('shows the next card from the answer, without waiting for a poll', async () => {
    const user = userEvent.setup();
    api.posts = [storedAnswer('n-1', joined('A-05', 4, { card: card('A-05', { title: 'Weiter' }) }), true)];
    renderBar();
    await user.click(await screen.findByTestId('qa-bar-ok'));
    await waitFor(() => expect(screen.getByTestId('qa-bar-step')).toHaveTextContent('A-05'));
    expect(screen.getByTestId('qa-bar-title')).toHaveTextContent('Weiter');
    expect(stateCalls()).toHaveLength(1);
  });

  it('shows a spinner on the tapped button and disables the moves while the request runs', async () => {
    let answer: (response: Response) => void = () => undefined;
    api.posts = [() => new Promise<Response>((resolve) => (answer = resolve))];
    renderBar();
    fireEvent.click(await screen.findByTestId('qa-bar-skip'));

    expect(within(screen.getByTestId('qa-bar-skip')).getByTestId('qa-bar-spinner')).toBeInTheDocument();
    for (const id of ['qa-bar-back', 'qa-bar-ok', 'qa-bar-skip']) expect(screen.getByTestId(id)).toBeDisabled();

    // A second tap while the first runs sends nothing.
    fireEvent.click(screen.getByTestId('qa-bar-ok'));
    expect(postedBodies()).toHaveLength(1);

    await act(async () => {
      answer(jsonResponse({ id: 'n-1', seq: 1, created_at: 'x', duplicate: false, moved: true, state: joined('A-05', 4) }, 201));
    });
    await waitFor(() => expect(screen.getByTestId('qa-bar-step')).toHaveTextContent('A-05'));
    expect(screen.queryByTestId('qa-bar-spinner')).not.toBeInTheDocument();
    expect(screen.getByTestId('qa-bar-ok')).not.toBeDisabled();
  });

  it('two taps that both reach the server carry the same step, and the server decides', async () => {
    const user = userEvent.setup();
    api.posts = [() => Promise.reject(new TypeError('Failed to fetch'))];
    renderBar();
    await user.click(await screen.findByTestId('qa-bar-ok'));
    await screen.findByText(/Kein Netz/);
    await user.click(screen.getByTestId('qa-bar-ok'));
    await waitFor(() => expect(JSON.parse(localStorage.getItem(OUTBOX_KEY)!)).toHaveLength(2));
    const queued = JSON.parse(localStorage.getItem(OUTBOX_KEY)!) as Array<{ step: string; client_id: string }>;
    expect(queued.map((n) => n.step)).toEqual(['A-04', 'A-04']);
    expect(queued[0].client_id).not.toBe(queued[1].client_id);
  });

  it('back reports kind back without text or element', async () => {
    const user = userEvent.setup();
    api.posts = [storedAnswer('n-1', joined('A-03', 2), true)];
    renderBar();
    await user.click(await screen.findByTestId('qa-bar-expand'));
    await user.type(screen.getByTestId('qa-bar-text'), 'bleibt im Feld');
    await user.click(screen.getByTestId('qa-bar-back'));

    await waitFor(() => expect(postedBodies()).toHaveLength(1));
    expect(postedBodies()[0]).toMatchObject({ kind: 'back', step: 'A-04', text: null, selector: null });
    await waitFor(() => expect(screen.getByTestId('qa-bar-step')).toHaveTextContent('A-03'));
    expect(screen.getByTestId('qa-bar-text')).toHaveValue('bleibt im Feld');
  });

  it('says the plan is done when ok on the last step moves nothing', async () => {
    const user = userEvent.setup();
    api.state = joined('J-05', 107);
    renderBar();
    await user.click(await screen.findByTestId('qa-bar-ok'));
    expect(await screen.findByText('Ende des Plans erreicht')).toBeInTheDocument();
    expect(screen.getByTestId('qa-bar-step')).toHaveTextContent('J-05');
  });

  it('a tap on a block jumps to its first step', async () => {
    const user = userEvent.setup();
    api.posts = [storedAnswer('n-1', joined('E-01', 66), true)];
    renderBar();
    await user.click(await screen.findByTestId('qa-bar-expand'));
    expect(screen.getByTestId('qa-bar-block-A')).toHaveAttribute('aria-current', 'step');
    await user.click(screen.getByTestId('qa-bar-block-E'));

    await waitFor(() => expect(postedBodies()).toHaveLength(1));
    expect(postedBodies()[0]).toMatchObject({ kind: 'goto', step: 'A-04', target: 'E-01', text: null, selector: null });
    await waitFor(() => expect(screen.getByTestId('qa-bar-step')).toHaveTextContent('E-01'));
    expect(screen.getByTestId('qa-bar-toast')).toHaveTextContent('Weiter bei E-01');
    expect(screen.getByTestId('qa-bar-block-E')).toHaveAttribute('aria-current', 'step');
  });

  /** A fetch whose next GET of the state waits until the test answers it. */
  function deferNextStateGet(): { answer: (body: unknown) => void } {
    const handle: { answer: (body: unknown) => void } = { answer: () => undefined };
    const original = vi.mocked(fetch).getMockImplementation()!;
    let armed = true;
    vi.mocked(fetch).mockImplementation((url, init) => {
      if (armed && url === '/api/qa/state') {
        armed = false;
        return new Promise<Response>((resolve) => {
          handle.answer = (body: unknown) => resolve(jsonResponse(body));
        });
      }
      return original(url, init);
    });
    return handle;
  }

  it('a poll that started before a move cannot put the old card back (R2)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    renderBar();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId('qa-bar-step')).toHaveTextContent('A-04');

    // The next poll starts and hangs.
    const poll = deferNextStateGet();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS);
    });

    // The tester taps ok, the answer moves to A-05.
    api.posts = [storedAnswer('n-1', joined('A-05', 4, { updated_at: '2026-10-31T12:00:05Z' }), true)];
    fireEvent.click(screen.getByTestId('qa-bar-ok'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId('qa-bar-step')).toHaveTextContent('A-05');

    // The hanging poll answers with the old card, even with a later write time.
    await act(async () => {
      poll.answer(joined('A-04', 3, { updated_at: '2026-10-31T12:00:09Z' }));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId('qa-bar-step')).toHaveTextContent('A-05');
  });

  it('never shows a state the server wrote before the one shown (R2)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    api.state = joined('A-05', 4, { updated_at: '2026-10-31T12:00:05Z' });
    renderBar();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    api.state = joined('A-04', 3, { updated_at: '2026-10-31T12:00:01Z' });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS);
    });
    expect(screen.getByTestId('qa-bar-step')).toHaveTextContent('A-05');

    // A new session starts over, whatever its times.
    api.state = joined('A-01', 0, { session_id: 's-2', updated_at: '2026-10-31T11:00:00Z' });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS);
    });
    expect(screen.getByTestId('qa-bar-step')).toHaveTextContent('A-01');
  });

  it('after an own action the bar polls every second for a move made elsewhere', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    renderBar();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    fireEvent.click(screen.getByTestId('qa-bar-ok'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    api.state = joined('A-06', 5);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(FAST_POLL_MS);
    });
    expect(screen.getByTestId('qa-bar-step')).toHaveTextContent('A-06');
  });

  // ------------------------------------------------------ role and voice ---

  it('shows in amber when the page is not signed in as the step expects', async () => {
    api.state = joined('A-04', 3, { card: card('A-04', { role: 'operator' }) });
    renderBar('/login');
    const badge = await screen.findByTestId('qa-bar-role');
    expect(badge).toHaveTextContent('Braucht: Betreiber · Du bist: abgemeldet');
    expect(badge).toHaveAttribute('data-match', 'no');
  });

  it('shows in green when the role matches', async () => {
    api.state = joined('A-04', 3, { card: card('A-04', { role: 'operator' }) });
    renderBar('/instance');
    const badge = await screen.findByTestId('qa-bar-role');
    expect(badge).toHaveTextContent('Du bist: Betreiber');
    expect(badge).toHaveAttribute('data-match', 'yes');
  });

  it("shows the assistant's message, and a note answers it", async () => {
    const user = userEvent.setup();
    api.state = joined('A-04', 3, { message: 'Welcher Button war gemeint?' });
    api.posts = [storedAnswer('n-1', joined('A-04', 3, { message: null }))];
    renderBar();
    expect(await screen.findByTestId('qa-bar-message')).toHaveTextContent('Welcher Button war gemeint?');

    await user.click(screen.getByTestId('qa-bar-answer'));
    await waitFor(() => expect(screen.getByTestId('qa-bar-text')).toHaveFocus());
    await user.type(screen.getByTestId('qa-bar-text'), 'Der blaue');
    await user.click(screen.getByTestId('qa-bar-send'));

    await waitFor(() => expect(postedBodies()).toHaveLength(1));
    expect(postedBodies()[0]).toMatchObject({ kind: 'note', text: 'Der blaue', answers_message: 'Welcher Button war gemeint?' });
    await waitFor(() => expect(screen.queryByTestId('qa-bar-message')).not.toBeInTheDocument());
  });

  // --------------------------------------------------------------- trail ---

  it('sends the trail since the step began, and starts a new one after a move', async () => {
    const user = userEvent.setup();
    api.posts = [storedAnswer('n-1', joined('A-05', 4), true), storedAnswer('n-2', joined('A-06', 5), true)];
    renderBar('/stations', <button type="button">Speichern</button>);
    await screen.findByTestId('qa-bar-step');
    await user.click(screen.getByRole('button', { name: 'Speichern' }));
    await user.click(screen.getByTestId('qa-bar-ok'));
    await waitFor(() => expect(screen.getByTestId('qa-bar-step')).toHaveTextContent('A-05'));
    await user.click(screen.getByTestId('qa-bar-ok'));
    await waitFor(() => expect(postedBodies()).toHaveLength(2));

    const [first, second] = postedBodies();
    expect(first.trail).toEqual(expect.arrayContaining([expect.objectContaining({ t: 'click', txt: 'Speichern' })]));
    expect(JSON.stringify(first.trail)).not.toContain('qa-bar');
    expect(JSON.stringify(second.trail)).not.toContain('Speichern');
  });

  it('sends the route redacted', async () => {
    const user = userEvent.setup();
    renderBar('/reset-password?token=abc&lang=de');
    await user.click(await screen.findByTestId('qa-bar-ok'));
    await waitFor(() => expect(postedBodies()).toHaveLength(1));
    expect(postedBodies()[0].route).toBe('/reset-password?token=:redacted&lang=de');
  });

  // -------------------------------------------------------- device and layout ---

  function phone(isPhone: boolean): void {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: isPhone && query === '(pointer: coarse)', media: query }));
    vi.stubGlobal('innerWidth', isPhone ? 412 : 1280);
  }

  it('says when a step belongs on the other device', async () => {
    phone(false);
    api.state = joined('E-01', 66, { card: card('E-01', { device: 'phone' }) });
    renderBar('/join');
    expect(await screen.findByTestId('qa-bar-device')).toHaveTextContent('Auf dem Handy ausführen');
  });

  it('shows no device line when the device matches', async () => {
    phone(true);
    api.state = joined('E-01', 66, { card: card('E-01', { device: 'phone' }) });
    renderBar('/join');
    await screen.findByTestId('qa-bar-step');
    expect(screen.queryByTestId('qa-bar-device')).not.toBeInTheDocument();
  });

  it('the bottom dock rises above an open phone keyboard and follows it', async () => {
    const listeners = new Map<string, () => void>();
    const viewport = {
      height: 800,
      offsetTop: 0,
      addEventListener: vi.fn((type: string, fn: () => void) => listeners.set(type, fn)),
      removeEventListener: vi.fn((type: string) => listeners.delete(type)),
    };
    Object.defineProperty(window, 'visualViewport', { value: viewport, configurable: true });
    vi.stubGlobal('innerHeight', 800);
    const { unmount } = renderBar();
    const bar = await screen.findByTestId('qa-bar');
    expect(getComputedStyle(bar).bottom).not.toBe('408px');

    viewport.height = 400;
    act(() => listeners.get('resize')!());
    expect(getComputedStyle(bar).bottom).toBe('408px');
    expect(408).toBeGreaterThan(BOTTOM_BASE_PX);

    viewport.height = 800;
    act(() => listeners.get('resize')!());
    expect(getComputedStyle(bar).bottom).not.toBe('408px');

    unmount();
    expect(viewport.removeEventListener).toHaveBeenCalledWith('resize', expect.any(Function));
    expect(viewport.removeEventListener).toHaveBeenCalledWith('scroll', expect.any(Function));
    expect(listeners.size).toBe(0);
  });

  it('never grows wider than the screen, and long words wrap', async () => {
    api.state = joined('A-04', 3, { card: card('A-04', { title: 'x'.repeat(300) }) });
    renderBar();
    const bar = await screen.findByTestId('qa-bar');
    const style = getComputedStyle(bar);
    // jsdom resolves the calc against its window: the screen width minus the 16 px margin.
    expect(style.maxWidth).toBe(`${window.innerWidth - 16}px`);
    expect(style.overflowWrap).toBe('anywhere');
  });

  // --------------------------------------- note, pointing, hiding, delivery ---

  it('a note needs a text and is stored with kind note', async () => {
    const user = userEvent.setup();
    renderBar();
    await user.click(await screen.findByTestId('qa-bar-note'));
    const send = screen.getByTestId('qa-bar-send');
    expect(send).toBeDisabled();

    await user.type(screen.getByTestId('qa-bar-text'), '   ');
    expect(send).toBeDisabled();
    await user.clear(screen.getByTestId('qa-bar-text'));
    await user.type(screen.getByTestId('qa-bar-text'), 'Der Knopf ist zu klein');
    await user.click(send);

    await waitFor(() => expect(postedBodies()).toHaveLength(1));
    expect(postedBodies()[0]).toMatchObject({ kind: 'note', step: 'A-04', text: 'Der Knopf ist zu klein' });
    expect(await screen.findByTestId('qa-bar-toast')).toHaveTextContent('Notiz gespeichert');
    expect(screen.getByTestId('qa-bar-text')).toHaveValue('');
  });

  it('skip carries the note field as its reason', async () => {
    const user = userEvent.setup();
    renderBar();
    await user.click(await screen.findByTestId('qa-bar-note'));
    await user.type(screen.getByTestId('qa-bar-text'), 'kein Testkonto');
    await user.click(screen.getByTestId('qa-bar-skip'));

    await waitFor(() => expect(postedBodies()).toHaveLength(1));
    expect(postedBodies()[0]).toMatchObject({ kind: 'skip', text: 'kein Testkonto' });
  });

  it('point at swallows the next click and attaches selector and text', async () => {
    const user = userEvent.setup();
    const productClick = vi.fn();
    renderBar(
      '/stations',
      <button type="button" data-testid="product-save" onClick={productClick}>
        Speichern
      </button>,
    );
    await user.click(await screen.findByTestId('qa-bar-expand'));
    await user.click(screen.getByTestId('qa-bar-point'));
    expect(screen.getByTestId('qa-bar-pointing')).toBeInTheDocument();

    await user.click(screen.getByTestId('product-save'));
    expect(productClick).not.toHaveBeenCalled();
    expect(screen.getByTestId('qa-bar-target')).toHaveTextContent('button[data-testid=product-save] · Speichern');
    expect(screen.queryByTestId('qa-bar-pointing')).not.toBeInTheDocument();

    await user.type(screen.getByTestId('qa-bar-text'), 'Zu klein');
    await user.click(screen.getByTestId('qa-bar-send'));
    await waitFor(() => expect(postedBodies()).toHaveLength(1));
    expect(postedBodies()[0]).toMatchObject({
      kind: 'note',
      selector: 'html>body>div>button[data-testid=product-save]',
      element_text: 'Speichern',
    });

    // Pointing was one-shot: the product works again.
    await user.click(screen.getByTestId('product-save'));
    expect(productClick).toHaveBeenCalledTimes(1);
  });

  it('Escape cancels pointing, and the chip can be removed', async () => {
    const user = userEvent.setup();
    const productClick = vi.fn();
    renderBar(
      '/stations',
      <button type="button" data-testid="product-save" onClick={productClick}>
        Speichern
      </button>,
    );
    await user.click(await screen.findByTestId('qa-bar-expand'));
    await user.click(screen.getByTestId('qa-bar-point'));
    await user.keyboard('{Escape}');
    expect(screen.queryByTestId('qa-bar-pointing')).not.toBeInTheDocument();
    await user.click(screen.getByTestId('product-save'));
    expect(productClick).toHaveBeenCalledTimes(1);

    await user.click(screen.getByTestId('qa-bar-point'));
    await user.click(screen.getByTestId('product-save'));
    expect(screen.getByTestId('qa-bar-target')).toBeInTheDocument();
    // The remove control is a 44 px target with a name.
    const remove = screen.getByRole('button', { name: 'Element entfernen' });
    expect(remove).toHaveAttribute('data-testid', 'qa-bar-target-remove');
    expect(getComputedStyle(remove).width).toBe('44px');
    expect(getComputedStyle(remove).height).toBe('44px');
    await user.click(remove);
    expect(screen.queryByTestId('qa-bar-target')).not.toBeInTheDocument();
  });

  it('?qa=off leaves no element and is remembered, ?qa=on brings the bar back', async () => {
    const user = userEvent.setup();
    renderBar('/login?qa=off', <NavigateButton to="/dashboard?qa=on" />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    await act(async () => {});
    expect(barRoot()).toBeNull();
    expect(localStorage.getItem(HIDDEN_KEY)).toBe('1');

    await user.click(screen.getByRole('button', { name: 'go' }));
    expect(await screen.findByTestId('qa-bar-step')).toBeInTheDocument();
    expect(localStorage.getItem(HIDDEN_KEY)).toBeNull();
  });

  it('a remembered local hide holds on a page without the parameter', async () => {
    localStorage.setItem(HIDDEN_KEY, '1');
    renderBar('/dashboard');
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    await act(async () => {});
    expect(barRoot()).toBeNull();
  });

  it('the hide button removes the bar and remembers it', async () => {
    const user = userEvent.setup();
    renderBar();
    await user.click(await screen.findByTestId('qa-bar-expand'));
    await user.click(screen.getByTestId('qa-bar-hide'));
    expect(barRoot()).toBeNull();
    expect(localStorage.getItem(HIDDEN_KEY)).toBe('1');
  });

  it("the kit's hidden flag leaves no element, and the next poll brings the bar back", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    api.state = joined('A-04', 3, { hidden: true });
    renderBar();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetch).toHaveBeenCalledWith('/api/qa/state', expect.anything());
    expect(barRoot()).toBeNull();

    api.state = joined('A-05', 4);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS);
    });
    expect(barRoot()).not.toBeNull();
    expect(screen.getByTestId('qa-bar-step')).toHaveTextContent('A-05');
  });

  it('a note sent offline is queued and stored exactly once when the network returns', async () => {
    const user = userEvent.setup();
    api.posts = [() => Promise.reject(new TypeError('Failed to fetch')), storedAnswer('n-9')];
    renderBar();
    await user.click(await screen.findByTestId('qa-bar-ok'));
    expect(await screen.findByTestId('qa-bar-toast')).toHaveTextContent('Kein Netz');
    expect(JSON.parse(localStorage.getItem(OUTBOX_KEY)!)).toHaveLength(1);

    await act(async () => {
      window.dispatchEvent(new Event('online'));
    });
    await waitFor(() => expect(localStorage.getItem(OUTBOX_KEY)).toBeNull());
    await act(async () => {
      window.dispatchEvent(new Event('online'));
    });

    const bodies = postedBodies();
    expect(bodies).toHaveLength(2);
    expect(bodies[0].client_id).toBe(bodies[1].client_id);
  });

  it('every stored event writes one [OBS] line for the observer', async () => {
    const user = userEvent.setup();
    const debug = vi.spyOn(console, 'debug').mockImplementation(() => undefined);
    renderBar();
    await user.click(await screen.findByTestId('qa-bar-ok'));
    await waitFor(() => expect(debug).toHaveBeenCalledTimes(1));

    const line = debug.mock.calls[0][0] as string;
    expect(line.startsWith('[OBS]')).toBe(true);
    expect(JSON.parse(line.slice(5))).toMatchObject({
      t: 'feedback',
      kind: 'ok',
      step: 'A-04',
      stored: true,
      note_id: 'n-1',
    });
  });

  it('an open MUI dialog does not take the focus from the note field', async () => {
    const user = userEvent.setup();
    renderBar(
      '/instance',
      <Dialog open>
        <DialogTitle>Organisation archivieren?</DialogTitle>
        <DialogContent>
          <button type="button">Abbrechen</button>
        </DialogContent>
      </Dialog>,
    );
    await user.click(await screen.findByTestId('qa-bar-note'));
    const field = screen.getByTestId('qa-bar-text');
    await user.click(field);
    await user.type(field, 'Der Dialog nennt nicht, was einfriert');
    // MUI's trap also checks on an interval. Give it time to strike.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 150));
    });

    expect(field).toHaveFocus();
    expect(field).toHaveValue('Der Dialog nennt nicht, was einfriert');
    expect(screen.getByText('Organisation archivieren?')).toBeInTheDocument();
  });
});
