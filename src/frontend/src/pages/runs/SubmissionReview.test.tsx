import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { ThemeProvider } from '@mui/material/styles';
import { axe } from 'vitest-axe';
import SubmissionReview from './SubmissionReview';
import { AuthContext } from '../../auth/AuthContext';
import apiClient from '../../api/client';
import theme from '../../theme';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const authUser = {
  id: 'u1',
  email: 'coordinator@example.com',
  display_name: 'Spielleitung',
  role: 'coordinator' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const submission = {
  id: 'sub-1',
  organization_id: 'org-1',
  run_id: 'run-1',
  team_id: 'team-1',
  run_challenge_id: 'ch-1',
  content: { text: 'Antwort von Team Alpha' },
  submitted_at: '2026-01-01T00:00:00Z',
  is_scored: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const score = {
  id: 'score-1',
  organization_id: 'org-1',
  submission_id: 'sub-1',
  points: 7,
  max_points: 10,
  comment: null,
  scored_by: 'u1',
  scored_at: '2026-01-01T01:00:00Z',
  created_at: '2026-01-01T01:00:00Z',
  updated_at: '2026-01-01T01:00:00Z',
};

// Route mocked GETs by URL so the Promise.all in load() resolves regardless of order.
const mockLoadCalls = () => {
  vi.mocked(apiClient.get).mockImplementation((url: string) => {
    if (url.includes('/challenges/')) {
      return Promise.resolve({
        data: {
          items: [
            {
              id: 'ch-1',
              organization_id: 'org-1',
              run_id: 'run-1',
              run_station_id: 'st-1',
              source_challenge_id: null,
              challenge_type: 'text',
              title: { de: 'Textaufgabe' },
              instructions: { de: 'Antworte.' },
              scoring_rules: { max_points: 10 },
              sort_order: 0,
              created_at: '2026-01-01T00:00:00Z',
              updated_at: '2026-01-01T00:00:00Z',
            },
          ],
          total: 1,
          skip: 0,
          limit: 100,
        },
      });
    }
    if (url.includes('/submissions/')) {
      return Promise.resolve({ data: { items: [submission], total: 1, skip: 0, limit: 100 } });
    }
    if (url.includes('/teams/')) {
      return Promise.resolve({
        data: {
          items: [{ id: 'team-1', name: 'Team Alpha', color: '#FF0000', member_count: 1 }],
          total: 1,
          skip: 0,
          limit: 100,
        },
      });
    }
    if (url.includes('/stations/')) {
      return Promise.resolve({
        data: { items: [{ id: 'st-1', title: { de: 'Station 1' } }], total: 1, skip: 0, limit: 100 },
      });
    }
    if (url.includes('/scores/')) {
      return Promise.resolve({ data: { items: [score], total: 1 } });
    }
    return Promise.reject(new Error(`Unexpected GET ${url}`));
  });
};

const renderView = () =>
  render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={['/runs/run-1/submissions']}>
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
            <Route path="/runs/:runId/submissions" element={<SubmissionReview />} />
          </Routes>
        </AuthContext.Provider>
      </MemoryRouter>
    </ThemeProvider>,
  );

describe('SubmissionReview — re-open grading', () => {
  beforeEach(() => vi.resetAllMocks());

  it('renders the points chip with a re-open action on scored rows', async () => {
    mockLoadCalls();
    renderView();

    await waitFor(() => {
      expect(screen.getByText('7 / 10')).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: 'Bewertung zurücknehmen' })).toBeInTheDocument();
    // "7 / 10" alone does not say "scored". The tick carries
    // the full name and the digits are hidden, so it is announced once.
    const scoredChip = screen.getByText('7 / 10').closest('.MuiChip-root');
    expect(scoredChip).toHaveClass('MuiChip-colorSuccess');
    expect(screen.getByRole('img', { name: 'Bewertet, 7 von 10 Punkten' })).toHaveAttribute(
      'data-testid',
      'CheckCircleIcon',
    );
    expect(screen.getByText('7 / 10')).toHaveAttribute('aria-hidden', 'true');
    // Scored rows have no inline scoring form.
    expect(screen.queryByRole('spinbutton', { name: 'Punkte' })).not.toBeInTheDocument();
  });

  // The action's label needs 4.5:1 on white, which MUI's
  // `warning.main` misses, so the button carries `warning.dark` instead.
  it('renders the re-open action in warning.dark', async () => {
    mockLoadCalls();
    renderView();

    const reopenButton = await screen.findByRole('button', { name: 'Bewertung zurücknehmen' });
    expect(reopenButton).toHaveStyle({ color: theme.palette.warning.dark });
  });

  it('confirming re-open deletes the score and the scoring row reappears', async () => {
    mockLoadCalls();
    vi.mocked(apiClient.delete).mockResolvedValueOnce({ data: undefined });
    renderView();

    const reopenButton = await screen.findByRole('button', { name: 'Bewertung zurücknehmen' });
    await userEvent.click(reopenButton);

    // ConfirmDialog appears
    expect(await screen.findByText('Bewertung zurücknehmen?')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Zurücknehmen' }));

    await waitFor(() => {
      expect(apiClient.delete).toHaveBeenCalledWith('/api/orgs/test-org/runs/run-1/scores/score-1');
    });

    // Wait until the modal is fully gone (MUI keeps aria-modal during the
    // close transition, which hides the page from role queries).
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    // Score chip is gone, inline scoring form is back.
    expect(screen.queryByText('7 / 10')).not.toBeInTheDocument();
    expect(screen.getByRole('spinbutton', { name: 'Punkte' })).toBeInTheDocument();
  });

  it('cancelling the dialog keeps the score', async () => {
    mockLoadCalls();
    renderView();

    const reopenButton = await screen.findByRole('button', { name: 'Bewertung zurücknehmen' });
    await userEvent.click(reopenButton);
    expect(await screen.findByText('Bewertung zurücknehmen?')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Abbrechen' }));

    await waitFor(() => {
      expect(screen.queryByText('Bewertung zurücknehmen?')).not.toBeInTheDocument();
    });
    expect(apiClient.delete).not.toHaveBeenCalled();
    expect(screen.getByText('7 / 10')).toBeInTheDocument();
  });
});

describe('SubmissionReview — accessible names on the filters', () => {
  beforeEach(() => vi.resetAllMocks());

  // A filter without a hit is not a run without submissions.
  it('says that the filter finds nothing when submissions exist', async () => {
    mockLoadCalls();
    renderView();
    await screen.findByText('7 / 10');

    await userEvent.click(screen.getByRole('button', { name: 'Unbewertet' }));
    await userEvent.click(screen.getByRole('button', { name: 'Bewertet' }));
    // Every submission of the fixture is either graded or not: pick the
    // filter that leaves none, whichever it is.
    const empty = () => screen.queryByText('Keine Einreichung passt zu diesem Filter.');
    if (!empty()) await userEvent.click(screen.getByRole('button', { name: 'Unbewertet' }));
    expect(empty()).toBeInTheDocument();
    expect(screen.queryByText('Noch keine Einreichungen vorhanden.')).toBeNull();
  });

  it('names both filters: "Status" for the state filter, "Aufgabe" for the challenge filter', async () => {
    mockLoadCalls();
    renderView();

    await screen.findByText('7 / 10');
    expect(screen.getByRole('group', { name: /status/i })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: /aufgabe/i })).toBeInTheDocument();
  });

  it('announces the current value on both filters: the active toggle and the selected option', async () => {
    mockLoadCalls();
    renderView();

    await screen.findByText('7 / 10');

    // scoreFilter defaults to 'all' — that toggle is pressed, the others are not.
    expect(screen.getByRole('button', { name: 'Alle' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Bewertet' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: 'Unbewertet' })).toHaveAttribute('aria-pressed', 'false');

    // challengeFilter defaults to 'all' — the combobox reads its display value.
    expect(screen.getByRole('combobox', { name: /aufgabe/i })).toHaveTextContent('Alle');
  });

  it('has no name violation on the filter controls', async () => {
    mockLoadCalls();
    const { container } = renderView();

    await screen.findByText('7 / 10');
    const results = await axe(container, {
      runOnly: ['aria-input-field-name', 'aria-progressbar-name', 'aria-prohibited-attr'],
    });
    expect(results).toHaveNoViolations();
  });
});

describe('SubmissionReview — media rendering', () => {
  const withContent = (content: Record<string, unknown>): typeof submission =>
    ({
      ...submission,
      id: 'sub-media',
      is_scored: false,
      content,
    }) as typeof submission;

  const mockLoadCallsWith = (sub: typeof submission) => {
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.includes('/challenges/')) {
        return Promise.resolve({
          data: {
            items: [
              {
                id: 'ch-1',
                organization_id: 'org-1',
                run_id: 'run-1',
                run_station_id: 'st-1',
                source_challenge_id: null,
                challenge_type: 'photo',
                title: { de: 'Fotoaufgabe' },
                instructions: { de: 'Foto machen.' },
                scoring_rules: { max_points: 10 },
                sort_order: 0,
                created_at: '2026-01-01T00:00:00Z',
                updated_at: '2026-01-01T00:00:00Z',
              },
            ],
            total: 1,
            skip: 0,
            limit: 100,
          },
        });
      }
      if (url.includes('/submissions/')) {
        return Promise.resolve({ data: { items: [sub], total: 1, skip: 0, limit: 100 } });
      }
      if (url.includes('/teams/')) {
        return Promise.resolve({
          data: {
            items: [{ id: 'team-1', name: 'Team Alpha', color: '#FF0000', member_count: 1 }],
            total: 1,
            skip: 0,
            limit: 100,
          },
        });
      }
      if (url.includes('/stations/')) {
        return Promise.resolve({
          data: { items: [{ id: 'st-1', title: { de: 'Station 1' } }], total: 1, skip: 0, limit: 100 },
        });
      }
      if (url.includes('/scores/')) {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders photo submissions as a thumb image linking to the display variant', async () => {
    mockLoadCallsWith(withContent({ photo_url: 'org/run/team/abc_photo.jpg' }));
    renderView();
    const img = await screen.findByRole('img', { name: 'Eingereichtes Foto' });
    // the list loads the 400 px thumb, not the original
    expect(img).toHaveAttribute(
      'src',
      expect.stringContaining('/api/files/org/run/team/abc_photo.jpg?variant=thumb'),
    );
    // the full view in a new tab opens the 1600 px display variant
    const link = img.closest('a');
    expect(link).toHaveAttribute(
      'href',
      expect.stringContaining('/api/files/org/run/team/abc_photo.jpg?variant=display'),
    );
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('renders creative audio clips with an audio player', async () => {
    mockLoadCallsWith(withContent({ text: 'Unser Song', audio_url: 'org/run/team/xyz_clip.mp3' }));
    renderView();
    await screen.findByText('Unser Song');
    const audio = document.querySelector('audio');
    expect(audio).not.toBeNull();
    expect(audio).toHaveAttribute('controls');
    expect(audio?.getAttribute('src')).toContain('/api/files/org/run/team/xyz_clip.mp3');
  });

  // The name of the player holds the team and the task.
  it('names the audio and the video player with the team and the task', async () => {
    mockLoadCallsWith(
      withContent({ text: 'Unser Song', audio_url: 'org/run/team/a.mp3', video_url: 'org/run/team/v.mp4' }),
    );
    renderView();
    await screen.findByText('Unser Song');
    expect(document.querySelector('audio')).toHaveAttribute(
      'aria-label',
      'Audio-Antwort von Team Alpha, Fotoaufgabe',
    );
    expect(document.querySelector('video')).toHaveAttribute(
      'aria-label',
      'Video-Antwort von Team Alpha, Fotoaufgabe',
    );
  });

  // The purge nulls the medium's reference and sets
  // `media_removed`; the row and its score stay. The grading view says so
  // instead of loading a medium that no longer exists.
  it('says "Medium entfernt" for a purged submission and loads no medium', async () => {
    mockLoadCallsWith(withContent({ photo_url: null, media_removed: true }));
    renderView();

    expect(await screen.findByText('Team Alpha')).toBeInTheDocument();
    expect(screen.getByText('Medium entfernt')).toBeInTheDocument();
    expect(
      screen.getByText('Medium entfernt').querySelector('[data-testid="HideImageIcon"]'),
    ).toHaveAttribute('aria-hidden', 'true');
    expect(document.querySelector('img')).not.toBeInTheDocument();
    expect(document.querySelector('audio')).not.toBeInTheDocument();
    expect(document.querySelector('video')).not.toBeInTheDocument();
  });
});

describe('SubmissionReview — grading with the full picture', () => {
  // The base fixture infers `content` as `{ text: string }`; challenge answers
  // carry other keys, so variants are cast like `withContent` above.
  const withSub = (id: string, content: Record<string, unknown>): typeof submission =>
    ({ ...submission, id, is_scored: false, content }) as typeof submission;

  // Photo challenge deliberately has no `suggested_solution`: the task text
  // must show even when there is no reference answer to put it above.
  const baseChallenge = {
    id: 'ch-1',
    organization_id: 'org-1',
    run_id: 'run-1',
    run_station_id: 'st-1',
    source_challenge_id: null,
    challenge_type: 'photo',
    title: { de: 'Fotoaufgabe' },
    instructions: { de: 'Macht ein Foto vom Rathaus.' },
    scoring_rules: { max_points: 10 },
    sort_order: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };

  const mockLoad = (opts: {
    sub?: typeof submission;
    challenge?: Record<string, unknown>;
    scores?: unknown[];
  }) => {
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.includes('/challenges/')) {
        return Promise.resolve({
          data: { items: [opts.challenge ?? baseChallenge], total: 1, skip: 0, limit: 100 },
        });
      }
      if (url.includes('/submissions/')) {
        return Promise.resolve({ data: { items: [opts.sub ?? submission], total: 1, skip: 0, limit: 100 } });
      }
      if (url.includes('/teams/')) {
        return Promise.resolve({
          data: {
            items: [{ id: 'team-1', name: 'Team Alpha', color: '#FF0000', member_count: 1 }],
            total: 1,
            skip: 0,
            limit: 100,
          },
        });
      }
      if (url.includes('/stations/')) {
        return Promise.resolve({
          data: { items: [{ id: 'st-1', title: { de: 'Station 1' } }], total: 1, skip: 0, limit: 100 },
        });
      }
      if (url.includes('/scores/')) {
        return Promise.resolve({ data: { items: opts.scores ?? [], total: 0 } });
      }
      return Promise.reject(new Error(`Unexpected GET ${url}`));
    });
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  // The reference answer is rich text. The grading view
  // renders it through the sanitiser, like the task text above it.
  it('renders a formatted reference answer, and drops script and foreign images', async () => {
    mockLoad({
      challenge: {
        ...baseChallenge,
        suggested_solution:
          '<p><strong>1620</strong>, <a href="https://wiki.example/rathaus">Quelle</a></p><img src="https://tracker.example/p.gif" alt=""><script>alert(1)</script>',
      },
    });
    const { container } = renderView();
    expect(await screen.findByText('1620')).toBeInTheDocument();
    expect(screen.getByText('1620').tagName).toBe('STRONG');
    expect(screen.getByRole('link', { name: 'Quelle' })).toHaveAttribute('href', 'https://wiki.example/rathaus');
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img[src^="https://tracker"]')).toBeNull();
  });

  it('keeps a plain reference answer from before the editor readable, with its link', async () => {
    mockLoad({
      challenge: { ...baseChallenge, suggested_solution: '1620, Elias Holl.\nQuelle: https://wiki.example/rathaus' },
    });
    renderView();
    expect(await screen.findByRole('link', { name: 'https://wiki.example/rathaus' })).toBeInTheDocument();
    expect(screen.getByText(/1620, Elias Holl\./)).toBeInTheDocument();
  });

  it('shows the challenge task text even without a reference answer', async () => {
    mockLoad({ sub: withSub('sub-photo', { photo_url: 'org/run/team/abc_photo.jpg' }) });
    renderView();

    expect(await screen.findByText('Macht ein Foto vom Rathaus.')).toBeInTheDocument();
    expect(screen.queryByText('Referenzantwort')).not.toBeInTheDocument();
  });

  it('renders a quiz answer as the selected option', async () => {
    mockLoad({
      challenge: {
        ...baseChallenge,
        challenge_type: 'quiz',
        title: { de: 'Quizfrage' },
        instructions: { de: 'Wähle die richtige Antwort.' },
      },
      sub: withSub('sub-quiz', { selected_option: 'Blau' }),
    });
    renderView();

    expect(await screen.findByText('Option: Blau')).toBeInTheDocument();
  });

  it('renders a QR answer as the scanned value', async () => {
    mockLoad({
      challenge: {
        ...baseChallenge,
        challenge_type: 'qr_scan',
        title: { de: 'QR-Code' },
        instructions: { de: 'Scanne den Code an der Tür.' },
      },
      sub: withSub('sub-qr', { scanned_value: 'CLAVIS-RATHAUS' }),
    });
    renderView();

    expect(await screen.findByText('Gescannter Code: CLAVIS-RATHAUS')).toBeInTheDocument();
  });

  it('does not show the submission count next to the challenge title', async () => {
    mockLoad({});
    renderView();

    await screen.findByText('Antwort von Team Alpha');
    expect(screen.queryByText('(1)')).not.toBeInTheDocument();
  });

  it('shows a scored row\'s note under its points', async () => {
    mockLoad({
      scores: [{ ...score, comment: 'Gute Bildwahl, ein Punkt Abzug für Schärfe.' }],
    });
    renderView();

    await screen.findByText('7 / 10');
    expect(
      screen.getByText(/Interne Bewertungsnotiz: Gute Bildwahl, ein Punkt Abzug für Schärfe\./),
    ).toBeInTheDocument();
  });

  // An unscored row carries its state where a scored row has
  // its points, with the word of the filter.
  it('marks an unscored row with the blue hourglass chip "Unbewertet"', async () => {
    mockLoad({ sub: { ...submission, is_scored: false } });
    renderView();

    expect(await screen.findByText('Team Alpha')).toBeInTheDocument();
    const chip = screen
      .getAllByText('Unbewertet')
      .map((el) => el.closest('.MuiChip-root'))
      .find((el) => el !== null);
    expect(chip).toBeDefined();
    expect(chip).toHaveStyle({ color: theme.palette.primary.dark });
    expect(chip?.querySelector('[data-testid="HourglassEmptyIcon"]')).toHaveAttribute(
      'aria-hidden',
      'true',
    );
    expect(screen.queryByTestId('CheckCircleIcon')).not.toBeInTheDocument();
  });

  it('labels the note field as internal, with a helper that teams never see it', async () => {
    mockLoad({ sub: { ...submission, is_scored: false } });
    renderView();

    expect(await screen.findByLabelText('Interne Bewertungsnotiz')).toBeInTheDocument();
    expect(
      screen.getByText('Sichtbar nur für die Spielleitung. Teams sehen diese Notiz nie.'),
    ).toBeInTheDocument();
  });
});

// Typography subtitle1/subtitle2 render h6 by the MUI default, so titles would
// sit directly under the page's h1. The tests check that no level is skipped.
describe('SubmissionReview — headings and accordion names', () => {
  beforeEach(() => vi.resetAllMocks());

  // No heading skips a level, and the look of each heading stays: the
  // challenge title keeps subtitle1 (a span inside the accordion's h2 wrapper,
  // the heading slot MUI renders), the team row keeps subtitle2.
  it('jumps no heading level: the challenge group is an h2, the team row an h3', async () => {
    mockLoadCalls();
    const { container } = renderView();
    await screen.findByText('7 / 10');

    const levels = Array.from(container.querySelectorAll('h1,h2,h3,h4,h5,h6')).map(
      (h) => Number(h.tagName[1]),
    );
    expect(levels).toEqual([1, 2, 3]);
    const groupTitle = screen.getByText('Textaufgabe');
    expect(groupTitle.tagName).toBe('SPAN');
    expect(groupTitle).toHaveClass('MuiTypography-subtitle1');
    expect(groupTitle.closest('h2')).not.toBeNull();
    expect(container.querySelector('h3')).toHaveClass('MuiTypography-subtitle2');
  });

  // The accordion region is named after its challenge, via the id the
  // summary carries and the region's aria-labelledby.
  it('names the accordion region after its challenge', async () => {
    mockLoadCalls();
    renderView();
    await screen.findByText('7 / 10');

    expect(screen.getByRole('region', { name: 'Textaufgabe' })).toBeInTheDocument();
  });
});

// The grading view shows a new answer without a reload: it asks again every
// 10 s, for the answers, the grades and the teams. The frozen tasks are not
// read again.
describe('SubmissionReview — refresh', () => {
  beforeEach(() => vi.resetAllMocks());

  it('shows a new answer without a reload, and reads no task again', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mockLoadCalls();
      renderView();
      expect(await screen.findByText('Antwort von Team Alpha')).toBeInTheDocument();
      const before = vi.mocked(apiClient.get).mock.calls.length;

      const fresh = { ...submission, id: 'sub-2', team_id: 'team-1', content: { text: 'Neue Antwort' } };
      const base = vi.mocked(apiClient.get).getMockImplementation()!;
      vi.mocked(apiClient.get).mockImplementation((url: string) =>
        url.includes('/submissions/')
          ? Promise.resolve({ data: { items: [submission, fresh], total: 2, skip: 0, limit: 100 } })
          : base(url),
      );
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });

      expect(await screen.findByText('Neue Antwort')).toBeInTheDocument();
      const urls = vi.mocked(apiClient.get).mock.calls.slice(before).map((c) => c[0] as string);
      expect(urls).toHaveLength(3);
      expect(urls.some((u) => u.includes('/submissions/'))).toBe(true);
      expect(urls.some((u) => u.includes('/scores/'))).toBe(true);
      expect(urls.some((u) => u.includes('/teams/'))).toBe(true);
      expect(urls.some((u) => u.includes('/challenges/') || u.includes('/stations/'))).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });
});
