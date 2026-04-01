import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import FeedView from './FeedView';
import { AuthContext } from '../../auth/AuthContext';
import apiClient from '../../api/client';
import { useWebSocket } from '../../hooks/useWebSocket';
import type { WsMessage } from '../../hooks/useWebSocket';
import { useUnreadNotices, __resetNoticeStoreForTests } from '../../hooks/useUnreadNotices';
import type { FeedChangedEvent, FeedEvent, FeedEventType } from '../../api/types';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

vi.mock('../../hooks/useWebSocket', () => ({
  useWebSocket: vi.fn(),
}));

// CountdownTimer depends on real time — stub it out so we don't need fake timers here.
vi.mock('../../components/CountdownTimer', () => ({
  default: ({ targetTime }: { targetTime: string }) => (
    <div data-testid="countdown-timer">{targetTime}</div>
  ),
}));

const authUser = {
  id: 'u1',
  email: 'participant@example.com',
  display_name: 'Teilnehmer',
  role: 'participant' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const makeEvent = (
  id: string,
  type: FeedEventType,
  content: Record<string, unknown> = {},
  createdAt = '2026-01-01T12:00:00Z',
): FeedEvent => ({
  id,
  organization_id: 'org-1',
  run_id: 'run-1',
  event_type: type,
  content,
  author_id: null,
  target_team_id: null,
  created_at: createdAt,
});

const renderFeed = () =>
  render(
    <MemoryRouter initialEntries={['/runs/run-1/participant/feed']}>
      <AuthContext.Provider
        value={{
          user: authUser,
          isAuthenticated: true,
          isLoading: false,
          login: vi.fn(),
          logout: vi.fn(),
          checkAuth: vi.fn(),
          needsTwoFactorSetup: () => false,
        }}
      >
        <Routes>
          <Route path="/runs/:runId/participant/feed" element={<FeedView />} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('FeedView', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    localStorage.clear();
    __resetNoticeStoreForTests();
    vi.mocked(useWebSocket).mockReturnValue({ status: 'open', lastError: null, send: vi.fn() });
  });

  it('shows empty state when no events', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [], total: 0, skip: 0, limit: 100 },
    });
    renderFeed();
    await waitFor(() => {
      expect(screen.getByText('Noch keine Mitteilungen')).toBeInTheDocument();
    });
  });

  it('renders announcement event as outlined card', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [makeEvent('ev1', 'announcement', { text: 'Wichtige Mitteilung' })],
        total: 1,
        skip: 0,
        limit: 100,
      },
    });
    renderFeed();
    await waitFor(() => {
      expect(screen.getByText('Wichtige Mitteilung')).toBeInTheDocument();
    });
    expect(screen.getByText('Mitteilung')).toBeInTheDocument();
  });

  it('makes a web address in a notice a link that opens beside the app', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [makeEvent('ev1', 'announcement', { text: 'Der Stadtplan: https://augsburg.de/plan.' })],
        total: 1,
        skip: 0,
        limit: 100,
      },
    });
    renderFeed();
    const link = await screen.findByRole('link', { name: 'https://augsburg.de/plan' });
    expect(link).toHaveAttribute('href', 'https://augsburg.de/plan');
    expect(link).toHaveAttribute('target', '_blank');
  });

  // A result entry leads to the leaderboard, where the published link lives.
  it('offers the way to the leaderboard on a result entry', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [
          makeEvent('ev1', 'results_available', {
            de: 'Das Endergebnis ist veröffentlicht. Den Link findet ihr in der Rangliste.',
            en: 'The final result is published.',
            result_link: true,
          }),
        ],
        total: 1,
        skip: 0,
        limit: 100,
      },
    });
    renderFeed();

    expect(await screen.findByText(/Das Endergebnis ist veröffentlicht/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Zur Rangliste' })).toBeInTheDocument();
  });

  it('renders alert event as MUI Alert with error severity', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [makeEvent('ev1', 'alert', { text: 'Kritischer Alarm!' })],
        total: 1,
        skip: 0,
        limit: 100,
      },
    });
    renderFeed();
    await waitFor(() => {
      expect(screen.getByText('Kritischer Alarm!')).toBeInTheDocument();
    });
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('renders countdown event with CountdownTimer', async () => {
    const target = '2026-12-31T23:59:00Z';
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [makeEvent('ev1', 'countdown', { text: 'Countdown läuft!', target_time: target })],
        total: 1,
        skip: 0,
        limit: 100,
      },
    });
    renderFeed();
    await waitFor(() => {
      expect(screen.getByTestId('countdown-timer')).toBeInTheDocument();
    });
    expect(screen.getByText('Countdown läuft!')).toBeInTheDocument();
  });

  // Newest first: a new alarm is the first thing on the tab, not the last line
  // below the fold.
  it('lists the newest entry first, a live one on top', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [
          { ...makeEvent('e1', 'announcement', { text: 'Erste Meldung' }), created_at: '2026-01-01T10:00:00Z' },
          { ...makeEvent('e2', 'announcement', { text: 'Zweite Meldung' }), created_at: '2026-01-01T11:00:00Z' },
        ],
        total: 2,
        skip: 0,
        limit: 100,
      },
    });
    renderFeed();
    await waitFor(() => expect(screen.getByText('Zweite Meldung')).toBeInTheDocument());

    const order = () =>
      screen.getAllByText(/^(Erste Meldung|Zweite Meldung|Neuer Alarm)$/).map((el) => el.textContent);
    expect(order()).toEqual(['Zweite Meldung', 'Erste Meldung']);

    const handlers = vi.mocked(useWebSocket).mock.calls.at(-1)![0].handlers!;
    const alarm = { ...makeEvent('e3', 'alert', { text: 'Neuer Alarm' }), created_at: '2026-01-01T12:00:00Z' };
    act(() => {
      handlers.feed_event(alarm, { type: 'feed_event', data: alarm, timestamp: alarm.created_at } as WsMessage);
    });

    expect(order()).toEqual(['Neuer Alarm', 'Zweite Meldung', 'Erste Meldung']);
  });

  it('WS push of feed_event prepends to list and dedupes by id', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [makeEvent('old-ev', 'announcement', { text: 'Alte Meldung' })],
        total: 1,
        skip: 0,
        limit: 100,
      },
    });
    renderFeed();
    await waitFor(() => expect(screen.getByText('Alte Meldung')).toBeInTheDocument());

    const mockedUseWs = vi.mocked(useWebSocket);
    const handlers = mockedUseWs.mock.calls.at(-1)![0].handlers!;

    const newEv = makeEvent('new-ev', 'milestone', { text: 'Meilenstein erreicht!' });
    act(() => {
      handlers.feed_event(newEv, {
        type: 'feed_event',
        data: newEv,
        timestamp: '2026-01-01T12:01:00Z',
      } as WsMessage);
    });

    await waitFor(() => {
      expect(screen.getByText('Meilenstein erreicht!')).toBeInTheDocument();
    });
    expect(screen.getByText('Alte Meldung')).toBeInTheDocument();

    // Sending the same event again should not add a duplicate
    act(() => {
      handlers.feed_event(newEv, {
        type: 'feed_event',
        data: newEv,
        timestamp: '2026-01-01T12:02:00Z',
      } as WsMessage);
    });

    expect(screen.getAllByText('Meilenstein erreicht!').length).toBe(1);
  });

  // The publish of a result replaces the earlier result notice and removes the
  // interim one, and a withdrawal removes the notice and adds nothing. The backend
  // then sends `feed_changed` on the run socket, and the open page reads its feed
  // again and shows that list.
  describe('an open page after the result was published or withdrawn', () => {
    const RESULT_TEXT = /Das Endergebnis ist veröffentlicht/;
    const INTERIM_TEXT = /Die Bewertung läuft noch/;

    const runEnded = makeEvent(
      'end-1',
      'results_available',
      { de: 'Eure Ergebnisse sind verfügbar!', en: "Your team's results are available!" },
      '2026-01-01T10:00:00Z',
    );
    const interim = makeEvent(
      'interim-1',
      'results_available',
      {
        de: 'Der Lauf ist beendet. Die Bewertung läuft noch, die Rangliste ist ein Zwischenstand.',
        en: 'The run has ended. Grading is still under way, the leaderboard is an interim one.',
        interim: true,
      },
      '2026-01-01T10:00:00Z',
    );
    const feedbackPrompt = makeEvent(
      'prompt-1',
      'feedback_prompt',
      { de: 'Erzählt uns, wie es war!', en: 'Tell us how it went!' },
      '2026-01-01T10:00:01Z',
    );
    const resultNotice = (id: string, createdAt: string) =>
      makeEvent(
        id,
        'results_available',
        {
          de: 'Das Endergebnis ist veröffentlicht. Den Link findet ihr in der Rangliste.',
          en: 'The final result is published. You find the link in the leaderboard.',
          result_link: true,
        },
        createdAt,
      );

    const feedOf = (items: FeedEvent[]) => ({
      data: { items, total: items.length, skip: 0, limit: 100 },
    });

    // Delivers a message the way useWebSocket does: to the handler of its type, if the page has one.
    const receive = (type: string, data: unknown) => {
      const handlers = vi.mocked(useWebSocket).mock.calls.at(-1)![0].handlers!;
      act(() => {
        handlers[type]?.(data, { type, data, timestamp: '2026-01-01T12:00:00Z' } as WsMessage);
      });
    };
    const feedChanged: FeedChangedEvent = { run_id: 'run-1' };

    // Lets every answer that is already there reach the page.
    const settle = () =>
      act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });

    // A read whose answer the test holds back, to place socket events before it.
    type Answer = ReturnType<typeof feedOf>;
    const heldBack = () => {
      let answer: (value: Answer) => void = () => {};
      const pending = new Promise<Answer>((resolve) => {
        answer = resolve;
      });
      const give = (items: FeedEvent[]) =>
        act(async () => {
          answer(feedOf(items));
          await Promise.resolve();
        });
      return { pending, give };
    };

    const announcement = makeEvent(
      'announce-1',
      'announcement',
      { text: 'Treffpunkt ist der Rathausplatz' },
      '2026-01-01T12:30:00Z',
    );

    it('shows one result notice after a second publish', async () => {
      const first = resultNotice('result-1', '2026-01-01T11:00:00Z');
      const second = resultNotice('result-2', '2026-01-01T12:00:00Z');
      vi.mocked(apiClient.get)
        .mockResolvedValueOnce(feedOf([runEnded, feedbackPrompt, first]))
        .mockResolvedValue(feedOf([runEnded, feedbackPrompt, second]));
      renderFeed();
      expect(await screen.findByText(RESULT_TEXT)).toBeInTheDocument();

      // The second publish: its new notice as a feed event, then the word that
      // the feed changed. Each of the two starts a read of the feed.
      receive('feed_event', second);
      receive('feed_changed', feedChanged);

      await waitFor(() => expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(3));
      await settle();
      expect(screen.getAllByText(RESULT_TEXT)).toHaveLength(1);
      // Every other notice is untouched.
      expect(screen.getByText('Eure Ergebnisse sind verfügbar!')).toBeInTheDocument();
      expect(screen.getByText('Erzählt uns, wie es war!')).toBeInTheDocument();

      // The feed event of the same notice arriving late adds nothing.
      receive('feed_event', second);
      expect(screen.getAllByText(RESULT_TEXT)).toHaveLength(1);
      await settle();
      expect(screen.getAllByText(RESULT_TEXT)).toHaveLength(1);
    });

    it('shows no interim notice after a publish', async () => {
      const published = resultNotice('result-1', '2026-01-01T12:00:00Z');
      vi.mocked(apiClient.get)
        .mockResolvedValueOnce(feedOf([interim, feedbackPrompt]))
        .mockResolvedValue(feedOf([feedbackPrompt, published]));
      renderFeed();
      expect(await screen.findByText(INTERIM_TEXT)).toBeInTheDocument();

      receive('feed_event', published);
      receive('feed_changed', feedChanged);

      await waitFor(() => expect(screen.queryByText(INTERIM_TEXT)).not.toBeInTheDocument());
      expect(screen.getAllByText(RESULT_TEXT)).toHaveLength(1);
      expect(screen.getByText('Erzählt uns, wie es war!')).toBeInTheDocument();
    });

    it('shows no result notice after a withdrawal, and keeps the list while it reads', async () => {
      let answer: ((value: ReturnType<typeof feedOf>) => void) | undefined;
      const pending = new Promise<ReturnType<typeof feedOf>>((resolve) => {
        answer = resolve;
      });
      vi.mocked(apiClient.get)
        .mockResolvedValueOnce(feedOf([runEnded, feedbackPrompt, resultNotice('result-1', '2026-01-01T11:00:00Z')]))
        .mockReturnValueOnce(pending);
      renderFeed();
      expect(await screen.findByText(RESULT_TEXT)).toBeInTheDocument();

      // A withdrawal adds no feed event: `feed_changed` is all the page gets.
      receive('feed_changed', feedChanged);
      await waitFor(() => expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(2));

      // While the feed is read again the page keeps what it shows: no spinner, no empty list.
      expect(screen.getByText('Eure Ergebnisse sind verfügbar!')).toBeInTheDocument();
      expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();

      await act(async () => {
        answer!(feedOf([runEnded, feedbackPrompt]));
        await Promise.resolve();
      });

      await waitFor(() => expect(screen.queryByText(RESULT_TEXT)).not.toBeInTheDocument());
      expect(screen.getByText('Eure Ergebnisse sind verfügbar!')).toBeInTheDocument();
      expect(screen.getByText('Erzählt uns, wie es war!')).toBeInTheDocument();
    });

    it('shows the feed of the last change when two answers arrive out of order', async () => {
      type Answer = ReturnType<typeof feedOf>;
      const answers: ((value: Answer) => void)[] = [];
      const pendingAnswer = () =>
        new Promise<Answer>((resolve) => {
          answers.push(resolve);
        });
      vi.mocked(apiClient.get)
        .mockResolvedValueOnce(feedOf([runEnded, feedbackPrompt]))
        .mockReturnValueOnce(pendingAnswer())
        .mockReturnValueOnce(pendingAnswer());
      renderFeed();
      expect(await screen.findByText('Eure Ergebnisse sind verfügbar!')).toBeInTheDocument();

      // A publish, then at once its withdrawal: two reads are under way.
      receive('feed_changed', feedChanged);
      receive('feed_changed', feedChanged);
      await waitFor(() => expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(3));

      // The later read (after the withdrawal) answers first, the earlier one after it.
      await act(async () => {
        answers[1](feedOf([runEnded, feedbackPrompt]));
        await Promise.resolve();
      });
      await act(async () => {
        answers[0](feedOf([runEnded, feedbackPrompt, resultNotice('result-1', '2026-01-01T11:00:00Z')]));
        await Promise.resolve();
      });

      expect(screen.queryByText(RESULT_TEXT)).not.toBeInTheDocument();
      expect(screen.getByText('Erzählt uns, wie es war!')).toBeInTheDocument();
    });

    it('says so when the feed cannot be read again, and keeps the list', async () => {
      vi.mocked(apiClient.get)
        .mockResolvedValueOnce(feedOf([runEnded, feedbackPrompt]))
        .mockRejectedValueOnce(new Error('Netzwerk weg'));
      renderFeed();
      expect(await screen.findByText('Eure Ergebnisse sind verfügbar!')).toBeInTheDocument();

      receive('feed_changed', feedChanged);

      expect(await screen.findByText(/Netzwerk weg/)).toBeInTheDocument();
      expect(screen.getByText('Eure Ergebnisse sind verfügbar!')).toBeInTheDocument();
    });

    // A notice travels through the database listener, `feed_changed` goes out
    // directly. So the feed event of a notice the server has deleted since can arrive
    // after the last read was answered. The feed the server returns is the truth for
    // a result notice and an interim notice: the page never adds one from the socket
    // alone.
    describe('a feed event that arrives after its notice was removed', () => {
      it('does not put the notice of the first publish beside that of the second', async () => {
        const first = resultNotice('result-1', '2026-01-01T11:00:00Z');
        const second = resultNotice('result-2', '2026-01-01T12:00:00Z');
        vi.mocked(apiClient.get)
          .mockResolvedValueOnce(feedOf([runEnded, feedbackPrompt]))
          .mockResolvedValueOnce(feedOf([runEnded, feedbackPrompt, first]))
          .mockResolvedValue(feedOf([runEnded, feedbackPrompt, second]));
        renderFeed();
        expect(await screen.findByText('Eure Ergebnisse sind verfügbar!')).toBeInTheDocument();

        // Two publishes, each read to its end before a feed event came through.
        receive('feed_changed', feedChanged);
        expect(await screen.findByText(RESULT_TEXT)).toBeInTheDocument();
        receive('feed_changed', feedChanged);
        await waitFor(() => expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(3));
        await settle();

        // Now the feed event of the first publish. Its notice is deleted.
        receive('feed_event', first);
        expect(screen.getAllByText(RESULT_TEXT)).toHaveLength(1);
        await settle();
        expect(screen.getAllByText(RESULT_TEXT)).toHaveLength(1);
        // The page asked the server instead of trusting the event.
        expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(4);
        expect(screen.getByText('Erzählt uns, wie es war!')).toBeInTheDocument();
      });

      it('does not put the result notice back after a withdrawal', async () => {
        const published = resultNotice('result-1', '2026-01-01T11:00:00Z');
        vi.mocked(apiClient.get)
          .mockResolvedValueOnce(feedOf([runEnded, feedbackPrompt]))
          .mockResolvedValueOnce(feedOf([runEnded, feedbackPrompt, published]))
          .mockResolvedValue(feedOf([runEnded, feedbackPrompt]));
        renderFeed();
        expect(await screen.findByText('Eure Ergebnisse sind verfügbar!')).toBeInTheDocument();

        // The publish, then the withdrawal, each read to its end.
        receive('feed_changed', feedChanged);
        expect(await screen.findByText(RESULT_TEXT)).toBeInTheDocument();
        receive('feed_changed', feedChanged);
        await waitFor(() => expect(screen.queryByText(RESULT_TEXT)).not.toBeInTheDocument());

        // The feed event of the publish arrives after the withdrawal was read.
        receive('feed_event', published);
        expect(screen.queryByText(RESULT_TEXT)).not.toBeInTheDocument();
        await settle();
        expect(screen.queryByText(RESULT_TEXT)).not.toBeInTheDocument();
        expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(4);
        expect(screen.getByText('Eure Ergebnisse sind verfügbar!')).toBeInTheDocument();
      });

      it('does not put the interim notice back after a publish', async () => {
        const published = resultNotice('result-1', '2026-01-01T12:00:00Z');
        vi.mocked(apiClient.get)
          .mockResolvedValueOnce(feedOf([announcement]))
          .mockResolvedValue(feedOf([announcement, feedbackPrompt, published]));
        renderFeed();
        expect(await screen.findByText('Treffpunkt ist der Rathausplatz')).toBeInTheDocument();

        // The run ended with grading open and the result was published right
        // after. The publish is read to its end first.
        receive('feed_changed', feedChanged);
        expect(await screen.findByText(RESULT_TEXT)).toBeInTheDocument();

        // The feed event of the end of the run arrives last. Its interim notice is deleted.
        receive('feed_event', interim);
        expect(screen.queryByText(INTERIM_TEXT)).not.toBeInTheDocument();
        await settle();
        expect(screen.queryByText(INTERIM_TEXT)).not.toBeInTheDocument();
        expect(screen.getAllByText(RESULT_TEXT)).toHaveLength(1);
        expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(3);
      });
    });

    // The answer to a read is the feed as it was when the server read it. An entry
    // the socket delivers while the answer is on its way is newer than that, and the
    // answer must not erase it.
    describe('a feed event that arrives while a read is under way', () => {
      it('keeps an announcement that arrived during the read after a withdrawal', async () => {
        const read = heldBack();
        vi.mocked(apiClient.get)
          .mockResolvedValueOnce(feedOf([runEnded, feedbackPrompt, resultNotice('result-1', '2026-01-01T11:00:00Z')]))
          .mockReturnValueOnce(read.pending);
        renderFeed();
        expect(await screen.findByText(RESULT_TEXT)).toBeInTheDocument();

        receive('feed_changed', feedChanged);
        await waitFor(() => expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(2));

        // The server has read the feed for the answer. Then the announcement is sent.
        receive('feed_event', announcement);
        expect(screen.getByText('Treffpunkt ist der Rathausplatz')).toBeInTheDocument();

        await read.give([runEnded, feedbackPrompt]);

        expect(screen.queryByText(RESULT_TEXT)).not.toBeInTheDocument();
        expect(screen.getByText('Treffpunkt ist der Rathausplatz')).toBeInTheDocument();
        expect(screen.getByText('Erzählt uns, wie es war!')).toBeInTheDocument();
        // An ordinary entry starts no read of its own.
        expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(2);
      });

      it('shows it once when the answer holds it too', async () => {
        const read = heldBack();
        vi.mocked(apiClient.get)
          .mockResolvedValueOnce(feedOf([runEnded, feedbackPrompt]))
          .mockReturnValueOnce(read.pending);
        renderFeed();
        expect(await screen.findByText('Eure Ergebnisse sind verfügbar!')).toBeInTheDocument();

        receive('feed_changed', feedChanged);
        await waitFor(() => expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(2));
        receive('feed_event', announcement);

        await read.give([runEnded, feedbackPrompt, announcement]);

        expect(screen.getAllByText('Treffpunkt ist der Rathausplatz')).toHaveLength(1);
      });

      it('keeps an announcement that arrived while the page was first loading', async () => {
        const read = heldBack();
        vi.mocked(apiClient.get).mockReturnValueOnce(read.pending);
        renderFeed();
        await waitFor(() => expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(1));

        receive('feed_event', announcement);
        await read.give([runEnded, feedbackPrompt]);

        expect(screen.getByText('Treffpunkt ist der Rathausplatz')).toBeInTheDocument();
        expect(screen.getByText('Eure Ergebnisse sind verfügbar!')).toBeInTheDocument();
      });

      it('keeps the spinner until the newest read answers, not an empty list', async () => {
        const first = heldBack();
        const second = heldBack();
        vi.mocked(apiClient.get).mockReturnValueOnce(first.pending).mockReturnValueOnce(second.pending);
        renderFeed();
        await waitFor(() => expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(1));

        // The feed changes while the page loads: a second read, and the first answer is the older one.
        receive('feed_changed', feedChanged);
        await waitFor(() => expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(2));
        await first.give([]);

        expect(screen.queryByText('Noch keine Mitteilungen')).not.toBeInTheDocument();
        expect(screen.getByRole('progressbar')).toBeInTheDocument();

        await second.give([runEnded, feedbackPrompt]);
        expect(screen.getByText('Eure Ergebnisse sind verfügbar!')).toBeInTheDocument();
        expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
      });
    });
  });

  // The loaded list seeds the shared notice registry, so a notice that arrived
  // while the participant was elsewhere still reaches the activity tab even before
  // any WS event. Opening this tab is the act of seeing, so once its own load has
  // landed everything on the list counts as read.
  it('seeds the notice registry from the loaded feed list and marks it seen', async () => {
    localStorage.setItem(
      'clavis_feed_last_seen_run-1',
      String(Date.parse('2026-06-01T11:00:00Z')),
    );
    const { result } = renderHook(() => useUnreadNotices('run-1'));
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [
          makeEvent('seen-ev', 'announcement', { text: 'Alte Meldung' }, '2026-06-01T10:30:00Z'),
          makeEvent('fresh-ev', 'alert', { text: 'Neue Warnung' }, '2026-06-01T11:30:00Z'),
          makeEvent('noise-ev', 'system', { text: 'Systemgeräusch' }, '2026-06-01T11:45:00Z'),
        ],
        total: 3,
        skip: 0,
        limit: 100,
      },
    });

    renderFeed();
    await waitFor(() => expect(screen.getByText('Neue Warnung')).toBeInTheDocument());
    // The activity tab is open and its own load has landed — even the
    // notice that arrived before this visit now counts as read.
    await waitFor(() => expect(result.current.unreadCount).toBe(0));
  });

  // Opening the tab is the act of seeing, but only once this view's own load has
  // landed: a notice already known to the shared registry (for example from the run
  // shell's initial load) must not be marked seen while the fetch this component
  // started is still in flight.
  it('does not mark as seen before the list has loaded', async () => {
    let resolveList: ((value: {
      data: { items: FeedEvent[]; total: number; skip: number; limit: number };
    }) => void) | undefined;
    const pending = new Promise<{
      data: { items: FeedEvent[]; total: number; skip: number; limit: number };
    }>((resolve) => {
      resolveList = resolve;
    });
    vi.mocked(apiClient.get).mockReturnValueOnce(pending);

    const { result } = renderHook(() => useUnreadNotices('run-1'));
    act(() => {
      result.current.recordNotices([
        makeEvent('n1', 'announcement', { text: 'Mitteilung' }, '2026-06-01T12:00:00Z'),
      ]);
    });
    expect(result.current.unreadCount).toBe(1);

    renderFeed();

    // Still in flight — must not have marked the already-known notice seen.
    expect(result.current.unreadCount).toBe(1);

    await act(async () => {
      resolveList!({ data: { items: [], total: 0, skip: 0, limit: 100 } });
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.unreadCount).toBe(0));
  });

  it('has no axe accessibility violations on initial render', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [], total: 0, skip: 0, limit: 100 },
    });
    const { container } = renderFeed();
    await waitFor(() =>
      expect(screen.getByText('Noch keine Mitteilungen')).toBeInTheDocument(),
    );
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
