import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import CoordinatorTeamChat from './CoordinatorTeamChat';
import { AuthContext } from '../../auth/AuthContext';
import apiClient, { ApiError } from '../../api/client';
import { useWebSocket } from '../../hooks/useWebSocket';
import type { WsMessage } from '../../hooks/useWebSocket';
import type { ChatMessage } from '../../api/types';
import { formatTime } from '../../utils/formatDate';
import i18n from '../../i18n';

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

const authUser = {
  id: 'coord-me',
  email: 'coord@example.com',
  display_name: 'Coordinator',
  role: 'coordinator' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const teamFixture = {
  id: 'team-1',
  organization_id: 'org-1',
  run_id: 'run-1',
  name: 'Team Alpha',
  color: null,
  member_count: 2,
  share_code: null,
  coordinator_chat_open: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const makeMessage = (
  id: string,
  content: string,
  authorId: string | null,
  authorRole: ChatMessage['author_role'] = 'participant',
  isSystem = false,
  createdAt = '2026-01-01T10:00:00Z',
  authorDisplayName: string | null = null,
): ChatMessage => ({
  id,
  organization_id: 'org-1',
  team_id: 'team-1',
  author_id: authorId,
  content,
  is_system_message: isSystem,
  author_role: isSystem ? null : authorRole,
  author_display_name: isSystem ? null : authorDisplayName,
  created_at: createdAt,
});

/** Default GET mock: team endpoint returns the fixture, chat list the given items. */
const mockGets = (chatItems: ChatMessage[]) => {
  vi.mocked(apiClient.get).mockImplementation((url: string) => {
    if (url.endsWith('/chat/')) {
      return Promise.resolve({
        data: { items: chatItems, total: chatItems.length, skip: 0, limit: 100 },
      });
    }
    return Promise.resolve({ data: teamFixture });
  });
};

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={['/runs/run-1/teams/team-1/chat']}>
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
          <Route path="/runs/:runId/teams/:teamId/chat" element={<CoordinatorTeamChat />} />
          <Route path="/runs/:runId" element={<div>Dashboard-Stub</div>} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('CoordinatorTeamChat', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useWebSocket).mockReturnValue({ status: 'open', lastError: null, send: vi.fn() });
  });

  it('renders the team name and the message history (oldest-first)', async () => {
    // The endpoint returns oldest-first (created_at ASC) — mirror that here,
    // or the fixture asserts against an ordering the API never produces.
    mockGets([
      makeMessage(
        'm1',
        '[coordinator_request] Team Team Alpha has requested coordinator assistance.',
        null,
        null,
        true,
      ),
      makeMessage('m2', 'Wo ist Station 2?', 'guest-1', 'guest', false, '2026-01-01T10:01:00Z'),
    ]);

    renderPage();

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: 'Chat mit Team Alpha' }),
      ).toBeInTheDocument();
    });
    expect(screen.getByText('Wo ist Station 2?')).toBeInTheDocument();
    // System message (the help request) renders as italic divider row,
    // localized from the [coordinator_request] marker
    const systemText = screen.getByText('Das Team hat Hilfe von der Spielleitung angefordert.');
    expect(window.getComputedStyle(systemText).fontStyle).toBe('italic');
    // A system message shows its time like a normal message, computed
    // the same way ChatMessageList formats every message's timestamp: in the
    // UI language, not the browser locale.
    const expectedSystemTime = formatTime('2026-01-01T10:00:00Z', 'de');
    expect(expectedSystemTime).not.toMatch(/AM|PM/);
    expect(screen.getByText(expectedSystemTime)).toBeInTheDocument();
  });

  // The axe rule scrollable-region-focusable: the message list takes the keyboard and has a name.
  it('lets the message list take the keyboard and names it, keeping the role log', async () => {
    mockGets([makeMessage('m2', 'Wo ist Station 2?', 'guest-1', 'guest', false, '2026-01-01T10:01:00Z')]);
    renderPage();
    await screen.findByText('Wo ist Station 2?');
    const log = screen.getByRole('log', { name: 'Chat mit Team Alpha' });
    expect(log).toHaveAttribute('tabindex', '0');
    expect(log).toHaveAttribute('aria-live', 'polite');
  });

  it('shows a role chip on messages from other coordinators', async () => {
    mockGets([makeMessage('m1', 'Anderer Spielleitung', 'coord-other', 'coordinator')]);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Anderer Spielleitung')).toBeInTheDocument();
    });
    expect(screen.getByText('Spielleitung')).toBeInTheDocument();
  });

  it('renders the author_display_name as the sender label when present', async () => {
    mockGets([
      makeMessage('m1', 'Hallo aus dem Team', 'guest-1', 'guest', false, '2026-01-01T10:00:00Z', 'Max Mustermann'),
    ]);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Max Mustermann')).toBeInTheDocument();
    });
    // The raw UUID fragment must never leak into the UI.
    expect(screen.queryByText('guest-1'.slice(0, 6))).not.toBeInTheDocument();
  });

  it('falls back to a neutral label when author_display_name is missing', async () => {
    mockGets([makeMessage('m1', 'Anonyme Nachricht', 'guest-1', 'guest')]);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Anonyme Nachricht')).toBeInTheDocument();
    });
    expect(screen.getByText('Teammitglied')).toBeInTheDocument();
    expect(screen.queryByText('guest-1'.slice(0, 6))).not.toBeInTheDocument();
  });

  it('posts a reply via apiClient.post (Enter key)', async () => {
    mockGets([]);
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: makeMessage('m-new', 'Wir kommen!', 'coord-me', 'coordinator'),
    });

    renderPage();

    await waitFor(() => screen.getByPlaceholderText(/Nachricht schreiben/i));
    const input = screen.getByPlaceholderText(/Nachricht schreiben/i);
    await userEvent.type(input, 'Wir kommen!{Enter}');

    await waitFor(() => {
      expect(apiClient.post).toHaveBeenCalledWith(
        '/api/orgs/test-org/runs/run-1/teams/team-1/chat/',
        { content: 'Wir kommen!' },
      );
    });
  });

  it('appends a live WS chat_message to the list', async () => {
    mockGets([]);

    renderPage();
    await waitFor(() => screen.getByPlaceholderText(/Nachricht schreiben/i));

    const mockedUseWs = vi.mocked(useWebSocket);
    // Subscribes to the same team-chat WS topic as the participant chat
    expect(mockedUseWs.mock.calls.at(-1)![0].path).toBe(
      '/api/orgs/test-org/runs/run-1/teams/team-1/ws',
    );
    const handlers = mockedUseWs.mock.calls.at(-1)![0].handlers!;

    const fakeMsg = makeMessage('ws-msg', 'Live vom Team', 'guest-1', 'guest');
    act(() => {
      handlers.chat_message(fakeMsg, {
        type: 'chat_message',
        data: fakeMsg,
        timestamp: '2026-01-01T10:05:00Z',
      } as WsMessage);
    });

    await waitFor(() => {
      expect(screen.getByText('Live vom Team')).toBeInTheDocument();
    });
  });

  it('end assistance asks for confirmation before closing the window and navigating back', async () => {
    mockGets([]);
    vi.mocked(apiClient.post).mockResolvedValue({
      data: { message: 'Coordinator left the team chat' },
    });

    renderPage();
    await waitFor(() => screen.getByPlaceholderText(/Nachricht schreiben/i));

    await userEvent.click(screen.getByRole('button', { name: 'Hilfe beenden' }));

    // The confirm dialog names the consequence; the API call only fires once
    // the coordinator confirms it, never on the header button click alone.
    const dialog = await screen.findByRole('dialog');
    expect(
      within(dialog).getByText(
        'Hilfe für dieses Team wirklich beenden? Der Chat schließt sich, bis das Team erneut Hilfe anfordert.',
      ),
    ).toBeInTheDocument();
    expect(apiClient.post).not.toHaveBeenCalledWith(
      '/api/orgs/test-org/runs/run-1/teams/team-1/chat/leave-coordinator',
    );

    await userEvent.click(
      within(dialog).getByRole('button', { name: 'Hilfe beenden' }),
    );

    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith(
        '/api/orgs/test-org/runs/run-1/teams/team-1/chat/leave-coordinator',
      ),
    );
    expect(screen.getByText('Dashboard-Stub')).toBeInTheDocument();
  });

  it('cancelling the end-assistance dialog does not close the window', async () => {
    mockGets([]);

    renderPage();
    await waitFor(() => screen.getByPlaceholderText(/Nachricht schreiben/i));

    await userEvent.click(screen.getByRole('button', { name: 'Hilfe beenden' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Abbrechen' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it('disables the composer and shows an info banner once [coordinator_left] arrives over the socket', async () => {
    mockGets([]);

    renderPage();
    // Wait for the team fetch to settle first — otherwise its own
    // `coordinator_chat_open: true` reseeds windowOpen right after the
    // socket handler below closes it, racing the two async updates.
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Chat mit Team Alpha' })).toBeInTheDocument(),
    );

    const mockedUseWs = vi.mocked(useWebSocket);
    const handlers = mockedUseWs.mock.calls.at(-1)![0].handlers!;
    const leftMsg = makeMessage(
      'm-left',
      '[coordinator_left] The coordinator has left the team chat.',
      null,
      'participant',
      true,
    );
    act(() => {
      handlers.chat_message(leftMsg, {
        type: 'chat_message',
        data: leftMsg,
        timestamp: '2026-01-01T10:06:00Z',
      } as WsMessage);
    });

    await waitFor(() => {
      expect(
        screen.getByText('Der Chat ist geschlossen. Das Team muss erneut Hilfe anfordern.'),
      ).toBeInTheDocument();
    });
    await userEvent.type(
      screen.getByPlaceholderText(/Nachricht schreiben/i),
      'Noch da?',
    );
    expect(screen.getByLabelText('Senden')).toBeDisabled();
  });

  it('maps a coded 403 on send to the window-closed banner, not the generic retry banner', async () => {
    mockGets([]);
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      new ApiError(403, 'Chat window closed', 'CHAT_WINDOW_CLOSED'),
    );

    renderPage();
    await waitFor(() => screen.getByPlaceholderText(/Nachricht schreiben/i));
    await userEvent.type(
      screen.getByPlaceholderText(/Nachricht schreiben/i),
      'Wo seid ihr?{Enter}',
    );

    await waitFor(() => {
      expect(
        screen.getByText('Der Chat ist geschlossen. Das Team muss erneut Hilfe anfordern.'),
      ).toBeInTheDocument();
    });
    expect(
      screen.queryByText('Deine Nachricht konnte nicht gesendet werden. Bitte versuche es erneut.'),
    ).not.toBeInTheDocument();
  });

  it('renders the coordinator-left system message localized', async () => {
    mockGets([
      makeMessage('m1', '[coordinator_left] The coordinator has left the team chat.', null, 'participant', true),
    ]);

    renderPage();
    await waitFor(() =>
      expect(
        screen.getByText('Die Spielleitung hat den Team-Chat verlassen.'),
      ).toBeInTheDocument(),
    );
  });

  it('renders the coordinator-joined system message localized (finding 13, 2026-07-14)', async () => {
    mockGets([
      makeMessage('m1', '[coordinator_joined] A coordinator has joined the team chat.', null, 'participant', true),
    ]);

    renderPage();
    await waitFor(() =>
      expect(
        screen.getByText('Die Spielleitung ist dem Team-Chat beigetreten.'),
      ).toBeInTheDocument(),
    );
  });

  it('back button navigates to the run dashboard', async () => {
    mockGets([]);

    renderPage();
    await waitFor(() => screen.getByPlaceholderText(/Nachricht schreiben/i));

    await userEvent.click(
      screen.getByRole('button', { name: 'Zurück zur Laufseite' }),
    );
    expect(screen.getByText('Dashboard-Stub')).toBeInTheDocument();
  });

  it('has no axe accessibility violations on initial render', async () => {
    mockGets([]);

    const { container } = renderPage();
    await waitFor(() => screen.getByPlaceholderText(/Nachricht schreiben/i));
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  // ====================================================================
  // Deterministic window state. `load` does not depend on `t`, so a
  // translation-function change does not re-fire the fetches, and the second
  // team answer (still `coordinator_chat_open: true`) does not reseed the
  // window open after the socket has closed it. The tests below pin both
  // halves: fetch once per team, and keep a closed window closed.
  // ====================================================================

  it('fetches the team and the messages once per run and team while the translation function changes', async () => {
    let teamCalls = 0;
    let chatCalls = 0;
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.endsWith('/chat/')) {
        chatCalls += 1;
        return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
      }
      teamCalls += 1;
      return Promise.resolve({ data: { ...teamFixture } });
    });

    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Chat mit Team Alpha' })).toBeInTheDocument(),
    );

    // t changes identity whenever i18n emits (namespace
    // finishing late, language event). Neither may re-fire the fetches.
    await act(async () => {
      i18n.emit('languageChanged', 'de');
      i18n.emit('languageChanged', 'de');
    });

    expect(teamCalls).toBe(1);
    expect(chatCalls).toBe(1);
  });

  it('keeps the window closed when the delayed second team answer lands after the [coordinator_left] message', async () => {
    // Replicates the flake deterministically: t changes after the first
    // render, and the second team
    // answer only arrives after the socket message closed the window.
    let teamCalls = 0;
    let resolveSecond: ((data: { data: typeof teamFixture }) => void) | null = null;
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.endsWith('/chat/')) {
        return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
      }
      teamCalls += 1;
      if (teamCalls === 1) return Promise.resolve({ data: teamFixture });
      return new Promise((res) => {
        resolveSecond = res;
      });
    });

    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Chat mit Team Alpha' })).toBeInTheDocument(),
    );

    await act(async () => {
      i18n.emit('languageChanged', 'de');
    });

    const handlers = vi.mocked(useWebSocket).mock.calls.at(-1)![0].handlers!;
    const leftMsg = makeMessage(
      'm-left',
      '[coordinator_left] The coordinator has left the team chat.',
      null,
      'participant',
      true,
      '2026-01-01T10:06:00Z',
    );
    act(() => {
      handlers.chat_message(leftMsg, {
        type: 'chat_message',
        data: leftMsg,
        timestamp: '2026-01-01T10:06:00Z',
      } as WsMessage);
    });

    await waitFor(() =>
      expect(
        screen.getByText('Der Chat ist geschlossen. Das Team muss erneut Hilfe anfordern.'),
      ).toBeInTheDocument(),
    );

    // The delayed second answer arrives after the socket message, carrying
    // the pre-close window state (open, updated_at older than the close).
    await act(async () => {
      resolveSecond?.({ data: { ...teamFixture } });
    });

    expect(
      screen.getByText('Der Chat ist geschlossen. Das Team muss erneut Hilfe anfordern.'),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('Senden')).toBeDisabled();
  });

  it('does not reopen the window for a team answer older than the [coordinator_left] message', async () => {
    // The initial team fetch is still in flight when the socket closes the
    // window; its answer settles late with the stale pre-close state.
    let resolveTeam!: (data: { data: typeof teamFixture }) => void;
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.endsWith('/chat/')) {
        return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
      }
      return new Promise((res) => {
        resolveTeam = res;
      });
    });

    renderPage();
    await waitFor(() => screen.getByPlaceholderText(/Nachricht schreiben/i));

    const handlers = vi.mocked(useWebSocket).mock.calls.at(-1)![0].handlers!;
    const leftMsg = makeMessage(
      'm-left',
      '[coordinator_left] The coordinator has left the team chat.',
      null,
      'participant',
      true,
      '2026-01-01T10:06:00Z',
    );
    act(() => {
      handlers.chat_message(leftMsg, {
        type: 'chat_message',
        data: leftMsg,
        timestamp: '2026-01-01T10:06:00Z',
      } as WsMessage);
    });

    await waitFor(() =>
      expect(
        screen.getByText('Der Chat ist geschlossen. Das Team muss erneut Hilfe anfordern.'),
      ).toBeInTheDocument(),
    );

    await act(async () => {
      resolveTeam({ data: { ...teamFixture } });
    });

    expect(
      screen.getByText('Der Chat ist geschlossen. Das Team muss erneut Hilfe anfordern.'),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('Senden')).toBeDisabled();
  });

  // The help request reaches coordinators only as a run feed event
  // (feed_staff:{run_id}); the team socket this page opens (chat:{team_id})
  // never receives it. So a closed window polls the team every 15 s. Only
  // setInterval is faked, so waitFor and promises keep running for real.
  describe('closed-window polling', () => {
    const CLOSED_BANNER = 'Der Chat ist geschlossen. Das Team muss erneut Hilfe anfordern.';
    const POLL_MS = 15_000;

    beforeEach(() => {
      vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    });
    afterEach(() => {
      vi.useRealTimers();
      vi.unstubAllGlobals();
    });

    /**
     * Team GET mock answering from a mutable reply; counts the team requests.
     * `chatItems` is read live, so a test can make the history answer grow.
     */
    const mockTeamReplies = (chatItems: ChatMessage[] = []) => {
      const state = { teamCalls: 0, reply: { ...teamFixture }, chatItems };
      vi.mocked(apiClient.get).mockImplementation((url: string) => {
        if (url.endsWith('/chat/')) {
          return Promise.resolve({
            data: { items: [...state.chatItems], total: state.chatItems.length, skip: 0, limit: 100 },
          });
        }
        state.teamCalls += 1;
        return Promise.resolve({ data: { ...state.reply } });
      });
      return state;
    };

    const closeViaSocket = async () => {
      const handlers = vi.mocked(useWebSocket).mock.calls.at(-1)![0].handlers!;
      const leftMsg = makeMessage(
        'm-left',
        '[coordinator_left] The coordinator has left the team chat.',
        null,
        'participant',
        true,
        '2026-01-01T10:06:00Z',
      );
      act(() => {
        handlers.chat_message(leftMsg, {
          type: 'chat_message',
          data: leftMsg,
          timestamp: leftMsg.created_at,
        } as WsMessage);
      });
      await waitFor(() => expect(screen.getByText(CLOSED_BANNER)).toBeInTheDocument());
    };

    const tick = async () => {
      await act(async () => {
        vi.advanceTimersByTime(POLL_MS);
      });
    };

    it('does not poll while the window is open', async () => {
      const state = mockTeamReplies();
      renderPage();
      await waitFor(() =>
        expect(screen.getByRole('heading', { name: 'Chat mit Team Alpha' })).toBeInTheDocument(),
      );
      await tick();
      await tick();
      expect(state.teamCalls).toBe(1);
    });

    it('reopens the window from the poll once a new help request makes the team answer newer', async () => {
      const state = mockTeamReplies();
      renderPage();
      await waitFor(() =>
        expect(screen.getByRole('heading', { name: 'Chat mit Team Alpha' })).toBeInTheDocument(),
      );
      await closeViaSocket();

      // First tick: the server still says closed — the window stays closed.
      state.reply = { ...teamFixture, coordinator_chat_open: false, updated_at: '2026-01-01T10:06:00Z' };
      await tick();
      expect(state.teamCalls).toBe(2);
      expect(screen.getByText(CLOSED_BANNER)).toBeInTheDocument();

      // The team asks for help again: the server reopens the window and
      // bumps updated_at past the close. The next tick reopens it here.
      state.reply = { ...teamFixture, coordinator_chat_open: true, updated_at: '2026-01-01T10:08:00Z' };
      await tick();
      expect(state.teamCalls).toBe(3);
      await waitFor(() => expect(screen.queryByText(CLOSED_BANNER)).not.toBeInTheDocument());

      // A reopened window works: the composer accepts and enables sending again.
      await userEvent.type(screen.getByPlaceholderText(/Nachricht schreiben/i), 'Wieder da!');
      expect(screen.getByLabelText('Senden')).toBeEnabled();

      // Polling stops once the window is open again.
      await tick();
      await tick();
      expect(state.teamCalls).toBe(3);
    });

    it('refetches and merges the history on reopening, so a question sent before reconnection shows up exactly once', async () => {
      const old = makeMessage('m-old', 'Alte Frage', 'guest-1', 'guest', false, '2026-01-01T10:00:00Z');
      const state = mockTeamReplies([old]);
      renderPage();
      await waitFor(() => expect(screen.getByText('Alte Frage')).toBeInTheDocument());
      await closeViaSocket();

      // Between its new help request and our reconnection the team asked
      // something — the socket was down, so only the history knows it.
      state.chatItems.push(
        old,
        makeMessage('m-q', 'Wo ist Station 3?', 'guest-1', 'guest', false, '2026-01-01T10:07:00Z'),
      );
      state.reply = { ...teamFixture, coordinator_chat_open: true, updated_at: '2026-01-01T10:08:00Z' };
      await tick();

      await waitFor(() => expect(screen.getByText('Wo ist Station 3?')).toBeInTheDocument());
      // Merged by id: the old message is not duplicated by the refetch.
      expect(screen.getAllByText('Alte Frage')).toHaveLength(1);
      expect(screen.queryByText(CLOSED_BANNER)).not.toBeInTheDocument();
    });

    it('a polled answer older than the [coordinator_left] message does not reopen the window', async () => {
      const state = mockTeamReplies();
      renderPage();
      await waitFor(() =>
        expect(screen.getByRole('heading', { name: 'Chat mit Team Alpha' })).toBeInTheDocument(),
      );
      await closeViaSocket();

      // A lagging read still reports the pre-close state (open, 10:00).
      state.reply = { ...teamFixture, coordinator_chat_open: true, updated_at: '2026-01-01T10:00:00Z' };
      await tick();
      expect(state.teamCalls).toBe(2);
      expect(screen.getByText(CLOSED_BANNER)).toBeInTheDocument();
      expect(screen.getByLabelText('Senden')).toBeDisabled();
    });

    // The module-level useWebSocket mock cannot observe a refused socket —
    // this test runs the real hook against a fake WebSocket class instead.
    it('reconnects a 4003-refused socket when the poll reopens the window, and shows the refetched history', async () => {
      class FakeWebSocket {
        static readonly OPEN = 1;
        static instances: FakeWebSocket[] = [];
        url: string;
        readyState = FakeWebSocket.OPEN;
        onopen: ((ev: unknown) => void) | null = null;
        onmessage: ((ev: { data: string }) => void) | null = null;
        onclose: ((ev: { code: number; reason?: string }) => void) | null = null;
        onerror: (() => void) | null = null;
        send = vi.fn();
        close = vi.fn();
        constructor(url: string) {
          this.url = url;
          FakeWebSocket.instances.push(this);
        }
      }
      vi.stubGlobal('WebSocket', FakeWebSocket);
      vi.resetModules();
      vi.doUnmock('../../hooks/useWebSocket');
      const [{ default: Page }, { default: client }, { AuthContext: FreshAuthContext }] =
        await Promise.all([
          import('./CoordinatorTeamChat'),
          import('../../api/client'),
          import('../../auth/AuthContext'),
        ]);

      // Page loads while assistance is closed: the first history request
      // fails with CHAT_WINDOW_CLOSED and ws.py refuses the socket with 4003.
      Object.assign(client, { defaults: { baseURL: '' } }); // the real hook reads baseURL
      let chatBlocked = true;
      const teamReply = { ...teamFixture };
      // The first team answer stays pending until the socket has been
      // refused: an immediate "closed" answer would disable the hook and
      // cancel its connection before the 4003 close event lands.
      let resolveFirstTeam!: (v: { data: typeof teamFixture }) => void;
      let firstTeamCall = true;
      vi.mocked(client.get).mockImplementation((url: string) => {
        if (url.includes('/ws-token')) return Promise.resolve({ data: { token: 'tok' } });
        if (url.endsWith('/chat/')) {
          if (chatBlocked) {
            return Promise.reject(new ApiError(403, 'Chat window closed', 'CHAT_WINDOW_CLOSED'));
          }
          return Promise.resolve({
            data: {
              items: [makeMessage('m-q', 'Wir brauchen Hilfe!', 'guest-1', 'guest', false, '2026-01-01T10:07:00Z')],
              total: 1, skip: 0, limit: 100,
            },
          });
        }
        if (firstTeamCall) {
          firstTeamCall = false;
          return new Promise((res) => {
            resolveFirstTeam = res;
          });
        }
        return Promise.resolve({ data: { ...teamReply } });
      });
      Object.assign(teamReply, { coordinator_chat_open: false, updated_at: '2026-01-01T10:06:00Z' });

      render(
        <MemoryRouter initialEntries={['/runs/run-1/teams/team-1/chat']}>
          <FreshAuthContext.Provider
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
              <Route path="/runs/:runId/teams/:teamId/chat" element={<Page />} />
              <Route path="/runs/:runId" element={<div>Dashboard-Stub</div>} />
            </Routes>
          </FreshAuthContext.Provider>
        </MemoryRouter>,
      );

      // The initial connection is attempted, then refused with 4003 — a
      // close code useWebSocket never reconnects on — while it is active.
      // Fake setInterval means waitFor polls only once — flush the effect's
      // microtask chain (token fetch → construction) before that check.
      await act(async () => {});
      await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
      expect(FakeWebSocket.instances[0].url).toContain(
        '/api/orgs/test-org/runs/run-1/teams/team-1/ws',
      );
      await act(async () => {
        FakeWebSocket.instances[0].onclose?.({
          code: 4003,
          reason: 'Coordinator assist window is closed',
        });
      });
      // Only now does the pending first team answer settle as closed.
      await act(async () => {
        resolveFirstTeam({ data: { ...teamReply } });
      });
      await waitFor(() => expect(screen.getByText(CLOSED_BANNER)).toBeInTheDocument());

      // The team asks for help again — the poll reopens the window, which
      // refetches the (now unblocked) history and restarts the subscription:
      // a second WebSocket is constructed.
      chatBlocked = false;
      Object.assign(teamReply, { coordinator_chat_open: true, updated_at: '2026-01-01T10:08:00Z' });
      await tick();
      // The reopen fetches and merges the history first; only then does the
      // subscription restart — flush that chain before the single check.
      await act(async () => {});

      await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2));
      await waitFor(() => expect(screen.getByText('Wir brauchen Hilfe!')).toBeInTheDocument());
      expect(screen.queryByText(CLOSED_BANNER)).not.toBeInTheDocument();

      // The replacement connection delivers live traffic: a coordinator
      // reply arriving through the second socket is shown.
      await act(async () => {
        const ws2 = FakeWebSocket.instances[1];
        ws2.onopen?.({});
        const live = makeMessage('m-live', 'Wir kommen!', 'coord-me', 'coordinator', false, '2026-01-01T10:09:00Z');
        ws2.onmessage?.({
          data: JSON.stringify({ type: 'chat_message', data: live, timestamp: live.created_at }),
        });
      });
      await waitFor(() => expect(screen.getByText('Wir kommen!')).toBeInTheDocument());
    });

    // The subscription restarts only after the history answer has
    // merged: no second socket may exist while the refetch is in flight,
    // even though the window is already open again.
    it('enables the socket on reopening only after the history answer has merged', async () => {
      class FakeWebSocket {
        static readonly OPEN = 1;
        static instances: FakeWebSocket[] = [];
        url: string;
        readyState = FakeWebSocket.OPEN;
        onopen: ((ev: unknown) => void) | null = null;
        onmessage: ((ev: { data: string }) => void) | null = null;
        onclose: ((ev: { code: number; reason?: string }) => void) | null = null;
        onerror: (() => void) | null = null;
        send = vi.fn();
        close = vi.fn();
        constructor(url: string) {
          this.url = url;
          FakeWebSocket.instances.push(this);
        }
      }
      vi.stubGlobal('WebSocket', FakeWebSocket);
      vi.resetModules();
      vi.doUnmock('../../hooks/useWebSocket');
      const [{ default: Page }, { default: client }, { AuthContext: FreshAuthContext }] =
        await Promise.all([
          import('./CoordinatorTeamChat'),
          import('../../api/client'),
          import('../../auth/AuthContext'),
        ]);
      Object.assign(client, { defaults: { baseURL: '' } });

      const old = makeMessage('m-old', 'Alte Frage', 'guest-1', 'guest', false, '2026-01-01T10:00:00Z');
      const late = makeMessage('m-late', 'Wo ist Station 3?', 'guest-1', 'guest', false, '2026-01-01T10:07:00Z');
      // Chat call #1 is the initial load (immediate); call #2 is the reopen
      // refetch and stays pending until the test releases it.
      let chatCalls = 0;
      let resolveRefetch!: (v: { data: { items: ChatMessage[]; total?: number; skip?: number; limit?: number } }) => void;
      const teamReply = { ...teamFixture };
      vi.mocked(client.get).mockImplementation((url: string) => {
        if (url.includes('/ws-token')) return Promise.resolve({ data: { token: 'tok' } });
        if (url.endsWith('/chat/')) {
          chatCalls += 1;
          if (chatCalls === 1) {
            return Promise.resolve({ data: { items: [old], total: 1, skip: 0, limit: 100 } });
          }
          return new Promise((res) => {
            resolveRefetch = res;
          });
        }
        return Promise.resolve({ data: { ...teamReply } });
      });

      render(
        <MemoryRouter initialEntries={['/runs/run-1/teams/team-1/chat']}>
          <FreshAuthContext.Provider
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
              <Route path="/runs/:runId/teams/:teamId/chat" element={<Page />} />
              <Route path="/runs/:runId" element={<div>Dashboard-Stub</div>} />
            </Routes>
          </FreshAuthContext.Provider>
        </MemoryRouter>,
      );

      // Fake setInterval means waitFor polls only once — flush the effect's
      // microtask chain (token fetch → construction, initial history load)
      // before those single checks.
      await act(async () => {});
      await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
      await waitFor(() => expect(screen.getByText('Alte Frage')).toBeInTheDocument());

      // The socket itself closes the window with [coordinator_left].
      await act(async () => {
        FakeWebSocket.instances[0].onmessage?.({
          data: JSON.stringify({
            type: 'chat_message',
            data: makeMessage(
              'm-left',
              '[coordinator_left] The coordinator has left the team chat.',
              null,
              'participant',
              true,
              '2026-01-01T10:06:00Z',
            ),
            timestamp: '2026-01-01T10:06:00Z',
          }),
        });
      });
      await waitFor(() => expect(screen.getByText(CLOSED_BANNER)).toBeInTheDocument());

      // The poll reopens the window — the history refetch starts and hangs.
      Object.assign(teamReply, { coordinator_chat_open: true, updated_at: '2026-01-01T10:08:00Z' });
      await tick();
      await waitFor(() => expect(chatCalls).toBe(2));
      // Microtasks settle; still no replacement socket before the merge.
      await act(async () => {});
      await act(async () => {});
      expect(FakeWebSocket.instances).toHaveLength(1);

      // The history answer lands: merge, then the second connection.
      await act(async () => {
        resolveRefetch({ data: { items: [old, late], total: 2, skip: 0, limit: 100 } });
      });
      await act(async () => {});
      await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2));
      await waitFor(() => expect(screen.getByText('Wo ist Station 3?')).toBeInTheDocument());
      expect(screen.getAllByText('Alte Frage')).toHaveLength(1);
      expect(screen.queryByText(CLOSED_BANNER)).not.toBeInTheDocument();
    });

    it('re-enables the socket even when the reopening history refetch fails', async () => {
      let chatCalls = 0;
      const state = { reply: { ...teamFixture } };
      vi.mocked(apiClient.get).mockImplementation((url: string) => {
        if (url.endsWith('/chat/')) {
          chatCalls += 1;
          if (chatCalls > 1) {
            return Promise.reject(new ApiError(500, 'boom', 'INTERNAL'));
          }
          return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
        }
        return Promise.resolve({ data: { ...state.reply } });
      });

      renderPage();
      await waitFor(() =>
        expect(screen.getByRole('heading', { name: 'Chat mit Team Alpha' })).toBeInTheDocument(),
      );
      await closeViaSocket();
      // The last hook call has the socket disabled while the window is closed.
      expect(vi.mocked(useWebSocket).mock.calls.at(-1)![0].enabled).toBe(false);

      state.reply = { ...teamFixture, coordinator_chat_open: true, updated_at: '2026-01-01T10:08:00Z' };
      await tick();
      await waitFor(() => expect(chatCalls).toBe(2));
      // The refetch failed, but the socket must not stay dead.
      await waitFor(() =>
        expect(vi.mocked(useWebSocket).mock.calls.at(-1)![0].enabled).toBe(true),
      );
      await waitFor(() => expect(screen.queryByText(CLOSED_BANNER)).not.toBeInTheDocument());
    });

    it('stops polling when the page unmounts', async () => {
      const state = mockTeamReplies();
      const { unmount } = renderPage();
      await waitFor(() =>
        expect(screen.getByRole('heading', { name: 'Chat mit Team Alpha' })).toBeInTheDocument(),
      );
      await closeViaSocket();
      state.reply = { ...teamFixture, coordinator_chat_open: false, updated_at: '2026-01-01T10:06:00Z' };
      await tick();
      expect(state.teamCalls).toBe(2);

      unmount();
      await tick();
      await tick();
      expect(state.teamCalls).toBe(2);
    });
  });
});
