import theme from '../theme';

/**
 * The state model of an answer.
 *
 * An answer has no status field. The state is derived from what exists:
 * no submission is open, a submission without a score is submitted, and a
 * submission with a score is scored. A score taken back makes the answer
 * submitted again. A wrong answer is scored with 0 points.
 */
export type AnswerState = 'open' | 'submitted' | 'scored';

/**
 * Every sign the state model knows. `stationDone` belongs to a station whose
 * tasks are all answered. `mediaRemoved` is an add-on to a submitted or scored
 * answer after a media purge.
 */
export type AnswerStateSign = AnswerState | 'stationDone' | 'mediaRemoved';

export const ANSWER_STATE_SIGNS: readonly AnswerStateSign[] = [
  'open',
  'submitted',
  'scored',
  'stationDone',
  'mediaRemoved',
];

/** The part of a submission that decides the state. */
interface ScoredFlag {
  is_scored?: boolean | null;
}

export const deriveAnswerState = (submission: ScoredFlag | null | undefined): AnswerState => {
  if (!submission) return 'open';
  return submission.is_scored === true ? 'scored' : 'submitted';
};

/** Theme tokens for the `sx` prop, one colour per sign. */
export const ANSWER_STATE_COLOR: Record<AnswerStateSign, string> = {
  // text.secondary, not `disabled`, so the empty circle stays visible.
  // `disabled` reaches only 1.6:1.
  open: 'text.secondary',
  submitted: 'primary.main',
  scored: 'success.main',
  stationDone: 'success.main',
  mediaRemoved: 'text.secondary',
};

/**
 * The tonal blue of everything that waits for scoring. The text colour
 * primary.dark (#002378) on this background reaches 11.5:1.
 */
export const WAITING_TONAL_BG = '#E4EAF7';
export const WAITING_TONAL_TEXT = 'primary.dark';

/** Text colour of the status line of an answer card. */
export const ANSWER_STATE_TEXT_COLOR: Record<AnswerState, string> = {
  open: 'text.secondary',
  submitted: WAITING_TONAL_TEXT,
  scored: 'success.main',
};

/** Translation key (namespace `runs`) of the screen reader name per sign. */
export const ANSWER_STATE_NAME_KEY: Record<AnswerStateSign, string> = {
  open: 'answerState.open',
  submitted: 'answerState.submitted',
  scored: 'answerState.scored',
  stationDone: 'answerState.stationDone',
  // The visible text already exists, so the name reuses it.
  mediaRemoved: 'participant.challenge.mediaRemoved',
};

/**
 * Raw colour values for surfaces that cannot read the theme, such as the HTML
 * of a Leaflet marker. They come from the theme, so the map and the list
 * share one blue and one green.
 */
export const STATION_MARKER_COLOR = {
  open: theme.palette.primary.main,
  done: theme.palette.success.main,
  /** The line from a marker that had to move aside to the point of its station. */
  leader: theme.palette.text.secondary,
} as const;
