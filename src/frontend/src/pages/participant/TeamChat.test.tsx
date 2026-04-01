import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Outlet, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import { ThemeProvider } from '@mui/material/styles';
import TeamChat from './TeamChat';
import theme from '../../theme';
import { AuthContext } from '../../auth/AuthContext';
import apiClient from '../../api/client';
import { useWebSocket } from '../../hooks/useWebSocket';
import type { WsMessage } from '../../hooks/useWebSocket';
import type { ChatMessage, Team } from '../../api/types';

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
  id: 'user-me',
  email: 'participant@example.com',
  display_name: 'Ich',
  role: 'participant' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const makeMessage = (
  id: string,
  content: string,
  authorId: string | null,
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
  author_role: isSystem ? null : 'participant',
  author_display_name: isSystem ? null : authorDisplayName,
  created_at: createdAt,
});

const makeTeam = (id = 'team-1'): Team => ({
  id,
  organization_id: 'org-1',
  run_id: 'run-1',
  name: 'Die Fugger',
  color: null,
  member_count: 2,
  share_code: 'ABC123',
  coordinator_chat_open: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
});

// The team arrives through the outlet (RunShell → TeamTab), never through
// sessionStorage. `team: null` is the "no team" case.
const chatTree = (
  ctxTeam: Team | null,
  run: { run_id: string; state: string } | null = null,
  membership?: 'unknown' | 'present' | 'none',
) => (
  <ThemeProvider theme={theme}>
    <MemoryRouter initialEntries={['/runs/run-1/participant/chat']}>
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
          <Route
            path="/runs/:runId/participant"
            element={<Outlet context={{ team: ctxTeam, run, membership }} />}
          >
            <Route path="chat" element={<TeamChat />} />
          </Route>
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>
  </ThemeProvider>
);

// Wrapped in the real ThemeProvider: the contrast assertion below is only
// meaningful when the palette the component reads is the shipped one.
const renderChat = (ctxTeam: Team | null = makeTeam('team-1')) => render(chatTree(ctxTeam));

/**
 * Same tree, but with a run supplied — the real shape
 * (RunShell → TeamTab → TeamChat). `renderChat` above deliberately passes no
 * run, which is the "state unknown" case.
 */
const renderChatInRun = (state: string) => render(chatTree(makeTeam('team-1'), { run_id: 'run-1', state }));

describe('TeamChat', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    sessionStorage.clear();
    vi.mocked(useWebSocket).mockReturnValue({ status: 'open', lastError: null, send: vi.fn() });
  });

  // The shell is still asking the server for the team of a run page opened without
  // the stored key: "no team" is not the answer yet.
  it('waits instead of saying "no team" while the shell asks the server', () => {
    render(chatTree(null, null, 'unknown'));

    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.queryByText(/keinem Team zugeordnet/i)).not.toBeInTheDocument();
  });

  it('says "no team" once the shell found no membership', () => {
    render(chatTree(null, null, 'none'));

    expect(screen.getByText(/keinem Team zugeordnet/i)).toBeInTheDocument();
  });

  it('shows the "no team" fallback when the outlet supplies no team', () => {
    renderChat(null);
    expect(
      screen.getByText(/keinem Team zugeordnet/i),
    ).toBeInTheDocument();
  });

  it('with a team: fetches messages and renders oldest-first', async () => {
    // The endpoint returns oldest-first (created_at ASC) — mirror that here,
    // or the fixture asserts against an ordering the API never produces.
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [
          makeMessage('m1', 'Erste', 'other-user', false, '2026-01-01T10:00:00Z'),
          makeMessage('m2', 'Zweite', 'other-user', false, '2026-01-01T10:01:00Z'),
        ],
        total: 2,
        skip: 0,
        limit: 100,
      },
    });

    renderChat();

    await waitFor(() => {
      expect(screen.getByText('Erste')).toBeInTheDocument();
    });
    expect(screen.getByText('Zweite')).toBeInTheDocument();

    // Oldest-first order: Erste should appear before Zweite in the DOM
    const items = screen.getAllByText(/^(Erste|Zweite)$/);
    expect(items[0]).toHaveTextContent('Erste');
    expect(items[1]).toHaveTextContent('Zweite');
  });

  // The axe rule scrollable-region-focusable: a scrollable region needs keyboard access and a name.
  it('lets the message list take the keyboard and names it, keeping the role log', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [makeMessage('m1', 'Erste', 'other-user', false, '2026-01-01T10:00:00Z')], total: 1, skip: 0, limit: 100 },
    });
    renderChat();
    await screen.findByText('Erste');
    const log = screen.getByRole('log', { name: 'Teamchat' });
    expect(log).toHaveAttribute('tabindex', '0');
    expect(log).toHaveAttribute('aria-live', 'polite');
  });

  // The chat has no height of its own: it is a flex child that takes what the shell
  // and the team tab leave, the list is the part that shrinks and scrolls, and the
  // input row follows it. A fixed height would put the input below the bottom
  // navigation. jsdom lays nothing out. The measured geometry is in
  // tests/e2e/participant-chat-layout.spec.ts.
  it('takes its height from the layout: a flex child whose list shrinks and scrolls', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [makeMessage('m1', 'Hallo', 'user-other')], total: 1, skip: 0, limit: 100 },
    });
    renderChat();
    await screen.findByText('Hallo');

    const list = screen.getByRole('log');
    const chat = list.parentElement!;
    expect(chat).toHaveStyle({ display: 'flex', flexDirection: 'column', flexGrow: '1', minHeight: '0' });
    expect(chat.style.height).toBe('');
    expect(getComputedStyle(chat).height).not.toMatch(/calc|dvh|px/);

    expect(list).toHaveStyle({ flexGrow: '1', minHeight: '0', overflowY: 'auto' });

    // The input row comes after the list inside the same column.
    const input = screen.getByRole('textbox', { name: 'Nachricht eingeben' });
    expect(chat).toContainElement(input);
    expect(list.compareDocumentPosition(input) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(list).not.toContainElement(input);
  });

  it('renders the author_display_name as the sender label when present', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [makeMessage('m1', 'Hallo Team', 'other-user', false, '2026-01-01T10:00:00Z', 'Erika Musterfrau')],
        total: 1,
        skip: 0,
        limit: 100,
      },
    });

    renderChat();

    await waitFor(() => {
      expect(screen.getByText('Erika Musterfrau')).toBeInTheDocument();
    });
    // The raw UUID fragment must never leak into the UI.
    expect(screen.queryByText('other-user'.slice(0, 6))).not.toBeInTheDocument();
  });

  it('falls back to a neutral label when author_display_name is missing', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [makeMessage('m1', 'Anonyme Nachricht', 'other-user')] },
      total: 1,
      skip: 0,
      limit: 100,
    });

    renderChat();

    await waitFor(() => {
      expect(screen.getByText('Anonyme Nachricht')).toBeInTheDocument();
    });
    expect(screen.getByText('Teammitglied')).toBeInTheDocument();
    expect(screen.queryByText('other-user'.slice(0, 6))).not.toBeInTheDocument();
  });

  it('Enter key posts a message via apiClient.post', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [], total: 0, skip: 0, limit: 100 },
    });
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: makeMessage('m-new', 'Hallo', 'user-me') });

    renderChat();

    await waitFor(() => screen.getByPlaceholderText(/Nachricht schreiben/i));
    const input = screen.getByPlaceholderText(/Nachricht schreiben/i);
    await userEvent.type(input, 'Hallo{Enter}');

    await waitFor(() => {
      expect(apiClient.post).toHaveBeenCalledWith(
        '/api/orgs/test-org/runs/run-1/teams/team-1/chat/',
        { content: 'Hallo' },
      );
    });
  });

  it('receiving a chat_message WS event appends message to list', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [], total: 0, skip: 0, limit: 100 },
    });

    renderChat();
    await waitFor(() => screen.getByPlaceholderText(/Nachricht schreiben/i));

    const mockedUseWs = vi.mocked(useWebSocket);
    const handlers = mockedUseWs.mock.calls.at(-1)![0].handlers!;

    const fakeMsg = makeMessage('ws-msg', 'Nachricht via WS', 'other-user');
    act(() => {
      handlers.chat_message(fakeMsg, {
        type: 'chat_message',
        data: fakeMsg,
        timestamp: '2026-01-01T10:05:00Z',
      } as WsMessage);
    });

    await waitFor(() => {
      expect(screen.getByText('Nachricht via WS')).toBeInTheDocument();
    });
  });

  it('own message renders as right-aligned bubble; system message is italic divider', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [
          // Oldest-first, as the endpoint returns them.
          makeMessage('m1', 'Meine Nachricht', 'user-me'),
          makeMessage('m2', 'System-Info', null, true),
        ],
        total: 2,
        skip: 0,
        limit: 100,
      },
    });

    renderChat();

    await waitFor(() => {
      expect(screen.getByText('Meine Nachricht')).toBeInTheDocument();
    });

    // Alignment and colour replace the "Du" sender label, so the test asserts that
    // the bubble parent uses flex-end (right-aligned). Walk up until a flex container
    // is found. Its alignItems should be flex-end.
    const ownText = screen.getByText('Meine Nachricht');
    let node: HTMLElement | null = ownText;
    let foundRightAlignedFlex = false;
    while (node && node !== document.body) {
      const cs = window.getComputedStyle(node);
      if (cs.display === 'flex' && cs.alignItems === 'flex-end') {
        foundRightAlignedFlex = true;
        break;
      }
      node = node.parentElement;
    }
    expect(foundRightAlignedFlex).toBe(true);

    // System messages: rendered as italic divider with the content text.
    const systemText = screen.getByText('System-Info');
    expect(systemText).toBeInTheDocument();
    expect(window.getComputedStyle(systemText).fontStyle).toBe('italic');
  });

  it('char counter reflects draft length', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [], total: 0, skip: 0, limit: 100 },
    });

    renderChat();
    await waitFor(() => screen.getByPlaceholderText(/Nachricht schreiben/i));

    // Initially 0/2000
    expect(screen.getByText('0/2000')).toBeInTheDocument();

    await userEvent.type(screen.getByPlaceholderText(/Nachricht schreiben/i), 'Hallo');
    expect(screen.getByText('5/2000')).toBeInTheDocument();
  });

  // Writing must close with the run, and say so. Otherwise the composer stays open
  // and the server answers each refused message with an English string in an
  // otherwise German app.
  it.each([
    ['ended', /Lauf ist beendet/i],
    ['paused', /Lauf ist pausiert/i],
    ['scheduled', /noch nicht begonnen/i],
  ])('closes the composer while the run is %s', async (state, expected) => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [], total: 0, skip: 0, limit: 100 },
    });

    renderChatInRun(state);

    expect(await screen.findByText(expected)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Nachricht schreiben/i)).not.toBeInTheDocument();
  });

  it('keeps the composer open while the run is active', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [], total: 0, skip: 0, limit: 100 },
    });

    renderChatInRun('active');

    expect(await screen.findByPlaceholderText(/Nachricht schreiben/i)).toBeInTheDocument();
  });

  it('keeps the composer open while the run state is still unknown', async () => {
    // `run` is null until the state loads; flashing "closed" at someone who can
    // in fact write would be its own bug.
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [], total: 0, skip: 0, limit: 100 },
    });

    renderChat();

    expect(await screen.findByPlaceholderText(/Nachricht schreiben/i)).toBeInTheDocument();
  });

  it('has no axe accessibility violations on initial render', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [], total: 0, skip: 0, limit: 100 },
    });

    const { container } = renderChat();
    await waitFor(() => screen.getByPlaceholderText(/Nachricht schreiben/i));
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  // The "call coordination" action is a medium outlined button at the theme button
  // size, not a 13 px `warning` label.
  it('has no color-contrast violations on the coordinator-request button', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [], total: 0, skip: 0, limit: 100 },
    });

    const { container } = renderChat();
    await waitFor(() => screen.getByPlaceholderText(/Nachricht schreiben/i));

    // The two assertions that actually measure: label size and label colour. Under
    // jsdom the axe call below cannot fail on `color-contrast`: elements have no
    // layout, so axe files the rule under `incomplete`, not `violations`.
    const callButton = screen.getByRole('button', { name: /hilfe anfordern/i });
    const style = window.getComputedStyle(callButton);
    expect(parseFloat(style.fontSize)).toBeGreaterThanOrEqual(14);
    expect(style.color).toBe('rgb(29, 27, 32)'); // palette.text.primary

    const results = await axe(container, { runOnly: ['color-contrast'] });
    expect(results).toHaveNoViolations();
  });

  // When the shell switches the outlet team (its reaction to team_member_moved for
  // this user), the chat follows: messages are refetched and the WS reconnects to
  // the new team's channel, all without a reload.
  it('refetches and reconnects when the outlet team switches', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [], total: 0, skip: 0, limit: 100 },
    });

    const view = renderChat(makeTeam('team-1'));
    await screen.findByPlaceholderText(/Nachricht schreiben/i);
    expect(apiClient.get).toHaveBeenCalledWith(
      '/api/orgs/test-org/runs/run-1/teams/team-1/chat/',
      { params: { latest: true } },
    );

    view.rerender(chatTree(makeTeam('team-2')));

    await waitFor(() => {
      expect(apiClient.get).toHaveBeenCalledWith(
        '/api/orgs/test-org/runs/run-1/teams/team-2/chat/',
        { params: { latest: true } },
      );
    });
    expect(vi.mocked(useWebSocket).mock.calls.at(-1)![0].path).toBe(
      '/api/orgs/test-org/runs/run-1/teams/team-2/ws',
    );
  });

  // `load()` must not write a response for another team. A late response for the
  // old team, arriving after the switch, would restore the old chat on top of the
  // new one. The fetch effect marks its request stale in its cleanup and drops a
  // response that arrives after that.
  it('drops a slow response for the old team that arrives after the team changed', async () => {
    let resolveTeam1: ((value: { data: unknown }) => void) | undefined;
    const team1Response = new Promise((resolve) => {
      resolveTeam1 = resolve;
    });

    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.endsWith('/teams/team-1/chat/')) return team1Response as Promise<{ data: unknown }>;
      if (url.endsWith('/teams/team-2/chat/')) {
        return Promise.resolve({
          data: {
            items: [makeMessage('m2', 'Team-zwei-Nachricht', 'other-user')],
            total: 1,
            skip: 0,
            limit: 100,
          },
        });
      }
      return Promise.reject(new Error('unexpected url'));
    });

    const view = renderChat(makeTeam('team-1'));
    await waitFor(() => {
      expect(apiClient.get).toHaveBeenCalledWith(
        '/api/orgs/test-org/runs/run-1/teams/team-1/chat/',
        { params: { latest: true } },
      );
    });

    // Switch teams before the slow team-1 response ever arrives.
    view.rerender(chatTree(makeTeam('team-2')));
    await waitFor(() => {
      expect(screen.getByText('Team-zwei-Nachricht')).toBeInTheDocument();
    });

    // The stale response now resolves. It must not repaint the old team's
    // message over the new team's chat.
    await act(async () => {
      resolveTeam1!({
        data: {
          items: [makeMessage('m1', 'Alte-Team-Nachricht', 'other-user')],
          total: 1,
          skip: 0,
          limit: 100,
        },
      });
      await Promise.resolve();
    });

    expect(screen.queryByText('Alte-Team-Nachricht')).not.toBeInTheDocument();
    expect(screen.getByText('Team-zwei-Nachricht')).toBeInTheDocument();
  });

  // After "Bestätigen" the focus stands on the button that now reads
  // "Hilfeanfrage läuft". A `disabled` button cannot hold the focus, so the button
  // is inert through `aria-disabled` and stays focusable.
  describe('focus after a help request', () => {
    const REQUEST_URL = '/api/orgs/test-org/runs/run-1/teams/team-1/chat/request-coordinator';

    beforeEach(() => {
      vi.mocked(apiClient.get).mockResolvedValue({ data: { items: [], total: 0, skip: 0, limit: 100 } });
    });

    it('puts the focus on the button that reads "Hilfeanfrage läuft"', async () => {
      vi.mocked(apiClient.post).mockResolvedValueOnce({ data: {} });
      renderChat();

      await userEvent.click(await screen.findByRole('button', { name: 'Hilfe anfordern' }));
      await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Bestätigen' }));

      const pending = await screen.findByRole('button', { name: 'Hilfeanfrage läuft' });
      expect(apiClient.post).toHaveBeenCalledWith(REQUEST_URL);
      await waitFor(() => expect(pending).toHaveFocus());
      expect(document.body).not.toHaveFocus();

      // Inert, not disabled: Enter on the focused button opens no second dialog.
      expect(pending).toHaveAttribute('aria-disabled', 'true');
      expect(pending).not.toBeDisabled();
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      await userEvent.keyboard('{Enter}');
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(apiClient.post).toHaveBeenCalledTimes(1);
    });

    it('holds the focus on the button while the request is in flight', async () => {
      let resolveRequest: (value: unknown) => void;
      vi.mocked(apiClient.post).mockReturnValueOnce(new Promise((resolve) => (resolveRequest = resolve)));
      renderChat();

      await userEvent.click(await screen.findByRole('button', { name: 'Hilfe anfordern' }));
      await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Bestätigen' }));

      // Not yet answered: the label is the old one, the button is inert and
      // has the focus. `hidden`: the closing dialog still hides the page.
      const button = screen.getByRole('button', { name: 'Hilfe anfordern', hidden: true });
      await waitFor(() => expect(button).toHaveFocus());
      expect(button).toHaveAttribute('aria-disabled', 'true');

      await act(async () => {
        resolveRequest!({ data: {} });
        await Promise.resolve();
      });
      expect(await screen.findByRole('button', { name: 'Hilfeanfrage läuft' })).toHaveFocus();
    });

    it('puts the focus on the button where the click that opened the dialog did not focus it', async () => {
      vi.mocked(apiClient.post).mockResolvedValueOnce({ data: {} });
      renderChat();

      // fireEvent.click moves no focus, as a click on a button in Safari: the
      // dialog then hands the focus back to the page's root, and the focus
      // would be lost when the dialog leaves the page.
      fireEvent.click(await screen.findByRole('button', { name: 'Hilfe anfordern' }));
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Bestätigen' }));

      const pending = await screen.findByRole('button', { name: 'Hilfeanfrage läuft' });
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      expect(pending).toHaveFocus();
    });

    it('leaves the focus where the person moved it while the request was in flight', async () => {
      let resolveRequest: (value: unknown) => void;
      vi.mocked(apiClient.post).mockReturnValueOnce(new Promise((resolve) => (resolveRequest = resolve)));
      renderChat();

      await userEvent.click(await screen.findByRole('button', { name: 'Hilfe anfordern' }));
      await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Bestätigen' }));
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      const field = screen.getByPlaceholderText(/Nachricht schreiben/i);
      await userEvent.click(field);

      await act(async () => {
        resolveRequest!({ data: {} });
        await Promise.resolve();
      });
      await screen.findByRole('button', { name: 'Hilfeanfrage läuft' });
      expect(field).toHaveFocus();
    });

    it('keeps the button usable and focused when the request fails', async () => {
      vi.mocked(apiClient.post).mockRejectedValueOnce(new Error('network'));
      renderChat();

      await userEvent.click(await screen.findByRole('button', { name: 'Hilfe anfordern' }));
      await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Bestätigen' }));

      const button = screen.getByRole('button', { name: 'Hilfe anfordern', hidden: true });
      await waitFor(() => expect(button).not.toHaveAttribute('aria-disabled'));
      expect(button).toHaveFocus();
    });

    it('shows a live request as an inert, focusable button after a reload', async () => {
      renderChat({ ...makeTeam('team-1'), coordinator_chat_open: true });

      const pending = await screen.findByRole('button', { name: 'Hilfeanfrage läuft' });
      expect(pending).toHaveAttribute('aria-disabled', 'true');
      expect(pending).not.toBeDisabled();
      pending.focus();
      expect(pending).toHaveFocus();
      await userEvent.keyboard('{Enter}');
      fireEvent.click(pending);
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
  });
});
