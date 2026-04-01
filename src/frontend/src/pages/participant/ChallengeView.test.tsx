import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route, Outlet } from 'react-router';
import { axe } from 'vitest-axe';
import ChallengeView from './ChallengeView';
import { AuthContext } from '../../auth/AuthContext';
import apiClient, { ApiError } from '../../api/client';
import type { ChallengeFormProps } from './TextChallengeForm';
import i18n from '../../i18n';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

// Mock all challenge form sub-components so we don't exercise their internals.
// The text form mock exposes the replace-mode props so tests can assert that
// ChallengeView re-enables the form with a submitAction + prefill, and the reload
// prop so a test can drive the duplicate-refusal path. The module also holds the
// send button every form shares, and the real quiz form below needs it, so
// everything but the form itself stays real.
vi.mock('./TextChallengeForm', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./TextChallengeForm')>()),
  default: (props: ChallengeFormProps) => (
    <div
      data-testid="text-challenge-form"
      data-already-submitted={String(props.alreadySubmitted)}
      data-has-submit-action={String(Boolean(props.submitAction))}
      data-has-reload={String(Boolean(props.onReload))}
      data-initial-text={typeof props.initialContent?.text === 'string' ? props.initialContent.text : ''}
    >
      {/* The real form renders this line, and only this, once an answer exists. */}
      {props.alreadySubmitted && <p>Ihr habt diese Aufgabe bereits beantwortet.</p>}
      {/* Lets a test drive the post-submit transition without a real form.
          run_challenge_id matters: ChallengeView counts the fresh answer
          towards "station done" from the payload, not from a reload. */}
      <button onClick={() => props.onSubmitted({ id: 'sub-new', run_challenge_id: 'ch-1', content: { text: 'Antwort' } } as never)}>mock-submit</button>
      {/* Stands in for the real form's TEAM_ALREADY_SUBMITTED handling: the
          coded refusal calls the reload callback the view passes. */}
      <button onClick={() => props.onReload?.()}>mock-reload</button>
      {/* The same refusal from a member who typed their own answer. */}
      <button onClick={() => props.onReload?.({ text: 'Meine eigene Antwort' })}>mock-reload-with-own</button>
      <div data-testid="form-initial-text">
        {typeof props.initialContent?.text === 'string' ? props.initialContent.text : ''}
      </div>
    </div>
  ),
}));
// QuizChallengeForm stays real: it is the one form these tests render through
// the view to check the submit button's rendered size. The three mocks below
// stand in for forms that, once an answer exists and nothing is being changed,
// render only the line "Ihr habt diese Aufgabe bereits beantwortet." (every real
// form does). They keep that line and a send button, so the view's own handling
// of both is what the tests see.
const mockKindForm = (testId: string) => (props: ChallengeFormProps) => (
  <div data-testid={testId}>
    {props.alreadySubmitted ? (
      <p>Ihr habt diese Aufgabe bereits beantwortet.</p>
    ) : (
      <button
        onClick={() =>
          props.onSubmitted({
            id: 'sub-new',
            run_challenge_id: 'ch-1',
            content: { text: 'Antwort' },
          } as never)
        }
      >
        mock-submit
      </button>
    )}
  </div>
);
vi.mock('./QrScanChallengeForm', () => ({ default: (p: ChallengeFormProps) => mockKindForm('qr-challenge-form')(p) }));
vi.mock('./PhotoChallengeForm', () => ({ default: (p: ChallengeFormProps) => mockKindForm('photo-challenge-form')(p) }));
vi.mock('./VideoChallengeForm', () => ({ default: (p: ChallengeFormProps) => mockKindForm('video-challenge-form')(p) }));

const authUser = {
  id: 'u1',
  email: 'participant@example.com',
  display_name: 'Teilnehmer',
  role: 'participant' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const makeChallenge = (type: string, overrides: Record<string, unknown> = {}) => ({
  id: 'ch-1',
  organization_id: 'org-1',
  run_id: 'run-1',
  run_station_id: 's-1',
  source_challenge_id: null,
  challenge_type: type,
  title: { de: 'Rätselaufgabe' },
  instructions: { de: 'Löse das Rätsel.' },
  scoring_rules: { max_points: 20 },
  sort_order: 0,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  ...overrides,
});

const makeSubmission = (overrides: Record<string, unknown> = {}) => ({
  id: 'sub-1',
  organization_id: 'org-1',
  run_id: 'run-1',
  team_id: 'team-1',
  run_challenge_id: 'ch-1',
  content: { text: 'Meine Antwort' },
  submitted_at: '2026-01-01T00:00:00Z',
  is_scored: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  ...overrides,
});

// runState mirrors the TeamTab outlet context (run/paused/ended) a real
// ChallengeView is nested under; omitted it leaves `run` null, same as an
// unmatched useOutletContext() call. Factored out from renderView so a test
// can `rerender` the same tree with a different run state — e.g. to simulate
// the run locking while a participant is mid-edit.
const buildTree = (
  initialEntry: string,
  runState: string | null,
) => (
  <MemoryRouter
    initialEntries={[initialEntry]}
  >
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
          element={<Outlet context={{ run: runState ? { run_id: 'run-1', state: runState } : null }} />}
        >
          <Route
            path="stations/:stationId/challenges/:challengeId"
            element={<ChallengeView />}
          />
        </Route>
      </Routes>
    </AuthContext.Provider>
  </MemoryRouter>
);

const renderView = (
  initialEntry = '/runs/run-1/participant/stations/s-1/challenges/ch-1',
  runState: string | null = null,
) => render(buildTree(initialEntry, runState));

// Helper: queue mocked API responses for challenges + submissions.
const mockGetCalls = (
  challengeType: string,
  withSubmission = false,
  submissionOverrides: Record<string, unknown> = {},
) => {
  vi.mocked(apiClient.get)
    // listRunChallenges
    .mockResolvedValueOnce({
      data: {
        items: [makeChallenge(challengeType)],
        total: 1,
        skip: 0,
        limit: 100,
      },
    })
    // listSubmissions
    .mockResolvedValueOnce({
      data: {
        items: withSubmission ? [makeSubmission(submissionOverrides)] : [],
        total: withSubmission ? 1 : 0,
        skip: 0,
        limit: 100,
      },
    });
};

// The quiz form is rendered for real, so it needs answer options to get past
// its "no options configured" guard.
const mockQuizWithOptions = () => {
  vi.mocked(apiClient.get)
    .mockResolvedValueOnce({
      data: {
        items: [makeChallenge('quiz', { scoring_rules: { max_points: 20, options: ['Augsburg', 'München'] } })],
        total: 1,
        skip: 0,
        limit: 100,
      },
    })
    .mockResolvedValueOnce({ data: { items: [], total: 0, skip: 0, limit: 100 } });
};

describe('ChallengeView', () => {
  beforeEach(() => vi.resetAllMocks());
  // Tests that fake timers must not leak them into the others when an assertion
  // fails mid-test.
  afterEach(() => vi.useRealTimers());

  it('renders title, type chip, instructions, and max_points chip', async () => {
    mockGetCalls('text');
    renderView();

    await waitFor(() => {
      expect(screen.getByText('Rätselaufgabe')).toBeInTheDocument();
    });
    expect(screen.getByText('Frage')).toBeInTheDocument();
    expect(screen.getByText('20 Pkt.')).toBeInTheDocument();
    expect(screen.getByText('Löse das Rätsel.')).toBeInTheDocument();
  });

  it('renders TextChallengeForm for type=text', async () => {
    mockGetCalls('text');
    renderView();
    await waitFor(() =>
      expect(screen.getByTestId('text-challenge-form')).toBeInTheDocument(),
    );
  });

  it('renders QuizChallengeForm for type=quiz', async () => {
    mockQuizWithOptions();
    renderView();
    await waitFor(() =>
      expect(screen.getByRole('radio', { name: 'Augsburg' })).toBeInTheDocument(),
    );
    // The option group has a name for a screen reader.
    expect(screen.getByRole('radiogroup', { name: 'Eure Antwort' })).toBeInTheDocument();
  });

  it('renders QrScanChallengeForm for type=qr_scan', async () => {
    mockGetCalls('qr_scan');
    renderView();
    await waitFor(() =>
      expect(screen.getByTestId('qr-challenge-form')).toBeInTheDocument(),
    );
  });

  it('renders PhotoChallengeForm for type=photo', async () => {
    mockGetCalls('photo');
    renderView();
    await waitFor(() =>
      expect(screen.getByTestId('photo-challenge-form')).toBeInTheDocument(),
    );
  });

  it('renders VideoChallengeForm for type=video', async () => {
    mockGetCalls('video');
    renderView();
    await waitFor(() =>
      expect(screen.getByTestId('video-challenge-form')).toBeInTheDocument(),
    );
  });

  // The type "kreativ" is gone. A run started before still carries it, and the
  // photo form with its text fallback takes what the creative form took: a photo,
  // a text, or both.
  it('renders the photo form for a creative task of an older run', async () => {
    mockGetCalls('creative');
    renderView();
    await waitFor(() =>
      expect(screen.getByTestId('photo-challenge-form')).toBeInTheDocument(),
    );
  });

  // The three post-submit situations must not share one line: an instantly
  // auto-scored quiz would look like nothing had happened, and everyone else would
  // guess whether a human still had to look at it.
  it('unscored submission says the coordination team will still score it', async () => {
    mockGetCalls('text', true);
    renderView();

    await waitFor(() => {
      expect(
        screen.getByText('Eure Antwort wurde gesendet. Die Bewertung folgt durch die Spielleitung.'),
      ).toBeInTheDocument();
    });
    // An answer that waits for scoring carries the hourglass in front of its status
    // line. The sentence names the state, the icon is hidden.
    const statusLine = screen.getByText(
      'Eure Antwort wurde gesendet. Die Bewertung folgt durch die Spielleitung.',
    ).parentElement;
    expect(statusLine?.querySelector('[data-testid="HourglassEmptyIcon"]')).toHaveAttribute(
      'aria-hidden',
      'true',
    );
    expect(screen.queryByTestId('CheckCircleIcon')).not.toBeInTheDocument();
  });

  it('auto-scorable challenge, scored: says it was scored automatically', async () => {
    mockGetCalls('quiz', true, { is_scored: true });
    // makeChallenge only carries max_points; auto-scoring needs the flag too.
    vi.mocked(apiClient.get).mockReset();
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({
        data: {
          items: [makeChallenge('quiz', { scoring_rules: { max_points: 20, auto_approve: true, correct_option: 'a' } })],
          total: 1,
          skip: 0,
          limit: 100,
        },
      })
      .mockResolvedValueOnce({
        data: { items: [makeSubmission({ is_scored: true })], total: 1, skip: 0, limit: 100 },
      });
    renderView();

    await waitFor(() => {
      expect(
        screen.getByText('Eure Antwort wurde gesendet und automatisch bewertet.'),
      ).toBeInTheDocument();
    });
  });

  it('scored by a coordinator keeps the neutral wording', async () => {
    // Same shape, but no auto_approve — a human scored it, so claiming
    // "automatically" would be a lie the participant can act on.
    mockGetCalls('text', true, { is_scored: true, points: 3, max_points: 5 });
    renderView();

    await waitFor(() => {
      expect(screen.getByText('Eure Antwort wurde gesendet.')).toBeInTheDocument();
    });
    // A scored answer carries the tick, in front of the status line and in the chip.
    // The trophy means ranking only.
    const statusLine = screen.getByText('Eure Antwort wurde gesendet.').parentElement;
    expect(statusLine?.querySelector('[data-testid="CheckCircleIcon"]')).toHaveAttribute(
      'aria-hidden',
      'true',
    );
    const chip = screen.getByText('Bewertet · 3/5').closest('.MuiChip-root');
    expect(chip).toHaveClass('MuiChip-colorSuccess');
    expect(chip?.querySelector('[data-testid="CheckCircleIcon"]')).toBeInTheDocument();
    expect(screen.queryByTestId('EmojiEventsIcon')).not.toBeInTheDocument();
    expect(screen.queryByTestId('HourglassEmptyIcon')).not.toBeInTheDocument();
  });

  it('hides the authored instructions once an answer exists', async () => {
    mockGetCalls('text', true);
    renderView();

    await waitFor(() => {
      expect(screen.getByText(/Die Bewertung folgt/)).toBeInTheDocument();
    });
    expect(screen.queryByText('Löse das Rätsel.')).not.toBeInTheDocument();
  });

  it('returns to the top of the page after an answer is submitted', async () => {
    // The answered view is shorter than the form it replaces, and the browser
    // keeps the offset it had at the submit button — leaving people mid-page
    // with the way back to the station scrolled out of sight.
    const scrollTo = vi.fn();
    vi.stubGlobal('scrollTo', scrollTo);

    mockGetCalls('text');
    renderView();
    await waitFor(() => expect(screen.getByTestId('text-challenge-form')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'mock-submit' }));

    expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ top: 0 }));
  });

  it('unscored submission shows the "Antwort ändern" button', async () => {
    mockGetCalls('text', true, { is_scored: false });
    renderView();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Antwort ändern' })).toBeInTheDocument();
    });
    // The form is left out while the answer box shows, until replace mode is entered.
    expect(screen.queryByTestId('text-challenge-form')).not.toBeInTheDocument();
    expect(screen.queryByText(/bereits bewertet/)).not.toBeInTheDocument();
  });

  it('entering replace mode re-enables the form with submitAction + prefill', async () => {
    mockGetCalls('text', true, { is_scored: false });
    renderView();

    const changeButton = await screen.findByRole('button', { name: 'Antwort ändern' });
    await userEvent.click(changeButton);

    const form = screen.getByTestId('text-challenge-form');
    expect(form).toHaveAttribute('data-already-submitted', 'false');
    expect(form).toHaveAttribute('data-has-submit-action', 'true');
    expect(form).toHaveAttribute('data-initial-text', 'Meine Antwort');

    // Cancel exits replace mode again.
    await userEvent.click(screen.getByRole('button', { name: 'Ändern abbrechen' }));
    expect(screen.queryByTestId('text-challenge-form')).not.toBeInTheDocument();
  });

  // The toast is gone: the box under the title already says the answer was sent,
  // and a toast would cover the bottom navigation for 4 s.
  it('shows no visible toast after a successful submit', async () => {
    mockGetCalls('text');
    renderView();
    await waitFor(() => expect(screen.getByTestId('text-challenge-form')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'mock-submit' }));

    expect(document.querySelector('.MuiSnackbar-root')).toBeNull();
    expect(screen.getAllByText('Antwort gesendet')).toHaveLength(1);
  });

  it('shows no visible toast after a replacement succeeds', async () => {
    mockGetCalls('text', true, { is_scored: false });
    renderView();

    await userEvent.click(await screen.findByRole('button', { name: 'Antwort ändern' }));
    await userEvent.click(screen.getByRole('button', { name: 'mock-submit' }));

    expect(document.querySelector('.MuiSnackbar-root')).toBeNull();
    expect(screen.getAllByText('Antwort gesendet')).toHaveLength(1);
  });

  // The region exists before the submit, empty and focusable. It is the one path
  // that speaks the confirmation, by taking the focus, so it carries no live role
  // of its own.
  it('has an empty focus-target region for the submit result before any submit', async () => {
    mockGetCalls('text');
    renderView();
    await waitFor(() => expect(screen.getByTestId('text-challenge-form')).toBeInTheDocument());

    const region = screen.getByTestId('submit-announcement');
    expect(region).not.toHaveAttribute('role');
    expect(region).not.toHaveAttribute('aria-live');
    expect(region).toHaveAttribute('tabindex', '-1');
    expect(region.textContent).toBe('');
  });

  // A submit lands its confirmation in that region and the focus on it: the submit
  // button disables and would otherwise drop the focus to body.
  it('puts the confirmation into the region and the focus onto it after a submit', async () => {
    mockGetCalls('text');
    renderView();
    await waitFor(() => expect(screen.getByTestId('text-challenge-form')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'mock-submit' }));

    const region = screen.getByTestId('submit-announcement');
    expect(region).not.toHaveAttribute('role');
    expect(region).toHaveTextContent('Antwort gesendet');
    expect(region).toHaveFocus();
  });

  // A failed send is announced by the form's own alert and by nothing else. The
  // hidden region of the page holds no error text and has no alert role, and the
  // focus stays on the send button. Driven through the real quiz form, first send
  // and replacement. The states of every kind are in the test file of each form.
  it('speaks a failed first send once, by the form alert, with the focus on the send button', async () => {
    mockQuizWithOptions();
    vi.mocked(apiClient.post).mockRejectedValueOnce(
      new ApiError(422, 'Antwort konnte nicht gespeichert werden'),
    );
    renderView();

    await userEvent.click(await screen.findByRole('radio', { name: 'Augsburg' }));
    await userEvent.click(screen.getByRole('button', { name: 'Antwort senden' }));

    const text = 'Antwort konnte nicht gespeichert werden';
    await screen.findByText(text);
    const region = screen.getByTestId('submit-announcement');
    expect(region).not.toHaveAttribute('role');
    expect(region).toHaveTextContent('');
    expect(screen.getAllByText(text)).toHaveLength(1);
    expect(screen.getByText(text).closest('[role="alert"]')).not.toBeNull();
    expect(screen.getByRole('button', { name: 'Antwort senden' })).toHaveFocus();
  });

  it('speaks a failed replacement once, by the form alert, with the focus on the send button', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({
        data: {
          items: [
            makeChallenge('quiz', {
              scoring_rules: { max_points: 20, options: ['Augsburg', 'München'] },
            }),
          ],
          total: 1,
          skip: 0,
          limit: 100,
        },
      })
      .mockResolvedValueOnce({
        data: {
          // selected_option preselects the quiz form's radio, so the replace
          // submit is enabled without another click.
          items: [makeSubmission({ content: { selected_option: 'Augsburg' } })],
          total: 1,
          skip: 0,
          limit: 100,
        },
      });
    vi.mocked(apiClient.put).mockRejectedValueOnce(
      new ApiError(422, 'Antwort konnte nicht gespeichert werden'),
    );
    renderView();

    await userEvent.click(await screen.findByRole('button', { name: 'Antwort ändern' }));
    await userEvent.click(screen.getByRole('button', { name: 'Antwort senden' }));

    const text = 'Antwort konnte nicht gespeichert werden';
    await screen.findByText(text);
    const region = screen.getByTestId('submit-announcement');
    expect(region).not.toHaveAttribute('role');
    expect(region).toHaveTextContent('');
    expect(screen.getAllByText(text)).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Antwort senden' })).toHaveFocus();
  });

  // The region keeps the text until the next submit, so a slow reader still finds
  // it, after any amount of time.
  it('keeps the confirmation in the region while time passes', async () => {
    mockGetCalls('text');
    renderView();
    await waitFor(() => expect(screen.getByTestId('text-challenge-form')).toBeInTheDocument());

    vi.useFakeTimers();
    fireEvent.click(screen.getByRole('button', { name: 'mock-submit' }));
    act(() => {
      vi.advanceTimersByTime(5000);
    });

    expect(screen.getAllByText('Antwort gesendet')).toHaveLength(1);
    expect(screen.getByTestId('submit-announcement')).toHaveTextContent('Antwort gesendet');
  });

  it('scored submission shows the locked hint and no change button', async () => {
    mockGetCalls('text', true, { is_scored: true });
    renderView();

    await waitFor(() => {
      expect(
        screen.getByText('Eure Antwort wurde bereits bewertet. Nur die Spielleitung kann die Bewertung wieder öffnen.'),
      ).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: 'Antwort ändern' })).not.toBeInTheDocument();
  });

  // While the form is open (no team answer yet, or replacing one), the view says
  // up front that a single answer counts for the whole team.
  it('shows the team-submission hint above the form while the team has no answer', async () => {
    mockGetCalls('text');
    renderView();

    await waitFor(() => expect(screen.getByTestId('text-challenge-form')).toBeInTheDocument());
    expect(screen.getByText('Eine Antwort gilt für das ganze Team.')).toBeInTheDocument();
  });

  it('hides the team-submission hint once an answer exists and brings it back while replacing', async () => {
    mockGetCalls('text', true, { is_scored: false });
    renderView();

    const changeButton = await screen.findByRole('button', { name: 'Antwort ändern' });
    expect(screen.queryByText('Eine Antwort gilt für das ganze Team.')).not.toBeInTheDocument();

    await userEvent.click(changeButton);
    expect(screen.getByText('Eine Antwort gilt für das ganze Team.')).toBeInTheDocument();
  });

  // The edit window is bounded by scoring: the hint under the change button says
  // so and disappears once the answer is graded.
  it('shows the edit-window hint under "Antwort ändern" while the answer is unscored', async () => {
    mockGetCalls('text', true, { is_scored: false });
    renderView();

    const changeButton = await screen.findByRole('button', { name: 'Antwort ändern' });
    expect(
      screen.getByText('Ihr könnt eure Antwort ändern, bis sie bewertet ist.'),
    ).toBeInTheDocument();
    expect(changeButton.nextElementSibling).toHaveTextContent(
      'Ihr könnt eure Antwort ändern, bis sie bewertet ist.',
    );
  });

  it('shows no edit-window hint once the answer is scored', async () => {
    mockGetCalls('text', true, { is_scored: true });
    renderView();

    await waitFor(() => expect(screen.getByText(/bereits bewertet/)).toBeInTheDocument());
    expect(
      screen.queryByText('Ihr könnt eure Antwort ändern, bis sie bewertet ist.'),
    ).not.toBeInTheDocument();
  });

  // An ended or paused run does not offer to change an answer. The edit window
  // must not stay open past the run.
  it('an active run keeps offering "Antwort ändern" and the edit-window hint', async () => {
    mockGetCalls('text', true, { is_scored: false });
    renderView('/runs/run-1/participant/stations/s-1/challenges/ch-1', 'active');

    expect(await screen.findByRole('button', { name: 'Antwort ändern' })).toBeInTheDocument();
    expect(
      screen.getByText('Ihr könnt eure Antwort ändern, bis sie bewertet ist.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Der Lauf ist beendet. Antworten können nicht mehr geändert werden.')).not.toBeInTheDocument();
    expect(screen.queryByText('Der Lauf ist pausiert. Antworten können erst nach der Pause wieder geändert werden.')).not.toBeInTheDocument();
  });

  it('an ended run hides "Antwort ändern" and the edit-window hint, and shows a locked note instead', async () => {
    mockGetCalls('text', true, { is_scored: false });
    renderView('/runs/run-1/participant/stations/s-1/challenges/ch-1', 'ended');

    await waitFor(() => {
      expect(
        screen.getByText('Der Lauf ist beendet. Antworten können nicht mehr geändert werden.'),
      ).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: 'Antwort ändern' })).not.toBeInTheDocument();
    expect(
      screen.queryByText('Ihr könnt eure Antwort ändern, bis sie bewertet ist.'),
    ).not.toBeInTheDocument();
  });

  it('a paused run hides "Antwort ändern" and the edit-window hint, and shows a locked note instead', async () => {
    mockGetCalls('text', true, { is_scored: false });
    renderView('/runs/run-1/participant/stations/s-1/challenges/ch-1', 'paused');

    await waitFor(() => {
      expect(
        screen.getByText('Der Lauf ist pausiert. Antworten können erst nach der Pause wieder geändert werden.'),
      ).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: 'Antwort ändern' })).not.toBeInTheDocument();
    expect(
      screen.queryByText('Ihr könnt eure Antwort ändern, bis sie bewertet ist.'),
    ).not.toBeInTheDocument();
  });

  // An unanswered task in an ended run has no form, only a notice that names the
  // state, one state per text.
  it.each([
    ['ended', 'Der Lauf ist beendet. Für diese Aufgabe kann keine Antwort mehr gesendet werden.'],
    ['paused', 'Der Lauf ist pausiert. Sobald es weitergeht, könnt ihr diese Aufgabe beantworten.'],
  ])('an unanswered task in a %s run shows a notice instead of the form', async (state, notice) => {
    mockGetCalls('text', false);
    renderView('/runs/run-1/participant/stations/s-1/challenges/ch-1', state);

    expect(await screen.findByText(notice)).toBeInTheDocument();
    expect(screen.queryByTestId('text-challenge-form')).not.toBeInTheDocument();
    expect(screen.queryByText('Eine Antwort gilt für das ganze Team.')).not.toBeInTheDocument();
  });

  // The lock note must show for a graded answer in an ended run too, not only
  // inside the unscored branch: the scored message alone would never say that the
  // run itself closed the edit window.
  it('a scored answer in an ended run also shows the change-locked note', async () => {
    mockGetCalls('text', true, { is_scored: true });
    renderView('/runs/run-1/participant/stations/s-1/challenges/ch-1', 'ended');

    await waitFor(() => {
      expect(
        screen.getByText('Der Lauf ist beendet. Antworten können nicht mehr geändert werden.'),
      ).toBeInTheDocument();
    });
    // The scored-specific note ("only coordination can re-open it") is a
    // separate, still-true fact and keeps showing alongside.
    expect(
      screen.getByText('Eure Antwort wurde bereits bewertet. Nur die Spielleitung kann die Bewertung wieder öffnen.'),
    ).toBeInTheDocument();
  });

  // An edit already in progress must not survive the run locking: a replace form
  // opened while the run was still active must not keep showing "Ändern abbrechen"
  // and the editable form after the run ended or paused.
  it('leaves replace mode and hides the form once the run locks mid-edit', async () => {
    mockGetCalls('text', true, { is_scored: false });
    const { rerender } = renderView('/runs/run-1/participant/stations/s-1/challenges/ch-1', 'active');

    const changeButton = await screen.findByRole('button', { name: 'Antwort ändern' });
    await userEvent.click(changeButton);

    expect(screen.getByTestId('text-challenge-form')).toHaveAttribute('data-already-submitted', 'false');
    expect(screen.getByRole('button', { name: 'Ändern abbrechen' })).toBeInTheDocument();

    // The run ends while the participant is still mid-edit.
    rerender(buildTree('/runs/run-1/participant/stations/s-1/challenges/ch-1', 'ended'));

    await waitFor(() => {
      expect(
        screen.getByText('Der Lauf ist beendet. Antworten können nicht mehr geändert werden.'),
      ).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: 'Ändern abbrechen' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('text-challenge-form')).not.toBeInTheDocument();
  });

  // The form reports the coded duplicate refusal through the reload callback.
  // The view re-fetches and the teammate's answer appears.
  it("reloads so the teammate's answer appears when the form reports the duplicate refusal", async () => {
    mockGetCalls('text'); // mount load: the team has no answer yet
    // The reload's answers: a teammate submitted while this member typed.
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({
        data: {
          items: [makeChallenge('text')],
          total: 1,
          skip: 0,
          limit: 100,
        },
      })
      .mockResolvedValueOnce({
        data: {
          items: [makeSubmission({ content: { text: 'Antwort der Mitspielerin' } })],
          total: 1,
          skip: 0,
          limit: 100,
        },
      });
    renderView();

    await waitFor(() => expect(screen.getByTestId('text-challenge-form')).toBeInTheDocument());
    expect(screen.getByTestId('text-challenge-form')).toHaveAttribute('data-has-reload', 'true');

    await userEvent.click(screen.getByRole('button', { name: 'mock-reload' }));

    // The teammate's answer arrived with the reload and the view flipped
    // into its answered state — the submission card carries the answer.
    expect(await screen.findByText('Antwort der Mitspielerin')).toBeInTheDocument();
    expect(screen.getByText(/Die Bewertung folgt/)).toBeInTheDocument();
    expect(screen.queryByTestId('text-challenge-form')).not.toBeInTheDocument();
  });

  // The member whose answer was refused must not read "Eure Antwort wurde
  // gesendet", and the own text must stay. A visible warning says it was not sent,
  // shows the own input, and "Antwort ändern" starts from it.
  it("shows a refused member's own input and offers it for the replacement", async () => {
    mockGetCalls('text');
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({
        data: { items: [makeChallenge('text')], total: 1, skip: 0, limit: 100 },
      })
      .mockResolvedValueOnce({
        data: {
          items: [makeSubmission({ content: { text: 'Antwort der Mitspielerin' }, is_scored: false })],
          total: 1,
          skip: 0,
          limit: 100,
        },
      });
    renderView('/runs/run-1/participant/stations/s-1/challenges/ch-1', 'active');
    await waitFor(() => expect(screen.getByTestId('text-challenge-form')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'mock-reload-with-own' }));

    expect(await screen.findByText('Deine Antwort wurde nicht gesendet')).toBeInTheDocument();
    expect(screen.getByText('Meine eigene Antwort')).toBeInTheDocument();
    expect(screen.getByText('Antwort der Mitspielerin')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Antwort ändern' }));
    expect(screen.getByTestId('form-initial-text')).toHaveTextContent('Meine eigene Antwort');
    // While replacing, the warning steps aside.
    expect(screen.queryByText('Deine Antwort wurde nicht gesendet')).not.toBeInTheDocument();
  });

  it('shows no refused-input warning when the reload carries no own input', async () => {
    mockGetCalls('text');
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({
        data: { items: [makeChallenge('text')], total: 1, skip: 0, limit: 100 },
      })
      .mockResolvedValueOnce({
        data: { items: [makeSubmission({ content: { text: 'Antwort der Mitspielerin' } })], total: 1, skip: 0, limit: 100 },
      });
    renderView();
    await waitFor(() => expect(screen.getByTestId('text-challenge-form')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'mock-reload' }));

    expect(await screen.findByText('Antwort der Mitspielerin')).toBeInTheDocument();
    expect(screen.queryByText('Deine Antwort wurde nicht gesendet')).not.toBeInTheDocument();
  });

  // After a submit, one primary button leads back: to the station's task list
  // while tasks remain open there.
  it('after a submit, the one primary button leads back to the station task list while it has open challenges', async () => {
    // Two challenges at the station; ch-2 stays open after ch-1 is answered.
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({
        data: {
          items: [makeChallenge('text'), makeChallenge('text', { id: 'ch-2' })],
          total: 2,
          skip: 0,
          limit: 100,
        },
      })
      .mockResolvedValueOnce({ data: { items: [], total: 0, skip: 0, limit: 100 } });
    renderView();
    await waitFor(() => expect(screen.getByTestId('text-challenge-form')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'mock-submit' }));

    const back = screen.getByRole('link', { name: 'Zurück zu den Aufgaben' });
    expect(back).toHaveAttribute('href', '/runs/run-1/participant/stations/s-1');
    // "One primary button" — no other control on the page may share the tone.
    expect(document.querySelectorAll('.MuiButton-contained')).toHaveLength(1);
  });

  // …and straight to the stations overview once the station is done,
  // restoring the map when that is where the participant came from.
  it('after a submit, the primary button leads to the stations overview keeping the map when the station is done', async () => {
    // One challenge at the station, already answered → the station is done.
    mockGetCalls('text', true, { is_scored: false });
    renderView('/runs/run-1/participant/stations/s-1/challenges/ch-1?from=map');

    const back = await screen.findByRole('link', { name: 'Zurück zu Stationen' });
    expect(back).toHaveAttribute('href', '/runs/run-1/participant/stations?view=map');
  });

  it('after a submit, the primary button leads to the plain stations overview when there is no map to keep', async () => {
    mockGetCalls('text', true, { is_scored: false });
    renderView();

    const back = await screen.findByRole('link', { name: 'Zurück zu Stationen' });
    expect(back).toHaveAttribute('href', '/runs/run-1/participant/stations');
  });

  // The station target keeps the provenance too — the station list decides its
  // own back destination from it, so losing it here would strand a map-arrived
  // participant on the plain list view.
  it('keeps ?from=map on the way back to the station while it has open challenges', async () => {
    // Two challenges; ch-1 already answered, ch-2 keeps the station open.
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({
        data: {
          items: [makeChallenge('text'), makeChallenge('text', { id: 'ch-2' })],
          total: 2,
          skip: 0,
          limit: 100,
        },
      })
      .mockResolvedValueOnce({
        data: { items: [makeSubmission({ run_challenge_id: 'ch-1' })], total: 1, skip: 0, limit: 100 },
      });
    renderView('/runs/run-1/participant/stations/s-1/challenges/ch-1?from=map');

    const back = await screen.findByRole('link', { name: 'Zurück zu den Aufgaben' });
    expect(back).toHaveAttribute('href', '/runs/run-1/participant/stations/s-1?from=map');
    expect(screen.getByRole('link', { name: 'Zurück zur Station' })).toHaveAttribute(
      'href',
      '/runs/run-1/participant/stations/s-1?from=map',
    );
  });

  // Both controls must reach the 44 px touch target. The assertions read the
  // rendered class and the declared style, not a measured box: jsdom lays nothing
  // out.
  it('renders the challenge form submit button in the large size', async () => {
    mockQuizWithOptions();
    renderView();

    const submit = await screen.findByRole('button', { name: 'Antwort senden' });
    expect(submit).toHaveClass('MuiButton-sizeLarge');
  });

  // The option group must not borrow the "Anleitung" heading, and the chip must not
  // promise "Multiple Choice" for a form that takes exactly one answer. The group
  // label tracks participant.challenge.yourAnswer, the team-facing "Eure Antwort".
  it('names the quiz option group and its type from the participant vocabulary', async () => {
    mockQuizWithOptions();
    renderView();

    expect(await screen.findByRole('group', { name: 'Eure Antwort' })).toBeInTheDocument();
    expect(screen.getByText('Quizfrage')).toBeInTheDocument();
    expect(screen.queryByText('Multiple Choice')).not.toBeInTheDocument();
  });

  it('enables the quiz submit button in the primary tone once an option is picked', async () => {
    mockQuizWithOptions();
    renderView();

    const submit = await screen.findByRole('button', { name: 'Antwort senden' });
    expect(submit).toBeDisabled();

    await userEvent.click(screen.getByRole('radio', { name: 'Augsburg' }));

    expect(submit).toBeEnabled();
    expect(submit).toHaveClass('MuiButton-contained', 'MuiButton-colorPrimary');
  });

  it('gives the "Zurück zur Station" link row a 44 px touch target', async () => {
    mockGetCalls('text');
    renderView();

    const backLink = await screen.findByRole('link', { name: 'Zurück zur Station' });
    expect(backLink).toHaveStyle({ minHeight: '44px' });
  });

  it('has no axe accessibility violations on initial render', async () => {
    mockGetCalls('text');
    const { container } = renderView();
    await waitFor(() => expect(screen.getByText('Rätselaufgabe')).toBeInTheDocument());
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  // The task page shows the place and the tips of the station without leaving the task.
  describe('the station inside the task', () => {
    const station = {
      id: 's-1',
      organization_id: 'org-1',
      run_id: 'run-1',
      source_station_id: null,
      title: { de: 'Rathaus' },
      description: { de: '' },
      location_lat: 48.3688,
      location_lng: 10.8985,
      address: 'Rathausplatz 2, 86150 Augsburg',
      title_image_url: null,
      tips: {
        header: { de: '<p>Mehr über das Rathaus</p>' },
        links: [{ label: { de: 'Goldener Saal' }, url: 'https://saal.example' }],
      },
      sort_order: 0,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    };
    const mockWithStation = () => {
      mockGetCalls('text');
      vi.mocked(apiClient.get).mockResolvedValueOnce({
        data: { items: [station], total: 1, skip: 0, limit: 100 },
      });
    };

    it('shows the address as a link to the maps app', async () => {
      mockWithStation();
      renderView();
      const link = await screen.findByRole('link', {
        name: 'Rathausplatz 2, 86150 Augsburg: in der Karten-App öffnen',
      });
      expect(link.getAttribute('href')).toContain('48.3688');
    });

    // As on the station page.
    it('gives the address link a touch target of 24 px', async () => {
      mockWithStation();
      renderView();
      const link = await screen.findByRole('link', {
        name: 'Rathausplatz 2, 86150 Augsburg: in der Karten-App öffnen',
      });
      expect(link).toHaveStyle({ display: 'inline-block', minHeight: '24px' });
    });

    it('opens the tips over the task and keeps the form', async () => {
      mockWithStation();
      renderView();
      await userEvent.click(await screen.findByRole('button', { name: 'Tipps & Links' }));
      const dialog = await screen.findByRole('dialog');
      expect(dialog).toHaveTextContent('Mehr über das Rathaus');
      expect(screen.getByRole('link', { name: 'Goldener Saal' })).toHaveAttribute('target', '_blank');
      expect(screen.getByTestId('text-challenge-form')).toBeInTheDocument();
    });

    it('still works when the station cannot be read', async () => {
      mockGetCalls('text');
      vi.mocked(apiClient.get).mockRejectedValueOnce(new ApiError(500, 'kaputt'));
      renderView();
      expect(await screen.findByTestId('text-challenge-form')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Tipps & Links' })).not.toBeInTheDocument();
      expect(screen.queryByText('kaputt')).not.toBeInTheDocument();
    });
  });

  // The task is frozen, so a refresh asks for the answers only.
  it('refreshes with one request for the answers only', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockGetCalls('text');
    renderView();
    await screen.findByTestId('text-challenge-form');
    const before = vi.mocked(apiClient.get).mock.calls.length;
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [makeSubmission()], total: 1, skip: 0, limit: 100 },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    const calls = vi.mocked(apiClient.get).mock.calls.slice(before).map((c) => c[0]);
    expect(calls).toEqual(['/api/orgs/test-org/runs/run-1/submissions/']);
    expect(await screen.findByText('Meine Antwort')).toBeInTheDocument();
  });

  // A grading that the coordinator takes back sends no live signal. The standing
  // follows it by asking every ten seconds, and the task page asks at the same
  // pace: the grade goes and "Antwort ändern" comes back within one round, without
  // a reload.
  it('drops the grade when the Spielleitung takes the grading back, at the pace of the standing', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockGetCalls('text', true, { is_scored: true, points: 15, max_points: 20 });
    renderView();
    expect(await screen.findByText('Bewertet · 15/20')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Antwort ändern' })).not.toBeInTheDocument();
    const before = vi.mocked(apiClient.get).mock.calls.length;

    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [makeSubmission({ is_scored: false, points: null, max_points: null })],
        total: 1,
        skip: 0,
        limit: 100,
      },
    });
    // Not yet: one second short of a round.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9_000);
    });
    expect(vi.mocked(apiClient.get).mock.calls.length).toBe(before);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(screen.queryByText(/Bewertet ·/)).not.toBeInTheDocument();
    expect(
      screen.getByText('Eure Antwort wurde gesendet. Die Bewertung folgt durch die Spielleitung.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Antwort ändern' })).toBeInTheDocument();
  });

  // The send button must not only turn grey while the answer is on its way: on a
  // slow connection that reads as "nothing happened". It says so from the tap until
  // the server answers. The quiz form is the real one here, and all six forms render
  // the same button.
  it('says that the answer is being sent, from the tap until the server answers', async () => {
    mockQuizWithOptions();
    let answer!: (value: unknown) => void;
    vi.mocked(apiClient.post).mockReturnValueOnce(
      new Promise((resolve) => {
        answer = resolve;
      }),
    );
    renderView();

    await userEvent.click(await screen.findByRole('radio', { name: 'Augsburg' }));
    await userEvent.click(screen.getByRole('button', { name: 'Antwort senden' }));

    const pending = await screen.findByRole('button', { name: 'Wird gesendet…' });
    // Not natively disabled, so the focus stays on it.
    expect(pending).not.toBeDisabled();
    expect(pending).toHaveAttribute('aria-disabled', 'true');
    expect(pending).toHaveAttribute('aria-busy', 'true');
    expect(pending.querySelector('.MuiCircularProgress-root')).not.toBeNull();
    expect(screen.queryByRole('button', { name: 'Antwort senden' })).not.toBeInTheDocument();

    await act(async () => {
      answer({ data: makeSubmission({ content: { selected_option: 'Augsburg' } }) });
    });

    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Wird gesendet…' })).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId('submit-announcement')).toHaveTextContent('Antwort gesendet');
  });

  it('offers the send button again when the server refuses the answer', async () => {
    mockQuizWithOptions();
    vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(500, 'kaputt'));
    renderView();

    await userEvent.click(await screen.findByRole('radio', { name: 'Augsburg' }));
    await userEvent.click(screen.getByRole('button', { name: 'Antwort senden' }));

    const again = await screen.findByRole('button', { name: 'Antwort senden' });
    expect(again).toBeEnabled();
    expect(again).not.toHaveAttribute('aria-busy');
    expect(screen.queryByRole('button', { name: 'Wird gesendet…' })).not.toBeInTheDocument();
  });

  // One message for one sent answer, in every state after a send. A unit test
  // cannot hear, so these tests check the structure that would make a screen reader
  // speak twice: the focused element is the only holder of the text, or one live
  // element is and the focus target is not.
  describe('one message for one sent answer', () => {
    const SENT = 'Antwort gesendet';
    const LIVE = '[role="alert"], [role="status"], [role="log"], [aria-live]';

    const liveHolders = () =>
      Array.from(document.querySelectorAll(LIVE)).filter(
        (el) => el.getAttribute('aria-live') !== 'off' && (el.textContent ?? '').includes(SENT),
      );

    const expectSpokenOnce = () => {
      const focused = document.activeElement as HTMLElement;
      // (e) the focus does not rest on BODY.
      expect(focused).not.toBe(document.body);
      const focusHolds = (focused.textContent ?? '').includes(SENT);
      const live = liveHolders();
      if (focusHolds) {
        // Focus path: no live element holds the text, not even one around the focus.
        expect(live).toHaveLength(0);
      } else {
        // Live path: exactly one live element holds it, and the focus target
        // is neither inside nor around it.
        expect(live).toHaveLength(1);
        expect(focused.contains(live[0])).toBe(false);
        expect(live[0].contains(focused)).toBe(false);
      }
    };

    const states: Array<{
      name: string;
      setup: () => void;
      beforeSend: () => Promise<void>;
      send: () => Promise<void>;
      waiting: boolean;
    }> = [
      ...(['text', 'qr_scan', 'photo', 'video'] as const).map((kind) => ({
        name: `${kind}, waiting for a grading`,
        setup: () => mockGetCalls(kind),
        beforeSend: async () => {
          await screen.findByRole('button', { name: 'mock-submit' });
        },
        send: async () => {
          await userEvent.click(screen.getByRole('button', { name: 'mock-submit' }));
        },
        waiting: true,
      })),
      {
        name: 'quiz with auto_approve, graded at once',
        setup: () => {
          vi.mocked(apiClient.get)
            .mockResolvedValueOnce({
              data: {
                items: [
                  makeChallenge('quiz', {
                    scoring_rules: { max_points: 20, auto_approve: true, options: ['Augsburg', 'München'] },
                  }),
                ],
                total: 1,
                skip: 0,
                limit: 100,
              },
            })
            .mockResolvedValueOnce({ data: { items: [], total: 0, skip: 0, limit: 100 } });
          vi.mocked(apiClient.post).mockResolvedValueOnce({
            data: makeSubmission({
              content: { selected_option: 'Augsburg' },
              is_scored: true,
              points: 20,
              max_points: 20,
            }),
          });
        },
        beforeSend: async () => {
          await userEvent.click(await screen.findByRole('radio', { name: 'Augsburg' }));
        },
        send: async () => {
          await userEvent.click(screen.getByRole('button', { name: 'Antwort senden' }));
        },
        waiting: false,
      },
      {
        name: 'text, a changed answer',
        setup: () => mockGetCalls('text', true, { is_scored: false }),
        beforeSend: async () => {
          await userEvent.click(await screen.findByRole('button', { name: 'Antwort ändern' }));
        },
        send: async () => {
          await userEvent.click(screen.getByRole('button', { name: 'mock-submit' }));
        },
        waiting: true,
      },
    ];

    it.each(states)('$name', async ({ setup, beforeSend, send, waiting }) => {
      setup();
      renderView();
      await beforeSend();
      await send();

      await waitFor(() =>
        expect(screen.getByTestId('submit-announcement')).toHaveTextContent(SENT),
      );
      // (a) no toast or Snackbar with the text.
      expect(document.querySelector('.MuiSnackbar-root')).toBeNull();
      expect(screen.getAllByText(SENT)).toHaveLength(1);
      // (b) the answer box, and while the grading waits, the hint.
      expect(
        screen.getByText(
          waiting
            ? 'Eure Antwort wurde gesendet. Die Bewertung folgt durch die Spielleitung.'
            : 'Eure Antwort wurde gesendet und automatisch bewertet.',
        ),
      ).toBeInTheDocument();
      if (waiting) {
        expect(
          screen.getByRole('button', { name: 'Antwort ändern' }),
        ).toBeInTheDocument();
        expect(screen.getByText('Ihr könnt eure Antwort ändern, bis sie bewertet ist.')).toBeInTheDocument();
      }
      // (c) the line of the forms is not on the page that shows the box.
      expect(screen.queryByText('Ihr habt diese Aufgabe bereits beantwortet.')).not.toBeInTheDocument();
      // (d) and (e).
      expectSpokenOnce();
    });
  });

  describe('a missing task and an unknown kind of task', () => {
    afterEach(async () => {
      await i18n.changeLanguage('de');
    });

    // The page says it in the person's language.
    it.each([
      ['de', 'Aufgabe nicht gefunden'],
      ['en', 'Task not found'],
    ])('says in %s that the task is missing', async (language, text) => {
      await i18n.changeLanguage(language);
      mockGetCalls('text');
      renderView('/runs/run-1/participant/stations/s-1/challenges/missing');

      expect(await screen.findByText(text)).toBeInTheDocument();
      expect(screen.queryByText('Challenge not found')).not.toBeInTheDocument();
    });

    // The unknown kind keeps its type in the text.
    it.each([
      ['de', 'Nicht unterstützte Aufgabenart: hologram'],
      ['en', 'Unsupported task type: hologram'],
    ])('says in %s that the kind of task is unknown, with the type', async (language, text) => {
      await i18n.changeLanguage(language);
      mockGetCalls('hologram');
      renderView();

      expect(await screen.findByText(text)).toBeInTheDocument();
      expect(screen.queryByText(/Unsupported challenge type/)).not.toBeInTheDocument();
    });
  });
});
