/**
 * Reading the backend's reason out of a join or create failure.
 *
 * `guest.py` and `teams.py` answer 422 with a stable `error_code` next to a
 * single-language `detail` literal. The literals mix languages ("Dieses Team
 * ist voll." next to "Run has not started yet.") and word one condition in
 * more than one way. So `error_code` is checked first, and the prose match is
 * only a fallback for a caller that has none.
 *
 * JoinTeamStep and InviteTeamStep share these reasons, so neither can miss a
 * case. A screen that misses "already in another team" falls back to a generic
 * "try again", and the participant retries a condition that a retry never clears.
 */

interface JoinErrorLike {
  errorCode?: string;
  detail?: string;
}

const norm = (detail: string | undefined) => (detail || '').toLowerCase();

/**
 * A present `error_code` decides alone, so an error with one code never also
 * matches another check's prose. `{ errorCode: 'TEAM_FULL', detail: 'You are
 * already in another team' }` is only TEAM_FULL.
 */
const matches = (
  err: JoinErrorLike | undefined,
  code: string,
  prose: (detail: string) => boolean,
): boolean => {
  if (err?.errorCode !== undefined) return err.errorCode === code;
  return prose(norm(err?.detail));
};

/**
 * The caller already belongs to a different team in this run.
 *
 * Both obvious ways out are traps. A guest who leaves cannot re-enter the run.
 * Removing the membership, the coordinator's only member-level tool, writes the
 * same departure record. Only a coordinator can resolve it, and not by
 * removing them.
 */
export const isAlreadyInTeam = (err?: JoinErrorLike) =>
  matches(
    err,
    'ALREADY_IN_OTHER_TEAM',
    (d) => d.includes('bereits in einem anderen team') || d.includes('already in'),
  );

/** The target team has reached `max_members_per_team`. */
export const isTeamFull = (err?: JoinErrorLike) =>
  matches(err, 'TEAM_FULL', (d) => d.includes('voll') || d.includes('full'));

/** The run is not accepting joins yet — still configured or merely scheduled. */
export const isRunNotStarted = (err?: JoinErrorLike) =>
  matches(
    err,
    'RUN_NOT_STARTED',
    (d) =>
      d.includes('not started') ||
      d.includes('noch nicht') ||
      d.includes('active run') || // teams.py: "…only be created in active runs."
      d.includes('configured') ||
      d.includes('scheduled'),
  );

/** The run is paused. Joining works again once a coordinator resumes it. */
export const isRunPaused = (err?: JoinErrorLike) =>
  matches(err, 'RUN_PAUSED', (d) => d.includes('paused') || d.includes('pausiert'));

/** The run is over. */
export const isRunEnded = (err?: JoinErrorLike) =>
  matches(err, 'RUN_ENDED', (d) => d.includes('ended') || d.includes('beendet'));

/** Every team allowed for this run already exists. */
export const isTeamLimitReached = (err?: JoinErrorLike) =>
  matches(
    err,
    'TEAM_LIMIT_REACHED',
    (d) => d.includes('maximum team limit') || d.includes('cannot create more teams'),
  );


/**
 * The guest left this run, or a coordinator removed them. A departure
 * record keeps them out for good, so a retry can never work.
 */
export const isDeparted = (err?: JoinErrorLike) => err?.errorCode === 'RUN_DEPARTED';

/** The run belongs to an organization the operator archived. */
export const isOrgArchived = (err?: JoinErrorLike) => err?.errorCode === 'ORG_ARCHIVED';

/**
 * A scanner that cannot start is a camera problem, never a join failure. A
 * refused permission reads NotAllowedError (the browser's name for it, also
 * inside html5-qrcode's string errors). The text says the code can be typed.
 */
export function cameraErrorCode(err: Error): 'cameraDenied' | 'cameraUnavailable' {
  const text = `${err.name} ${err.message}`;
  return /NotAllowed|Permission|denied/i.test(text) ? 'cameraDenied' : 'cameraUnavailable';
}
