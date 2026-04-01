import apiClient from './client';
import type {
  Challenge,
  ChallengeCreate,
  ChallengeListResponse,
  ChallengeSortKey,
  ChallengeType,
  ChallengeUpdate,
  ChallengeUsage,
  LibrarySortOrder,
  PaginatedList,
  Rallye,
  RallyeCreate,
  RallyeStationChallengeLink,
  RallyeStationChallengeListResponse,
  RallyeStationLink,
  RallyeStationListResponse,
  RallyeUpdate,
  Station,
  StationCreate,
  StationListResponse,
  StationSortKey,
  StationUpdate,
  StationUsage,
} from './types';

// ============================================================================
// Rallyes
// ============================================================================

export async function listRallyes(
  orgSlug: string,
  options?: { include_deleted?: boolean },
): Promise<PaginatedList<Rallye>> {
  const params: Record<string, unknown> = {};
  if (options?.include_deleted) params['include_deleted'] = true;
  const { data } = await apiClient.get<PaginatedList<Rallye>>(`/api/orgs/${orgSlug}/rallyes/`, {
    params,
  });
  return data;
}

export async function getRallye(orgSlug: string, rallyeId: string): Promise<Rallye> {
  const { data } = await apiClient.get<Rallye>(`/api/orgs/${orgSlug}/rallyes/${rallyeId}`);
  return data;
}

export async function createRallye(orgSlug: string, body: RallyeCreate): Promise<Rallye> {
  const { data } = await apiClient.post<Rallye>(`/api/orgs/${orgSlug}/rallyes/`, body);
  return data;
}

export async function updateRallye(
  orgSlug: string,
  rallyeId: string,
  body: RallyeUpdate,
): Promise<Rallye> {
  const { data } = await apiClient.put<Rallye>(`/api/orgs/${orgSlug}/rallyes/${rallyeId}`, body);
  return data;
}

export async function deleteRallye(orgSlug: string, rallyeId: string): Promise<void> {
  await apiClient.delete(`/api/orgs/${orgSlug}/rallyes/${rallyeId}`);
}

// Archive management, for org_admin.
export async function restoreRallye(orgSlug: string, rallyeId: string): Promise<Rallye> {
  const { data } = await apiClient.post<Rallye>(`/api/orgs/${orgSlug}/rallyes/${rallyeId}/restore`);
  return data;
}

export async function permanentlyDeleteRallye(orgSlug: string, rallyeId: string): Promise<void> {
  await apiClient.delete(`/api/orgs/${orgSlug}/rallyes/${rallyeId}/permanent`);
}

export async function duplicateRallye(orgSlug: string, rallyeId: string): Promise<Rallye> {
  const { data } = await apiClient.post<Rallye>(
    `/api/orgs/${orgSlug}/rallyes/${rallyeId}/duplicate`,
  );
  return data;
}

// ============================================================================
// Stations — org-wide library (CRUD)
// ============================================================================

/**
 * Query options of the library list endpoints. Only defined values are sent.
 * A blank `q` and a `type` of `'all'` mean "no restriction" and are not sent.
 */
interface LibraryListOptions<S extends string> {
  skip?: number;
  limit?: number;
  include_deleted?: boolean;
  q?: string;
  sort?: S;
  order?: LibrarySortOrder;
  lang?: string;
}

type ChallengeLibraryListOptions = LibraryListOptions<ChallengeSortKey> & {
  /** `'all'` (the unfiltered state of the type select) is not sent. */
  type?: ChallengeType | 'all';
};

function libraryListParams<S extends string>(
  options: LibraryListOptions<S> & { type?: ChallengeType | 'all' },
): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  if (options.skip !== undefined) params['skip'] = options.skip;
  if (options.limit !== undefined) params['limit'] = options.limit;
  if (options.include_deleted) params['include_deleted'] = true;
  const q = options.q?.trim();
  if (q) params['q'] = q;
  if (options.sort !== undefined) params['sort'] = options.sort;
  if (options.order !== undefined) params['order'] = options.order;
  if (options.lang !== undefined) params['lang'] = options.lang;
  if (options.type !== undefined && options.type !== 'all') params['type'] = options.type;
  return params;
}

export async function listStations(
  orgSlug: string,
  options?: LibraryListOptions<StationSortKey>,
): Promise<StationListResponse> {
  const params = libraryListParams(options ?? {});
  const { data } = await apiClient.get<StationListResponse>(`/api/orgs/${orgSlug}/stations/`, {
    params,
  });
  return data;
}

export async function getStation(orgSlug: string, stationId: string): Promise<Station> {
  const { data } = await apiClient.get<Station>(`/api/orgs/${orgSlug}/stations/${stationId}`);
  return data;
}

export async function createStation(orgSlug: string, body: StationCreate): Promise<Station> {
  const { data } = await apiClient.post<Station>(`/api/orgs/${orgSlug}/stations/`, body);
  return data;
}

export async function updateStation(
  orgSlug: string,
  stationId: string,
  body: StationUpdate,
): Promise<Station> {
  const { data } = await apiClient.put<Station>(
    `/api/orgs/${orgSlug}/stations/${stationId}`,
    body,
  );
  return data;
}

/**
 * Soft-deletes a library station and unlinks it from every rallye. Answers
 * 200 with the StationUsage it affected, so the caller can report it.
 */
export async function deleteStation(orgSlug: string, stationId: string): Promise<StationUsage> {
  const { data } = await apiClient.delete<StationUsage>(
    `/api/orgs/${orgSlug}/stations/${stationId}`,
  );
  return data;
}

// Archive management, for org_admin.
export async function restoreStation(orgSlug: string, stationId: string): Promise<Station> {
  const { data } = await apiClient.post<Station>(
    `/api/orgs/${orgSlug}/stations/${stationId}/restore`,
  );
  return data;
}

export async function permanentlyDeleteStation(orgSlug: string, stationId: string): Promise<void> {
  await apiClient.delete(`/api/orgs/${orgSlug}/stations/${stationId}/permanent`);
}

/**
 * Upload or replace a station's title image. The server deletes the previous
 * object. Returns the station with a fresh `title_image_url`. axios sets the
 * multipart Content-Type.
 */
export async function setStationTitleImage(
  orgSlug: string,
  stationId: string,
  file: File,
): Promise<Station> {
  const form = new FormData();
  form.append('file', file);
  const { data } = await apiClient.put<Station>(
    `/api/orgs/${orgSlug}/stations/${stationId}/title-image`,
    form,
  );
  return data;
}

/** Remove a station's title image. Returns the updated station. */
export async function removeStationTitleImage(
  orgSlug: string,
  stationId: string,
): Promise<Station> {
  const { data } = await apiClient.delete<Station>(
    `/api/orgs/${orgSlug}/stations/${stationId}/title-image`,
  );
  return data;
}

// ============================================================================
// Challenges — org-wide library (CRUD)
// ============================================================================

export async function listChallenges(
  orgSlug: string,
  options?: ChallengeLibraryListOptions,
): Promise<ChallengeListResponse> {
  const params = libraryListParams(options ?? {});
  const { data } = await apiClient.get<ChallengeListResponse>(`/api/orgs/${orgSlug}/challenges/`, {
    params,
  });
  return data;
}

export async function getChallenge(orgSlug: string, challengeId: string): Promise<Challenge> {
  const { data } = await apiClient.get<Challenge>(`/api/orgs/${orgSlug}/challenges/${challengeId}`);
  return data;
}

export async function createChallenge(
  orgSlug: string,
  body: ChallengeCreate,
): Promise<Challenge> {
  const { data } = await apiClient.post<Challenge>(`/api/orgs/${orgSlug}/challenges/`, body);
  return data;
}

export async function updateChallenge(
  orgSlug: string,
  challengeId: string,
  body: ChallengeUpdate,
): Promise<Challenge> {
  const { data } = await apiClient.put<Challenge>(
    `/api/orgs/${orgSlug}/challenges/${challengeId}`,
    body,
  );
  return data;
}

/**
 * Soft-deletes a library challenge and unlinks it from every station. Answers
 * 200 with the ChallengeUsage it affected.
 */
export async function deleteChallenge(
  orgSlug: string,
  challengeId: string,
): Promise<ChallengeUsage> {
  const { data } = await apiClient.delete<ChallengeUsage>(
    `/api/orgs/${orgSlug}/challenges/${challengeId}`,
  );
  return data;
}

// Archive management, for org_admin.
export async function restoreChallenge(orgSlug: string, challengeId: string): Promise<Challenge> {
  const { data } = await apiClient.post<Challenge>(
    `/api/orgs/${orgSlug}/challenges/${challengeId}/restore`,
  );
  return data;
}

export async function permanentlyDeleteChallenge(
  orgSlug: string,
  challengeId: string,
): Promise<void> {
  await apiClient.delete(`/api/orgs/${orgSlug}/challenges/${challengeId}/permanent`);
}

// ============================================================================
// Composition — Rallye ⇄ Station links
// ============================================================================

export async function listRallyeStations(
  orgSlug: string,
  rallyeId: string,
): Promise<RallyeStationListResponse> {
  const { data } = await apiClient.get<RallyeStationListResponse>(
    `/api/orgs/${orgSlug}/rallyes/${rallyeId}/stations`,
  );
  return data;
}

/** Creates a brand-new library station and links it into the rallye. */
export async function createRallyeStation(
  orgSlug: string,
  rallyeId: string,
  body: StationCreate,
): Promise<RallyeStationLink> {
  const { data } = await apiClient.post<RallyeStationLink>(
    `/api/orgs/${orgSlug}/rallyes/${rallyeId}/stations`,
    body,
  );
  return data;
}

/** Links an existing library station into the rallye. */
export async function importStation(
  orgSlug: string,
  rallyeId: string,
  body: { station_id: string; sort_order?: number },
): Promise<RallyeStationLink> {
  const { data } = await apiClient.post<RallyeStationLink>(
    `/api/orgs/${orgSlug}/rallyes/${rallyeId}/stations/import`,
    body,
  );
  return data;
}

/** Unlinks a station from the rallye (does not delete the library station). */
export async function unlinkStation(
  orgSlug: string,
  rallyeId: string,
  stationId: string,
): Promise<void> {
  await apiClient.delete(`/api/orgs/${orgSlug}/rallyes/${rallyeId}/stations/${stationId}`);
}

/** Reorders the rallye's station links; `ids` is the full ordered list. */
export async function reorderStations(
  orgSlug: string,
  rallyeId: string,
  ids: string[],
): Promise<RallyeStationListResponse> {
  const { data } = await apiClient.put<RallyeStationListResponse>(
    `/api/orgs/${orgSlug}/rallyes/${rallyeId}/stations/order`,
    { ids },
  );
  return data;
}

// ============================================================================
// Composition — RallyeStation ⇄ Challenge links (challenge-matching is
// scoped to a station *within a rallye*, not the library station).
// ============================================================================

function rallyeStationChallengesBase(
  orgSlug: string,
  rallyeId: string,
  rallyeStationId: string,
): string {
  return `/api/orgs/${orgSlug}/rallyes/${rallyeId}/stations/${rallyeStationId}/challenges`;
}

export async function listRallyeStationChallenges(
  orgSlug: string,
  rallyeId: string,
  rallyeStationId: string,
): Promise<RallyeStationChallengeListResponse> {
  const { data } = await apiClient.get<RallyeStationChallengeListResponse>(
    rallyeStationChallengesBase(orgSlug, rallyeId, rallyeStationId),
  );
  return data;
}

/** Creates a brand-new library challenge and matches it to the rallye-station. */
export async function createChallengeOnRallyeStation(
  orgSlug: string,
  rallyeId: string,
  rallyeStationId: string,
  body: ChallengeCreate,
): Promise<RallyeStationChallengeLink> {
  const { data } = await apiClient.post<RallyeStationChallengeLink>(
    rallyeStationChallengesBase(orgSlug, rallyeId, rallyeStationId),
    body,
  );
  return data;
}

/** Matches an existing library challenge to the rallye-station. */
export async function importChallengeToRallyeStation(
  orgSlug: string,
  rallyeId: string,
  rallyeStationId: string,
  body: { challenge_id: string; sort_order?: number },
): Promise<RallyeStationChallengeLink> {
  const { data } = await apiClient.post<RallyeStationChallengeLink>(
    `${rallyeStationChallengesBase(orgSlug, rallyeId, rallyeStationId)}/import`,
    body,
  );
  return data;
}

/** Unmatches a challenge from the rallye-station (does not delete the library challenge). */
export async function unlinkRallyeStationChallenge(
  orgSlug: string,
  rallyeId: string,
  rallyeStationId: string,
  challengeId: string,
): Promise<void> {
  await apiClient.delete(
    `${rallyeStationChallengesBase(orgSlug, rallyeId, rallyeStationId)}/${challengeId}`,
  );
}

/** Reorders the rallye-station's challenge matches; `ids` is the full ordered list. */
export async function reorderRallyeStationChallenges(
  orgSlug: string,
  rallyeId: string,
  rallyeStationId: string,
  ids: string[],
): Promise<RallyeStationChallengeListResponse> {
  const { data } = await apiClient.put<RallyeStationChallengeListResponse>(
    `${rallyeStationChallengesBase(orgSlug, rallyeId, rallyeStationId)}/reorder`,
    { ids },
  );
  return data;
}

// ============================================================================
// Copies: the library stays shared, and a creator adapts a station or task
// through a complete copy.
// ============================================================================

/** A complete copy of a library station, in no rallye, titled "(Kopie)". */
export async function duplicateStation(orgSlug: string, stationId: string): Promise<Station> {
  const { data } = await apiClient.post<Station>(`/api/orgs/${orgSlug}/stations/${stationId}/duplicate`);
  return data;
}

/** A complete copy of a library task, on no station, titled "(Kopie)". */
export async function duplicateChallenge(orgSlug: string, challengeId: string): Promise<Challenge> {
  const { data } = await apiClient.post<Challenge>(`/api/orgs/${orgSlug}/challenges/${challengeId}/duplicate`);
  return data;
}

/** "Als Kopie bearbeiten": this rallye gets its own copy of the station. */
export async function copyRallyeStation(
  orgSlug: string,
  rallyeId: string,
  rallyeStationId: string,
): Promise<RallyeStationLink> {
  const { data } = await apiClient.post<RallyeStationLink>(
    `/api/orgs/${orgSlug}/rallyes/${rallyeId}/stations/${rallyeStationId}/copy`,
  );
  return data;
}

/** "Als Kopie bearbeiten" for a task on a station of this rallye. */
export async function copyRallyeStationChallenge(
  orgSlug: string,
  rallyeId: string,
  rallyeStationId: string,
  challengeId: string,
): Promise<RallyeStationChallengeLink> {
  const { data } = await apiClient.post<RallyeStationChallengeLink>(
    `/api/orgs/${orgSlug}/rallyes/${rallyeId}/stations/${rallyeStationId}/challenges/${challengeId}/copy`,
  );
  return data;
}
