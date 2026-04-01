import apiClient, { ApiError } from './client';
import type {
  ParticipantOverview,
  AnalyticsBucketGranularity,
  AnalyticsCompareExportTable,
  AnalyticsComparison,
  AnalyticsPeriodExportTable,
  AnalyticsTimeseries,
  ChatMessage,
  ChatMessageCreate,
  FeedEvent,
  FeedEventCreate,
  Feedback,
  FeedbackCreate,
  FeedbackSummary,
  PaginatedList,
  Run,
  RunAnalytics,
  RunAnalyticsExportTable,
  RunChallenge,
  RunCreate,
  RunSchedule,
  ResultLinkPublish,
  ResultLinkStatus,
  RunStation,
  RunUpdate,
  Score,
  ScoreCreate,
  ScoreList,
  Scoreboard,
  Submission,
  SubmissionCreate,
  SubmissionUpdate,
  Team,
  TeamProgressResponse,
  TeamWouldDissolve,
} from './types';

// ============================================================================
// Runs
// ============================================================================

export async function listRuns(orgSlug: string): Promise<PaginatedList<Run>> {
  const { data } = await apiClient.get<PaginatedList<Run>>(`/api/orgs/${orgSlug}/runs/`);
  return data;
}

export async function getRun(orgSlug: string, runId: string): Promise<Run> {
  const { data } = await apiClient.get<Run>(`/api/orgs/${orgSlug}/runs/${runId}`);
  return data;
}

export async function createRun(orgSlug: string, body: RunCreate): Promise<Run> {
  const { data } = await apiClient.post<Run>(`/api/orgs/${orgSlug}/runs/`, body);
  return data;
}

export async function updateRun(orgSlug: string, runId: string, body: RunUpdate): Promise<Run> {
  const { data } = await apiClient.put<Run>(`/api/orgs/${orgSlug}/runs/${runId}`, body);
  return data;
}

// ============================================================================
// Run state transitions
// ============================================================================

export async function startRun(orgSlug: string, runId: string): Promise<Run> {
  const { data } = await apiClient.post<Run>(`/api/orgs/${orgSlug}/runs/${runId}/start`);
  return data;
}

export async function scheduleRun(
  orgSlug: string,
  runId: string,
  body: RunSchedule,
): Promise<Run> {
  const { data } = await apiClient.post<Run>(`/api/orgs/${orgSlug}/runs/${runId}/schedule`, body);
  return data;
}

export async function pauseRun(orgSlug: string, runId: string): Promise<Run> {
  const { data } = await apiClient.post<Run>(`/api/orgs/${orgSlug}/runs/${runId}/pause`);
  return data;
}

export async function resumeRun(orgSlug: string, runId: string): Promise<Run> {
  const { data } = await apiClient.post<Run>(`/api/orgs/${orgSlug}/runs/${runId}/resume`);
  return data;
}

export async function endRun(orgSlug: string, runId: string): Promise<Run> {
  const { data } = await apiClient.post<Run>(`/api/orgs/${orgSlug}/runs/${runId}/end`);
  return data;
}

// ============================================================================
// Frozen snapshots
// ============================================================================
// When a run starts, the backend copies the stations and challenges of its
// rallye into the run (`run_stations`, `run_challenges`). The run works on that
// copy, even when the library content is later changed, re-linked or deleted.
// The calls below read that copy.

export async function listRunStations(
  orgSlug: string,
  runId: string,
): Promise<PaginatedList<RunStation>> {
  const { data } = await apiClient.get<PaginatedList<RunStation>>(
    `/api/orgs/${orgSlug}/runs/${runId}/stations/`,
  );
  return data;
}

/**
 * The participant station list in one request: stations in route order with
 * their challenges, and the challenges the caller's team has answered.
 */
export async function getParticipantOverview(
  orgSlug: string,
  runId: string,
): Promise<ParticipantOverview> {
  const { data } = await apiClient.get<ParticipantOverview>(
    `/api/orgs/${orgSlug}/runs/${runId}/participant-overview`,
  );
  return data;
}

export async function listRunChallenges(
  orgSlug: string,
  runId: string,
  runStationId: string,
): Promise<PaginatedList<RunChallenge>> {
  const { data } = await apiClient.get<PaginatedList<RunChallenge>>(
    `/api/orgs/${orgSlug}/runs/${runId}/stations/${runStationId}/challenges/`,
  );
  return data;
}

// ============================================================================
// Teams
// ============================================================================

export async function listTeams(orgSlug: string, runId: string): Promise<PaginatedList<Team>> {
  const { data } = await apiClient.get<PaginatedList<Team>>(
    `/api/orgs/${orgSlug}/runs/${runId}/teams/`,
  );
  return data;
}

export async function getTeam(orgSlug: string, runId: string, teamId: string): Promise<Team> {
  const { data } = await apiClient.get<Team>(
    `/api/orgs/${orgSlug}/runs/${runId}/teams/${teamId}`,
  );
  return data;
}

interface LeaveTeamOptions {
  /**
   * Set only after the server refused this leave with 409
   * TEAM_WOULD_DISSOLVE (it changed nothing) and the person confirmed the
   * refusal they were shown. Never set from what the page believes about the
   * team. Ignored by the server while other members stay.
   */
  confirmDissolution?: boolean;
}

export async function leaveTeam(
  orgSlug: string,
  runId: string,
  teamId: string,
  options: LeaveTeamOptions = {},
): Promise<void> {
  const url = `/api/orgs/${orgSlug}/runs/${runId}/teams/${teamId}/leave`;
  if (options.confirmDissolution) {
    await apiClient.post(url, { confirm_dissolution: true });
  } else {
    await apiClient.post(url);
  }
}

// `confirm_dissolution` goes out only after the server refused the same step
// with TEAM_WOULD_DISSOLVE and the person confirmed that refusal. A first
// call carries nothing, so the server can refuse.
const dissolutionParams = (confirmDissolution: boolean) =>
  confirmDissolution ? { params: { confirm_dissolution: true } } : undefined;

// memberId is the TeamMember UUID from TeamResponse.members, not a user id.
// `confirmDissolution` says that the person confirmed the server's refusal
// of this removal. Without it the server refuses the removal of a last member
// with 409 TEAM_WOULD_DISSOLVE and changes nothing.
export async function removeTeamMember(
  orgSlug: string,
  runId: string,
  teamId: string,
  memberId: string,
  confirmDissolution = false,
): Promise<void> {
  const url = `/api/orgs/${orgSlug}/runs/${runId}/teams/${teamId}/members/${memberId}`;
  const config = dissolutionParams(confirmDissolution);
  await (config ? apiClient.delete(url, config) : apiClient.delete(url));
}

/**
 * Move a member to another team of the same run.
 * `confirmDissolution` as for the removal: the move of a last member
 * dissolves the team it leaves.
 */
export async function moveTeamMember(
  orgSlug: string,
  runId: string,
  teamId: string,
  memberId: string,
  targetTeamId: string,
  confirmDissolution = false,
): Promise<void> {
  await apiClient.post(
    `/api/orgs/${orgSlug}/runs/${runId}/teams/${teamId}/members/${memberId}/move`,
    confirmDissolution
      ? { target_team_id: targetTeamId, confirm_dissolution: true }
      : { target_team_id: targetTeamId },
  );
}

/**
 * Removes a team of a run with all its members. 404 for a team
 * that is already gone, 409 TEAM_BUSY when the team changed under the call.
 * The step always dissolves the team, so the server refuses the call without
 * `confirmDissolution` with 409 TEAM_WOULD_DISSOLVE and the facts to confirm.
 */
export async function removeTeam(
  orgSlug: string,
  runId: string,
  teamId: string,
  confirmDissolution = false,
): Promise<void> {
  const url = `/api/orgs/${orgSlug}/runs/${runId}/teams/${teamId}`;
  const config = dissolutionParams(confirmDissolution);
  await (config ? apiClient.delete(url, config) : apiClient.delete(url));
}

/**
 * The server's refusal of a step that would dissolve a team without the
 * person having confirmed that: 409 TEAM_WOULD_DISSOLVE with the numbers as
 * the server has them now. Null for every other error, and for a refusal
 * without its numbers: a number that is not there is never shown as zero.
 * A missing `run_ended` reads as false.
 */
export function teamWouldDissolve(err: unknown): TeamWouldDissolve | null {
  if (!(err instanceof ApiError) || err.errorCode !== 'TEAM_WOULD_DISSOLVE') return null;
  const { member_count, answer_count, result_published, run_ended } = err.payload;
  if (typeof member_count !== 'number' || typeof answer_count !== 'number') return null;
  return {
    member_count,
    answer_count,
    result_published: result_published === true,
    run_ended: run_ended === true,
  };
}

// ============================================================================
// Submissions
// ============================================================================

export async function listSubmissions(
  orgSlug: string,
  runId: string,
): Promise<PaginatedList<Submission>> {
  const { data } = await apiClient.get<PaginatedList<Submission>>(
    `/api/orgs/${orgSlug}/runs/${runId}/submissions/`,
  );
  return data;
}

export async function createSubmission(
  orgSlug: string,
  runId: string,
  body: SubmissionCreate,
): Promise<Submission> {
  const { data } = await apiClient.post<Submission>(
    `/api/orgs/${orgSlug}/runs/${runId}/submissions/`,
    body,
  );
  return data;
}

/** Replace an unscored submission's content. */
export async function updateSubmission(
  orgSlug: string,
  runId: string,
  submissionId: string,
  body: SubmissionUpdate,
): Promise<Submission> {
  const { data } = await apiClient.put<Submission>(
    `/api/orgs/${orgSlug}/runs/${runId}/submissions/${submissionId}`,
    body,
  );
  return data;
}

// ============================================================================
// Scores
// ============================================================================

export async function listScores(orgSlug: string, runId: string): Promise<ScoreList> {
  const { data } = await apiClient.get<ScoreList>(`/api/orgs/${orgSlug}/runs/${runId}/scores/`);
  return data;
}

export async function createScore(
  orgSlug: string,
  runId: string,
  body: ScoreCreate,
): Promise<Score> {
  const { data } = await apiClient.post<Score>(
    `/api/orgs/${orgSlug}/runs/${runId}/scores/`,
    body,
  );
  return data;
}

/** Re-open a graded submission by deleting its score. */
export async function deleteScore(
  orgSlug: string,
  runId: string,
  scoreId: string,
): Promise<void> {
  await apiClient.delete(`/api/orgs/${orgSlug}/runs/${runId}/scores/${scoreId}`);
}

export async function getScoreboard(orgSlug: string, runId: string): Promise<Scoreboard> {
  const { data } = await apiClient.get<Scoreboard>(
    `/api/orgs/${orgSlug}/runs/${runId}/scores/scoreboard`,
  );
  return data;
}

export async function getTeamProgress(
  orgSlug: string,
  runId: string,
): Promise<TeamProgressResponse> {
  const { data } = await apiClient.get<TeamProgressResponse>(
    `/api/orgs/${orgSlug}/runs/${runId}/team-progress`,
  );
  return data;
}

// ============================================================================
// Result links
// ============================================================================

/**
 * The live public result link for a member of the run. "unpublished" while
 * there is none, also after a withdrawal.
 */
export async function getMyResultLink(orgSlug: string, runId: string): Promise<ResultLinkStatus> {
  const { data } = await apiClient.get<ResultLinkStatus>(
    `/api/orgs/${orgSlug}/runs/${runId}/results/my-link`,
  );
  return data;
}

/** Current publish state of the run's public result link (unpublished/published/revoked). */
export async function getResultLink(orgSlug: string, runId: string): Promise<ResultLinkStatus> {
  const { data } = await apiClient.get<ResultLinkStatus>(
    `/api/orgs/${orgSlug}/runs/${runId}/results/link`,
  );
  return data;
}

/** Publish (or re-publish) the final standing; 409 unless the run ended and grading is complete. */
export async function publishResults(
  orgSlug: string,
  runId: string,
): Promise<ResultLinkPublish> {
  const { data } = await apiClient.post<ResultLinkPublish>(
    `/api/orgs/${orgSlug}/runs/${runId}/results/publish`,
  );
  return data;
}

/** Revoke the published link — the public page answers 410 immediately after. */
export async function revokeResults(orgSlug: string, runId: string): Promise<void> {
  await apiClient.delete(`/api/orgs/${orgSlug}/runs/${runId}/results/publish`);
}

// ============================================================================
// Feed
// ============================================================================

// `latest` asks for the newest window, not the first page. A feed and a chat
// are read at their end, so without it a long run opens on its beginning.
// `skip`/`limit` page further back through the newest-first window (up to
// 1000 per call), and the run shell loads its history that way.
export async function listFeed(
  orgSlug: string,
  runId: string,
  pagination?: { skip?: number; limit?: number },
): Promise<PaginatedList<FeedEvent>> {
  const { data } = await apiClient.get<PaginatedList<FeedEvent>>(
    `/api/orgs/${orgSlug}/runs/${runId}/feed/`,
    { params: { latest: true, ...pagination } },
  );
  return data;
}

export async function createFeedEvent(
  orgSlug: string,
  runId: string,
  body: FeedEventCreate,
): Promise<FeedEvent> {
  const { data } = await apiClient.post<FeedEvent>(
    `/api/orgs/${orgSlug}/runs/${runId}/feed/`,
    body,
  );
  return data;
}

// ============================================================================
// Chat
// ============================================================================

export async function listChatMessages(
  orgSlug: string,
  runId: string,
  teamId: string,
): Promise<PaginatedList<ChatMessage>> {
  const { data } = await apiClient.get<PaginatedList<ChatMessage>>(
    `/api/orgs/${orgSlug}/runs/${runId}/teams/${teamId}/chat/`,
    { params: { latest: true } },
  );
  return data;
}

export async function postChatMessage(
  orgSlug: string,
  runId: string,
  teamId: string,
  body: ChatMessageCreate,
): Promise<ChatMessage> {
  const { data } = await apiClient.post<ChatMessage>(
    `/api/orgs/${orgSlug}/runs/${runId}/teams/${teamId}/chat/`,
    body,
  );
  return data;
}

// ============================================================================
// Feedback
// ============================================================================

export async function createFeedback(
  orgSlug: string,
  runId: string,
  body: FeedbackCreate,
): Promise<Feedback> {
  const { data } = await apiClient.post<Feedback>(
    `/api/orgs/${orgSlug}/runs/${runId}/feedback/`,
    body,
  );
  return data;
}

export async function listFeedback(
  orgSlug: string,
  runId: string,
): Promise<PaginatedList<Feedback>> {
  const { data } = await apiClient.get<PaginatedList<Feedback>>(
    `/api/orgs/${orgSlug}/runs/${runId}/feedback/`,
  );
  return data;
}

export async function getFeedbackSummary(
  orgSlug: string,
  runId: string,
): Promise<FeedbackSummary> {
  const { data } = await apiClient.get<FeedbackSummary>(
    `/api/orgs/${orgSlug}/runs/${runId}/feedback/summary`,
  );
  return data;
}

// ============================================================================
// Analytics
// ============================================================================

export async function getRunAnalytics(orgSlug: string, runId: string): Promise<RunAnalytics> {
  const { data } = await apiClient.get<RunAnalytics>(
    `/api/orgs/${orgSlug}/runs/${runId}/analytics`,
  );
  return data;
}

/**
 * The formula-injection-safe CSV text of one analytics table. The caller
 * wraps it in a Blob for the download.
 */
export async function exportRunAnalyticsCsv(
  orgSlug: string,
  runId: string,
  table: RunAnalyticsExportTable,
): Promise<string> {
  const { data } = await apiClient.get<string>(
    `/api/orgs/${orgSlug}/runs/${runId}/analytics/export`,
    { params: { table }, responseType: 'text' },
  );
  return data;
}

// ============================================================================
// Date-range analytics — org-scoped timeseries
// ============================================================================

interface AnalyticsTimeseriesParams {
  dateFrom: string;
  dateTo: string;
  bucket: AnalyticsBucketGranularity;
}

export async function getAnalyticsTimeseries(
  orgSlug: string,
  { dateFrom, dateTo, bucket }: AnalyticsTimeseriesParams,
): Promise<AnalyticsTimeseries> {
  const { data } = await apiClient.get<AnalyticsTimeseries>(
    `/api/orgs/${orgSlug}/analytics/timeseries`,
    { params: { date_from: dateFrom, date_to: dateTo, bucket } },
  );
  return data;
}

export async function exportAnalyticsTimeseriesCsv(
  orgSlug: string,
  { dateFrom, dateTo, bucket }: AnalyticsTimeseriesParams,
  table: AnalyticsPeriodExportTable = 'metrics',
): Promise<string> {
  // `metrics` is the server's default table. Its request leaves out `table`,
  // so it stays the same as for a server without that parameter.
  const params = table === 'metrics'
    ? { date_from: dateFrom, date_to: dateTo, bucket }
    : { date_from: dateFrom, date_to: dateTo, bucket, table };
  const { data } = await apiClient.get<string>(
    `/api/orgs/${orgSlug}/analytics/timeseries/export`,
    { params, responseType: 'text' },
  );
  return data;
}

// ============================================================================
// Run comparison
// ============================================================================

export async function getAnalyticsComparison(orgSlug: string, runIds: string[]): Promise<AnalyticsComparison> {
  const { data } = await apiClient.get<AnalyticsComparison>(`/api/orgs/${orgSlug}/analytics/compare`, {
    // The client writes a list as run_ids=a&run_ids=b, the way FastAPI reads it.
    params: { run_ids: runIds },
  });
  return data;
}

export async function exportAnalyticsComparisonCsv(
  orgSlug: string,
  runIds: string[],
  table: AnalyticsCompareExportTable,
): Promise<string> {
  const { data } = await apiClient.get<string>(`/api/orgs/${orgSlug}/analytics/compare/export`, {
    params: { run_ids: runIds, table },
    responseType: 'text',
  });
  return data;
}
