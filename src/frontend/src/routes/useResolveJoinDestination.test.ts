/**
 * The resolution a wizard step routes on.
 *
 * The creator of a still-unnamed team and a joiner in the same team both come
 * back to this hook after a browser back, and they must end up on different
 * steps. The distinction is `is_creator` from the my-team response,
 * never a local flag, which a reload or a second tab loses.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

vi.mock('../services/publicRuns', () => ({ fetchMyTeam: vi.fn() }));

import { fetchMyTeam, type PublicRunMyTeam } from '../services/publicRuns';
import {
  teamPendingPath,
  useResolveJoinDestination,
} from './useResolveJoinDestination';

const mockFetchMyTeam = vi.mocked(fetchMyTeam);

const myTeam = (overrides: Partial<PublicRunMyTeam> = {}): PublicRunMyTeam => ({
  run_id: 'run-1',
  team_id: 'team-1',
  team_name: null,
  team_share_code: 'SHARE01',
  is_creator: false,
  ...overrides,
});

beforeEach(() => {
  mockFetchMyTeam.mockReset();
  sessionStorage.clear();
});

describe('useResolveJoinDestination', () => {
  it('resolves the creator of an unnamed team to team-pending with isCreator', async () => {
    mockFetchMyTeam.mockResolvedValue({ kind: 'ok', data: myTeam({ is_creator: true }) });

    const { result } = renderHook(() => useResolveJoinDestination('ABC123'));

    await waitFor(() =>
      expect(result.current).toEqual({
        kind: 'team-pending',
        runId: 'run-1',
        teamId: 'team-1',
        teamShareCode: 'SHARE01',
        isCreator: true,
      }),
    );
  });

  it('resolves a joiner in the same unnamed team to team-pending without isCreator', async () => {
    mockFetchMyTeam.mockResolvedValue({ kind: 'ok', data: myTeam({ is_creator: false }) });

    const { result } = renderHook(() => useResolveJoinDestination('ABC123'));

    await waitFor(() => expect(result.current.kind).toBe('team-pending'));
    expect(result.current).toMatchObject({ isCreator: false, teamId: 'team-1' });
  });

  it('resolves a member without a team to no-team', async () => {
    mockFetchMyTeam.mockResolvedValue({
      kind: 'ok',
      data: myTeam({ team_id: null, team_share_code: null }),
    });

    const { result } = renderHook(() => useResolveJoinDestination('ABC123'));

    await waitFor(() => expect(result.current).toEqual({ kind: 'no-team' }));
  });

  it('resolves a named team to team-ready and remembers the team for the run shell', async () => {
    mockFetchMyTeam.mockResolvedValue({
      kind: 'ok',
      data: myTeam({ team_name: 'Schnelle Füchse', is_creator: true }),
    });

    const { result } = renderHook(() => useResolveJoinDestination('ABC123'));

    await waitFor(() =>
      expect(result.current).toEqual({ kind: 'team-ready', runId: 'run-1' }),
    );
    expect(sessionStorage.getItem('clavis_team_for_run_run-1')).toBe('team-1');
  });

  it('falls through to no-team on a failed lookup and asks nothing without a code', async () => {
    mockFetchMyTeam.mockResolvedValue({ kind: 'error' });
    const { result } = renderHook(() => useResolveJoinDestination('ABC123'));
    await waitFor(() => expect(result.current).toEqual({ kind: 'no-team' }));

    mockFetchMyTeam.mockClear();
    const { result: noCode } = renderHook(() => useResolveJoinDestination(undefined));
    expect(noCode.current).toEqual({ kind: 'no-team' });
    expect(mockFetchMyTeam).not.toHaveBeenCalled();
  });
});

describe('teamPendingPath', () => {
  it('sends the creator to the invite step and a joiner to the waiting step', () => {
    expect(teamPendingPath('ABC123', true)).toBe('/join/ABC123/team/invite');
    expect(teamPendingPath('ABC123', false)).toBe('/join/ABC123/team/waiting');
  });
});
