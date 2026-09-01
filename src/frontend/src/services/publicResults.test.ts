import { describe, it, expect, vi, beforeEach } from 'vitest';
import apiClient, { ApiError } from '../api/client';
import { lookupPublicResults } from './publicResults';

// Keep the real ApiError — the service branches on it, so a stubbed-out class
// would make `instanceof` meaningless and let a broken mapping pass.
vi.mock('../api/client', async (importActual) => {
  const actual = await importActual<typeof import('../api/client')>();
  return { ...actual, default: { post: vi.fn() } };
});

const mockPost = apiClient.post as unknown as ReturnType<typeof vi.fn>;

const snapshot = {
  run_name: 'Herbst-Rallye',
  ended_at: '2026-01-17T16:00:00Z',
  language: 'de',
  entries: [
    { team_name: 'Team Alpha', points: 120, rank: 1, is_winner: true },
    { team_name: null, points: 90, rank: 2, is_winner: false },
  ],
};

describe('publicResults.lookupPublicResults', () => {
  beforeEach(() => {
    mockPost.mockReset();
  });

  it('posts the slug in the JSON body and returns the snapshot on 200', async () => {
    mockPost.mockResolvedValueOnce({ data: snapshot });

    await expect(lookupPublicResults('abc-slug')).resolves.toEqual({
      kind: 'ok',
      data: snapshot,
    });
    // The slug travels in the body, never the URL, so no
    // request line (and no access log) carries it.
    expect(mockPost).toHaveBeenCalledWith(
      '/api/public/results/lookup',
      { slug: 'abc-slug' },
      undefined,
    );
  });

  it('maps 404 to not_found', async () => {
    mockPost.mockRejectedValueOnce(
      new ApiError(404, 'Result link not found.', 'result_link_not_found'),
    );
    await expect(lookupPublicResults('unknown')).resolves.toEqual({ kind: 'not_found' });
  });

  it('maps a 410 with result_link_expired to expired', async () => {
    mockPost.mockRejectedValueOnce(
      new ApiError(410, 'This result link has expired.', 'result_link_expired'),
    );
    await expect(lookupPublicResults('old')).resolves.toEqual({ kind: 'expired' });
  });

  it('maps a 410 with result_link_revoked to revoked', async () => {
    mockPost.mockRejectedValueOnce(
      new ApiError(410, 'This result link has been revoked.', 'result_link_revoked'),
    );
    await expect(lookupPublicResults('pulled')).resolves.toEqual({ kind: 'revoked' });
  });

  it('maps a 410 without a coded reason to expired (same neutral message)', async () => {
    mockPost.mockRejectedValueOnce(new ApiError(410, 'Gone'));
    await expect(lookupPublicResults('x')).resolves.toEqual({ kind: 'expired' });
  });

  it('maps anything else to error', async () => {
    mockPost.mockRejectedValueOnce(new ApiError(429, 'Too many requests'));
    await expect(lookupPublicResults('x')).resolves.toEqual({ kind: 'error' });
  });

  it('maps a network failure to error', async () => {
    mockPost.mockRejectedValueOnce(new ApiError(0, 'Network Error'));
    await expect(lookupPublicResults('x')).resolves.toEqual({ kind: 'error' });
  });
});
