import apiClient from './client';
import type { GuestJoinDiscriminatedRequest, GuestJoinResponse } from './types';

/**
 * Join a run as guest. `code` is the join code of the rallye (6 characters,
 * case-insensitive). The body must carry an explicit `mode`: `'create'` with a
 * `team_name` or `'join'` with a `team_code`.
 */
export async function joinAsGuest(
  code: string,
  body: GuestJoinDiscriminatedRequest,
): Promise<GuestJoinResponse> {
  const { data } = await apiClient.post<GuestJoinResponse>(`/api/join/${code}`, body);
  return data;
}
