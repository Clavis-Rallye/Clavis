import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route, Outlet } from 'react-router';
import ChallengeView from './ChallengeView';
import { AuthContext } from '../../auth/AuthContext';
import apiClient, { ApiError } from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

// The real Text form inside the real task page, because the error is spoken by
// the form and by the page together. A unit test cannot hear, so these tests check
// the structure that would make a screen reader speak twice: the error text has one
// announcing path, the focus is not on BODY, and the answer stays in the form.
// Only a failed send heard with a screen reader (NVDA) proves it.
const authUser = {
  id: 'u1',
  email: 'participant@example.com',
  display_name: 'Teilnehmer',
  role: 'participant' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const challenge = {
  id: 'ch-1',
  organization_id: 'org-1',
  run_id: 'run-1',
  run_station_id: 's-1',
  source_challenge_id: null,
  challenge_type: 'text',
  title: { de: 'Rätselaufgabe' },
  instructions: { de: 'Löse das Rätsel.' },
  scoring_rules: { max_points: 10 },
  sort_order: 0,
};

const teammateAnswer = {
  id: 'sub-1',
  organization_id: 'org-1',
  run_id: 'run-1',
  team_id: 'team-1',
  run_challenge_id: 'ch-1',
  content: { text: 'Antwort des Teams' },
  submitted_at: '2026-01-01T00:00:00Z',
  is_scored: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const page = (items: unknown[]) => ({ data: { items, total: items.length, skip: 0, limit: 100 } });

const mockLoad = () => {
  vi.mocked(apiClient.get)
    .mockResolvedValueOnce(page([challenge]))
    .mockResolvedValueOnce(page([]))
    // Anything later, such as the re-read after a refusal: still no answer.
    .mockResolvedValue(page([]));
};

const renderView = () =>
  render(
    <MemoryRouter initialEntries={['/runs/run-1/participant/stations/s-1/challenges/ch-1']}>
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
          <Route path="/runs/:runId/participant" element={<Outlet context={{ run: null }} />}>
            <Route path="stations/:stationId/challenges/:challengeId" element={<ChallengeView />} />
          </Route>
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

const GENERIC = 'Die Antwort konnte nicht gesendet werden. Bitte versuche es erneut.';
const SEND = { name: 'Antwort senden' };
const NOTICE = 'Deine Antwort wurde nicht gesendet';

// The live-region selector that the failed-send suites share.
const LIVE = '[role="alert"], [role="status"], [role="log"], [aria-live]';

const liveHolders = (text: string) =>
  Array.from(document.querySelectorAll(LIVE)).filter(
    (el) => el.getAttribute('aria-live') !== 'off' && (el.textContent ?? '').includes(text),
  );

const expectSpokenOnce = (text: string) => {
  const focused = document.activeElement as HTMLElement;
  // The focus does not rest on BODY.
  expect(focused).not.toBe(document.body);
  const live = liveHolders(text);
  if ((focused.textContent ?? '').includes(text)) {
    // Focus path: no live element holds the same text.
    expect(live).toHaveLength(0);
  } else {
    // Live path: one live element holds it, and the focus is neither in nor around it.
    expect(live).toHaveLength(1);
    expect(focused.contains(live[0])).toBe(false);
    expect(live[0].contains(focused)).toBe(false);
  }
};

const states: Array<{ name: string; error: () => unknown; text: string }> = [
  { name: 'no answer from the server (status 0)', error: () => new ApiError(0, 'Network Error'), text: GENERIC },
  {
    name: '4xx RUN_PAUSED',
    error: () => new ApiError(409, 'paused', 'RUN_PAUSED'),
    text: 'Der Lauf ist gerade pausiert. Bitte sendet eure Antwort, sobald es weitergeht.',
  },
  {
    name: '4xx RUN_ENDED',
    error: () => new ApiError(409, 'ended', 'RUN_ENDED'),
    text: 'Der Lauf ist beendet. Es können keine Antworten mehr gesendet werden.',
  },
  { name: 'uncoded 422', error: () => new ApiError(422, 'Antwort ungültig'), text: 'Antwort ungültig' },
  { name: '5xx', error: () => new ApiError(503, 'Service Unavailable'), text: GENERIC },
  { name: 'an error that is not an ApiError', error: () => new Error('boom'), text: 'Error: boom' },
];

// The refusal after which a teammate's answer is shown instead of the form.
const TEAMMATE_REFUSAL = () => new ApiError(422, 'dup', 'TEAM_ALREADY_SUBMITTED');

const fill = async () => {
  await userEvent.type(await screen.findByRole('textbox', { name: 'Deine Antwort' }), 'Mein Text');
};
const expectAnswerStays = () => {
  expect(screen.getByRole('textbox', { name: 'Deine Antwort' })).toHaveValue('Mein Text');
};

describe('Text task: a failed send', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it.each(states)('$name: spoken once, focus not on BODY, answer stays', async ({ error, text }) => {
    mockLoad();
    vi.mocked(apiClient.post).mockRejectedValueOnce(error());
    renderView();

    await fill();
    await userEvent.click(await screen.findByRole('button', SEND));

    await screen.findAllByText(text);
    await screen.findByRole('button', SEND);
    expectSpokenOnce(text);
    expectAnswerStays();
    // A retry needs no new input.
    expect(screen.getByRole('button', SEND)).toBeEnabled();
  });

  // The view reloads and shows the team's answer with the person's own input.
  // The form is gone then, so the focus lands on the notice.
  it('TEAM_ALREADY_SUBMITTED: the team answer and the own input show, spoken once, focus not on BODY', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(page([challenge]))
      .mockResolvedValueOnce(page([]))
      .mockResolvedValue(page([teammateAnswer]));
    vi.mocked(apiClient.post).mockRejectedValueOnce(TEAMMATE_REFUSAL());
    renderView();

    await fill();
    await userEvent.click(await screen.findByRole('button', SEND));

    await screen.findByText(NOTICE);
    await waitFor(() => expect(screen.queryByRole('button', SEND)).not.toBeInTheDocument());
    expect(screen.getByText('Antwort des Teams')).toBeInTheDocument();
    expectSpokenOnce(NOTICE);
  });


  it('keeps the focus on the send button during the request, and a second press or Enter sends nothing', async () => {
    mockLoad();
    let reject!: (e: unknown) => void;
    vi.mocked(apiClient.post).mockReturnValueOnce(
      new Promise((_, r) => {
        reject = r;
      }) as never,
    );
    renderView();

    await fill();
    await userEvent.click(await screen.findByRole('button', SEND));
    const pending = await screen.findByRole('button', { name: 'Wird gesendet…' });
    expect(document.activeElement).not.toBe(document.body);
    // The button is not natively disabled, so the focus stays on it.
    expect(pending).not.toBeDisabled();
    expect(pending).toHaveAttribute('aria-disabled', 'true');
    expect(pending).toHaveFocus();

    await userEvent.click(pending);
    await userEvent.keyboard('{Enter}');
    expect(apiClient.post).toHaveBeenCalledTimes(1);
    expect(pending).toHaveFocus();

    await act(async () => reject(new ApiError(0, 'Network Error')));
    await screen.findAllByText(GENERIC);
    expectSpokenOnce(GENERIC);
  });

  it('refuses a press between the result and the next render', async () => {
    mockLoad();
    let reject!: (e: unknown) => void;
    vi.mocked(apiClient.post).mockReturnValueOnce(
      new Promise((_, r) => {
        reject = r;
      }) as never,
    );
    renderView();

    await fill();
    await userEvent.click(await screen.findByRole('button', SEND));
    const pending = await screen.findByRole('button', { name: 'Wird gesendet…' });

    // The rejection has been handled, React has not rendered the result yet.
    reject(new ApiError(0, 'Network Error'));
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
    fireEvent.click(pending);

    expect(apiClient.post).toHaveBeenCalledTimes(1);
    await screen.findAllByText(GENERIC);
  });

  // The success has one path, the focus, on every send of one visit. A person
  // sends, changes the answer and sends again without leaving.
  it('moves the focus to "Antwort gesendet" after each of two sends in one visit', async () => {
    mockLoad();
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { ...teammateAnswer, content: { text: 'Mein Text' } } });
    vi.mocked(apiClient.put).mockResolvedValueOnce({ data: { ...teammateAnswer, content: { text: 'Mein Text 2' } } });
    renderView();

    const expectSentAndFocused = () => {
      const region = screen.getByTestId('submit-announcement');
      expect(region).toHaveTextContent('Antwort gesendet');
      expect(region).toHaveFocus();
      expect(document.activeElement).not.toBe(document.body);
      // The region has no live role, so the focus is its one path.
      expect(liveHolders('Antwort gesendet')).toHaveLength(0);
    };

    await fill();
    await userEvent.click(await screen.findByRole('button', SEND));
    await waitFor(() => expect(screen.getByTestId('submit-announcement')).toHaveFocus());
    expectSentAndFocused();

    await userEvent.click(await screen.findByRole('button', { name: 'Antwort ändern' }));
    // The focus has left the region.
    expect(screen.getByTestId('submit-announcement')).not.toHaveFocus();
    await userEvent.type(await screen.findByRole('textbox', { name: 'Deine Antwort' }), ' 2');
    await userEvent.click(screen.getByRole('button', SEND));

    await waitFor(() => expect(screen.getByTestId('submit-announcement')).toHaveFocus());
    expectSentAndFocused();
    expect(apiClient.put).toHaveBeenCalledTimes(1);
  });

  // The refusal has one path at every moment, also while the view reloads.
  describe('TEAM_ALREADY_SUBMITTED while the reload runs', () => {
    const REFUSAL = 'Ein anderes Teammitglied hat diese Aufgabe bereits beantwortet';

    it('holds no live element with the refusal while the reload is pending, then the notice has the focus', async () => {
      let resolveReload!: (v: unknown) => void;
      vi.mocked(apiClient.get)
        .mockResolvedValueOnce(page([challenge]))
        .mockResolvedValueOnce(page([]))
        // The stations of the load.
        .mockResolvedValueOnce(page([]))
        .mockReturnValueOnce(
          new Promise((r) => {
            resolveReload = r;
          }) as never,
        )
        .mockResolvedValue(page([teammateAnswer]));
      vi.mocked(apiClient.post).mockRejectedValueOnce(TEAMMATE_REFUSAL());
      renderView();

      await fill();
      await userEvent.click(await screen.findByRole('button', SEND));
      // The re-read is on its way.
      await waitFor(() => expect(apiClient.get).toHaveBeenCalledTimes(4));
      for (let i = 0; i < 10; i += 1) await act(async () => Promise.resolve());

      expect(liveHolders(REFUSAL)).toHaveLength(0);
      expect(screen.queryByText(new RegExp(REFUSAL))).not.toBeInTheDocument();
      expect(document.activeElement).not.toBe(document.body);
      // A press during the reload sends nothing.
      fireEvent.click(screen.getByRole('button', { name: 'Wird gesendet…' }));
      expect(apiClient.post).toHaveBeenCalledTimes(1);

      await act(async () => resolveReload(page([teammateAnswer])));
      await screen.findByText(NOTICE);
      await waitFor(() => expect(screen.queryByRole('button', SEND)).not.toBeInTheDocument());
      expectSpokenOnce(NOTICE);
      expect(liveHolders(REFUSAL)).toHaveLength(0);
      expect(screen.getByText('Antwort des Teams')).toBeInTheDocument();
    });

    it('shows the refusal as the one live path when the reload fails, and the answer stays', async () => {
      vi.mocked(apiClient.get)
        .mockResolvedValueOnce(page([challenge]))
        .mockResolvedValueOnce(page([]))
        // The stations of the load, then the failed re-read.
        .mockResolvedValueOnce(page([]))
        .mockRejectedValueOnce(new ApiError(0, 'Network Error'))
        .mockResolvedValue(page([]));
      vi.mocked(apiClient.post).mockRejectedValueOnce(TEAMMATE_REFUSAL());
      renderView();

      await fill();
      await userEvent.click(await screen.findByRole('button', SEND));

      await screen.findAllByText(new RegExp(REFUSAL));
      await screen.findByRole('button', SEND);
      expectSpokenOnce(REFUSAL);
      expectAnswerStays();
      expect(screen.getByRole('button', SEND)).toBeEnabled();
    });

    it('shows the refusal as the one live path when the reload succeeds but shows no answer for this task', async () => {
      vi.mocked(apiClient.get)
        .mockResolvedValueOnce(page([challenge]))
        .mockResolvedValueOnce(page([]))
        .mockResolvedValueOnce(page([]))
        // The re-read answers, with an answer of another task only.
        .mockResolvedValueOnce(page([{ ...teammateAnswer, run_challenge_id: 'ch-other' }]))
        .mockResolvedValue(page([]));
      vi.mocked(apiClient.post).mockRejectedValueOnce(TEAMMATE_REFUSAL());
      renderView();

      await fill();
      await userEvent.click(await screen.findByRole('button', SEND));

      await screen.findAllByText(new RegExp(REFUSAL));
      await screen.findByRole('button', SEND);
      expectSpokenOnce(REFUSAL);
      expectAnswerStays();
      expect(screen.getByRole('button', SEND)).toBeEnabled();
    });

    // A stalled connection: the API client sets no timeout, so the re-read can
    // hang for good. The wait is bounded, or the refusal is never spoken.
    describe('a reload that never settles', () => {
      const flush = async () => {
        for (let i = 0; i < 10; i += 1) await act(async () => Promise.resolve());
      };

      it('speaks the refusal once after 5 seconds, frees the button and keeps the answer', async () => {
        vi.useFakeTimers();
        try {
          vi.mocked(apiClient.get)
            .mockResolvedValueOnce(page([challenge]))
            .mockResolvedValueOnce(page([]))
            .mockResolvedValueOnce(page([]))
            .mockReturnValueOnce(new Promise(() => {}) as never)
            .mockResolvedValue(page([]));
          vi.mocked(apiClient.post).mockRejectedValueOnce(TEAMMATE_REFUSAL());
          renderView();
          await flush();

          fireEvent.change(screen.getByRole('textbox', { name: 'Deine Antwort' }), {
            target: { value: 'Mein Text' },
          });
          fireEvent.click(screen.getByRole('button', SEND));
          await flush();

          await act(async () => {
            await vi.advanceTimersByTimeAsync(4_900);
          });
          expect(liveHolders(REFUSAL)).toHaveLength(0);
          expect(screen.queryByText(new RegExp(REFUSAL))).not.toBeInTheDocument();
          expect(document.activeElement).not.toBe(document.body);
          // A press before the 5 seconds sends nothing.
          fireEvent.click(screen.getByRole('button', { name: 'Wird gesendet…' }));
          expect(apiClient.post).toHaveBeenCalledTimes(1);

          await act(async () => {
            await vi.advanceTimersByTimeAsync(200);
          });
          expect(screen.getAllByText(new RegExp(REFUSAL)).length).toBeGreaterThan(0);
          expectSpokenOnce(REFUSAL);
          const button = screen.getByRole('button', SEND);
          expect(button).not.toHaveAttribute('aria-disabled');
          expect(button).not.toHaveAttribute('aria-busy');
          expectAnswerStays();
          // The button is free: a press sends again.
          vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(0, 'Network Error'));
          fireEvent.click(button);
          await flush();
          expect(apiClient.post).toHaveBeenCalledTimes(2);
        } finally {
          vi.useRealTimers();
        }
      });

      it('lets a late reload that shows the answer bring the notice with the focus', async () => {
        vi.useFakeTimers();
        try {
          let resolveReload!: (v: unknown) => void;
          vi.mocked(apiClient.get)
            .mockResolvedValueOnce(page([challenge]))
            .mockResolvedValueOnce(page([]))
            .mockResolvedValueOnce(page([]))
            .mockReturnValueOnce(
              new Promise((r) => {
                resolveReload = r;
              }) as never,
            )
            .mockResolvedValue(page([teammateAnswer]));
          vi.mocked(apiClient.post).mockRejectedValueOnce(TEAMMATE_REFUSAL());
          renderView();
          await flush();

          fireEvent.change(screen.getByRole('textbox', { name: 'Deine Antwort' }), {
            target: { value: 'Mein Text' },
          });
          fireEvent.click(screen.getByRole('button', SEND));
          await flush();
          await act(async () => {
            await vi.advanceTimersByTimeAsync(5_100);
          });
          expectSpokenOnce(REFUSAL);

          await act(async () => resolveReload(page([teammateAnswer])));
          await flush();
          expect(screen.getByText(NOTICE)).toBeInTheDocument();
          expect(screen.queryByRole('button', SEND)).not.toBeInTheDocument();
          expect(screen.getByText(NOTICE).closest('[tabindex="-1"]')).toHaveFocus();
          expect(liveHolders(NOTICE)).toHaveLength(0);
        } finally {
          vi.useRealTimers();
        }
      });
    });
  });

  // The notice takes the focus when it first appears after a refusal, not on
  // every later mount.
  it('does not take the focus again after "Antwort ändern" and cancel', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(page([challenge]))
      .mockResolvedValueOnce(page([]))
      .mockResolvedValue(page([teammateAnswer]));
    vi.mocked(apiClient.post).mockRejectedValueOnce(TEAMMATE_REFUSAL());
    renderView();

    await fill();
    await userEvent.click(await screen.findByRole('button', SEND));
    await screen.findByText(NOTICE);
    expect(screen.getByText(NOTICE).closest('[tabindex="-1"]')).toHaveFocus();

    await userEvent.click(await screen.findByRole('button', { name: 'Antwort ändern' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Ändern abbrechen' }));
    const notice = (await screen.findByText(NOTICE)).closest('[tabindex="-1"]') as HTMLElement;
    expect(notice).not.toHaveFocus();
  });

  // Safari does not focus a button on tap, so the focus can already be on BODY.
  it('moves the focus to the send button when the send starts with the focus on BODY', async () => {
    mockLoad();
    vi.mocked(apiClient.post).mockReturnValueOnce(new Promise(() => {}) as never);
    renderView();

    await fill();
    (document.activeElement as HTMLElement).blur();
    expect(document.activeElement).toBe(document.body);
    fireEvent.submit(screen.getByRole('button', SEND).closest('form') as HTMLFormElement);

    const pending = await screen.findByRole('button', { name: 'Wird gesendet…' });
    expect(pending).toHaveFocus();
  });
});
