import apiClient, { ApiError } from '../api/client';

/**
 * The frozen public projection behind `/results#<slug>`: the fields the
 * backend stores at publish time. Nothing about the organization's users or
 * the participants leaks into it.
 */
export interface PublicResultsEntry {
  /** Null for a team that never picked a name. It renders as "Team <n>". */
  team_name: string | null;
  points: number;
  rank: number;
  is_winner: boolean;
}

export interface PublicResults {
  run_name: string;
  ended_at: string | null;
  /** The run's first content language, frozen at publish time. */
  language: string;
  entries: PublicResultsEntry[];
}

type PublicResultsLookup =
  | { kind: 'ok'; data: PublicResults }
  | { kind: 'not_found' }
  | { kind: 'expired' }
  | { kind: 'revoked' }
  | { kind: 'error' };

/**
 * Look up a published final standing by its slug.
 *
 * The slug is the URL fragment of `/results#<slug>`, which browsers never
 * send, and it travels in the POST body. So it stays out of every access-log
 * request line. 404 and 410 are terminal states with a neutral message on
 * the page. Anything else is a transient error.
 */
export async function lookupPublicResults(
  slug: string,
  signal?: AbortSignal,
): Promise<PublicResultsLookup> {
  try {
    const { data } = await apiClient.post<PublicResults>(
      '/api/public/results/lookup',
      { slug },
      signal ? { signal } : undefined,
    );
    return { kind: 'ok', data };
  } catch (err) {
    // A 410 carries a coded reason.
    if (err instanceof ApiError) {
      if (err.status === 404) return { kind: 'not_found' };
      if (err.status === 410) {
        return err.errorCode === 'result_link_revoked'
          ? { kind: 'revoked' }
          : { kind: 'expired' };
      }
    }
    return { kind: 'error' };
  }
}
