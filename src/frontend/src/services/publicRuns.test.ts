import { describe, it, expect, vi, beforeEach } from 'vitest';
import apiClient, { ApiError } from '../api/client';
import { fetchByCode, fetchMyTeam, fetchRunState } from './publicRuns';

// Keep the real ApiError — the service branches on it, so a stubbed-out class
// would make `instanceof` meaningless and let a broken mapping pass.
vi.mock('../api/client', async (importActual) => {
  const actual = await importActual<typeof import('../api/client')>();
  return { ...actual, default: { get: vi.fn() } };
});

const mockGet = apiClient.get as unknown as ReturnType<typeof vi.fn>;

describe('publicRuns.fetchByCode', () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  it('returns the run payload on 200', async () => {
    mockGet.mockResolvedValueOnce({
      data: { run_id: 'abc-uuid', rallye_name: 'Augsburg Explorer' },
    });

    const result = await fetchByCode('ABC123');

    expect(result).toEqual({ run_id: 'abc-uuid', rallye_name: 'Augsburg Explorer' });
    expect(mockGet).toHaveBeenCalledWith(
      '/api/public/runs/ABC123',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it('returns null when the request rejects (404 or network error)', async () => {
    mockGet.mockRejectedValueOnce(new Error('boom'));
    const result = await fetchByCode('UNKNOWN');
    expect(result).toBeNull();
  });

  it('reports a 404 with ORG_ARCHIVED to the caller and still returns null', async () => {
    mockGet.mockRejectedValueOnce(new ApiError(404, 'Run not found', 'ORG_ARCHIVED'));
    const onOrgArchived = vi.fn();
    const result = await fetchByCode('ARCH01', undefined, { onOrgArchived });
    expect(result).toBeNull();
    expect(onOrgArchived).toHaveBeenCalledTimes(1);
  });

  it('does not report an unknown code as archived', async () => {
    mockGet.mockRejectedValueOnce(new ApiError(404, 'Run not found'));
    const onOrgArchived = vi.fn();
    const result = await fetchByCode('NOPE99', undefined, { onOrgArchived });
    expect(result).toBeNull();
    expect(onOrgArchived).not.toHaveBeenCalled();
  });

  it('URL-encodes the code', async () => {
    mockGet.mockResolvedValueOnce({ data: { run_id: 'x', rallye_name: 'X' } });
    await fetchByCode('AB C/?');
    expect(mockGet).toHaveBeenCalledWith(
      '/api/public/runs/AB%20C%2F%3F',
      expect.any(Object),
    );
  });
});

/**
 * The guest "am I already on a team?" probe distinguishes three outcomes, and
 * the caller shows a different screen for each. The service must read the status
 * off the ApiError that the client interceptor has already put in place of the raw
 * axios `err.response`. Pin all three: 404 maps to 'not-found', 401 to 'unauthenticated'.
 */
describe('publicRuns.fetchMyTeam status mapping', () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  it('maps 401 to unauthenticated', async () => {
    mockGet.mockRejectedValueOnce(new ApiError(401, 'Not authenticated'));
    await expect(fetchMyTeam('ABC123')).resolves.toEqual({ kind: 'unauthenticated' });
  });

  it('maps 404 to not-found', async () => {
    mockGet.mockRejectedValueOnce(new ApiError(404, 'No team'));
    await expect(fetchMyTeam('ABC123')).resolves.toEqual({ kind: 'not-found' });
  });

  it('maps anything else to error', async () => {
    mockGet.mockRejectedValueOnce(new ApiError(500, 'Boom'));
    await expect(fetchMyTeam('ABC123')).resolves.toEqual({ kind: 'error' });
  });

  it('maps a network failure to error', async () => {
    mockGet.mockRejectedValueOnce(new ApiError(0, 'Network Error'));
    await expect(fetchMyTeam('ABC123')).resolves.toEqual({ kind: 'error' });
  });

  // The wizard routes a back-navigating member on is_creator,
  // so the field has to survive the service untouched.
  it('carries is_creator through on 200', async () => {
    const payload = {
      run_id: 'run-1',
      team_id: 'team-1',
      team_name: null,
      team_share_code: 'SHARE01',
      is_creator: true,
    };
    mockGet.mockResolvedValueOnce({ data: payload });
    await expect(fetchMyTeam('ABC123')).resolves.toEqual({ kind: 'ok', data: payload });
  });
});

describe('publicRuns.fetchRunState', () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  // The endpoint carries grading_complete for ended runs.
  // The leaderboard banner keys off the run-state fields, so they have to survive
  // the service untouched.
  it('carries grading_complete through on 200', async () => {
    const payload = {
      run_id: 'run-1',
      name: 'Rallye durch Augsburg',
      state: 'ended',
      scheduled_for: null,
      started_at: '2026-01-01T09:00:00Z',
      paused_at: null,
      ended_at: '2026-01-01T12:00:00Z',
      grading_complete: true,
    };
    mockGet.mockResolvedValueOnce({ data: payload });

    await expect(fetchRunState('run-1')).resolves.toEqual(payload);
    expect(mockGet).toHaveBeenCalledWith('/api/public/runs/run-1/state', undefined);
  });

  it('returns null when the request rejects (401/404 or network error)', async () => {
    mockGet.mockRejectedValueOnce(new ApiError(404, 'Run not found'));
    await expect(fetchRunState('unknown-run')).resolves.toBeNull();
  });
});
