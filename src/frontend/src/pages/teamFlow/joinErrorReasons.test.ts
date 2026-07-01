import { describe, it, expect } from 'vitest';
import {
  isAlreadyInTeam,
  isRunEnded,
  isRunNotStarted,
  isRunPaused,
  isTeamFull,
  isTeamLimitReached,
} from './joinErrorReasons';

/**
 * The literals below are copied from the backend, not invented — if someone
 * rephrases them there, these tests are what notices.
 */
describe('join error reasons', () => {
  it('recognises the exact string guest.py sends for a second team', () => {
    // guest.py:251: this refusal must be recognised, or a participant retries against
    // a permanent condition.
    expect(
      isAlreadyInTeam({ detail: 'Du bist bereits in einem anderen Team in diesem Run.' }),
    ).toBe(true);
  });

  it('recognises an English rephrasing of the same condition', () => {
    expect(isAlreadyInTeam({ detail: 'You are already in another team in this run.' })).toBe(
      true,
    );
  });

  it('does not confuse a full team with being in one', () => {
    expect(isAlreadyInTeam({ detail: 'Dieses Team ist voll.' })).toBe(false);
    expect(isTeamFull({ detail: 'Dieses Team ist voll.' })).toBe(true);
    expect(isTeamFull({ detail: 'This team is full.' })).toBe(true);
  });

  it('recognises a run that has not started, in both languages', () => {
    expect(isRunNotStarted({ detail: 'Run has not started yet.' })).toBe(true);
    expect(isRunNotStarted({ detail: 'Der Lauf hat noch nicht begonnen.' })).toBe(true);
    expect(isRunNotStarted({ detail: 'Teams can only be created in active runs.' })).toBe(true);
  });

  it('separates paused from ended', () => {
    expect(isRunPaused({ detail: 'Der Lauf ist pausiert.' })).toBe(true);
    expect(isRunEnded({ detail: 'Der Lauf ist pausiert.' })).toBe(false);
    expect(isRunEnded({ detail: 'This run has ended.' })).toBe(true);
    expect(isRunPaused({ detail: 'This run has ended.' })).toBe(false);
  });

  it('recognises the team limit by prose', () => {
    expect(isTeamLimitReached({ detail: 'Maximum team limit reached for this run.' })).toBe(true);
    expect(isTeamLimitReached({ detail: 'Cannot create more teams for this run.' })).toBe(true);
  });

  it('treats an absent or empty detail as no known reason', () => {
    for (const check of [
      isAlreadyInTeam,
      isTeamFull,
      isRunNotStarted,
      isRunPaused,
      isRunEnded,
      isTeamLimitReached,
    ]) {
      expect(check(undefined)).toBe(false);
      expect(check({})).toBe(false);
      expect(check({ detail: '' })).toBe(false);
    }
  });

  // Every check reads error_code first. guest.py's create path answers "Maximum
  // number of teams has been reached for this run.", which the prose match above
  // does not cover: a guest at the team limit would read "try again" unless the
  // code is checked first.
  it('recognises the team limit from guest.py by error_code even though the prose never matched', () => {
    const err = {
      errorCode: 'TEAM_LIMIT_REACHED',
      detail: 'Maximum number of teams has been reached for this run.',
    };
    expect(isTeamLimitReached(err)).toBe(true);
  });

  it('recognises every code independently of its detail wording', () => {
    expect(isAlreadyInTeam({ errorCode: 'ALREADY_IN_OTHER_TEAM', detail: 'anything' })).toBe(
      true,
    );
    expect(isTeamFull({ errorCode: 'TEAM_FULL', detail: 'anything' })).toBe(true);
    expect(isRunNotStarted({ errorCode: 'RUN_NOT_STARTED', detail: 'anything' })).toBe(true);
    expect(isRunPaused({ errorCode: 'RUN_PAUSED', detail: 'anything' })).toBe(true);
    expect(isRunEnded({ errorCode: 'RUN_ENDED', detail: 'anything' })).toBe(true);
    expect(isTeamLimitReached({ errorCode: 'TEAM_LIMIT_REACHED', detail: 'anything' })).toBe(
      true,
    );
  });

  it('does not let an unrelated error_code trigger a check through prose leakage', () => {
    expect(isAlreadyInTeam({ errorCode: 'TEAM_FULL', detail: 'Dieses Team ist voll.' })).toBe(
      false,
    );
  });

  // A present error_code decides alone — the prose fallback is only for a
  // caller with no code at all. Without this, a TEAM_FULL answer whose detail
  // happens to contain "already in ..." would also satisfy isAlreadyInTeam.
  it('lets a present error_code win over prose that would otherwise match a different check', () => {
    expect(
      isAlreadyInTeam({ errorCode: 'TEAM_FULL', detail: 'You are already in another team' }),
    ).toBe(false);
  });

  it('never falls back to prose once any error_code is present, one case per code', () => {
    expect(
      isAlreadyInTeam({ errorCode: 'TEAM_FULL', detail: 'bereits in einem anderen team' }),
    ).toBe(false);
    expect(
      isTeamFull({ errorCode: 'ALREADY_IN_OTHER_TEAM', detail: 'Dieses Team ist voll.' }),
    ).toBe(false);
    expect(
      isRunNotStarted({ errorCode: 'RUN_PAUSED', detail: 'Run has not started yet.' }),
    ).toBe(false);
    expect(isRunPaused({ errorCode: 'RUN_ENDED', detail: 'Der Lauf ist pausiert.' })).toBe(
      false,
    );
    expect(isRunEnded({ errorCode: 'RUN_NOT_STARTED', detail: 'This run has ended.' })).toBe(
      false,
    );
    // The present code is TEAM_LIMIT_REACHED here, not ALREADY_IN_OTHER_TEAM,
    // even though the detail reads like the already-in-team case — the
    // team-limit mapping wins: isAlreadyInTeam ignores the misleading prose,
    // and isTeamLimitReached recognises its own code regardless of it.
    const teamLimitWithAlreadyInTeamProse = {
      errorCode: 'TEAM_LIMIT_REACHED',
      detail: 'You are already in another team',
    };
    expect(isAlreadyInTeam(teamLimitWithAlreadyInTeamProse)).toBe(false);
    expect(isTeamLimitReached(teamLimitWithAlreadyInTeamProse)).toBe(true);
  });
});
