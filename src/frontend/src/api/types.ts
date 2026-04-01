/**
 * TypeScript interfaces of the backend Pydantic schemas in
 * src/backend/app/routers/schemas.py.
 */

// ============================================================================
// Auth
// ============================================================================

export interface RegisterRequest {
  /**
   * Set for bootstrap mode, which creates a new org and its admin. Omit for
   * self-signup, which registers a participant into the org of
   * `organization_slug`.
   */
  organization_name?: string;
  organization_slug: string;
  email: string;
  password: string;
  display_name: string;
}

export interface RegisterResponse {
  user_id: string;
  organization_id: string;
  email: string;
  display_name: string;
  role: string;
  /** True if the user must complete 2FA setup before continuing. */
  requires_2fa: boolean;
  /**
   * Restricted JWT for the 2FA setup, sent instead of a session cookie when
   * `requires_2fa` is true (a bootstrap org_admin while TOTP is enforced).
   * Expires in 30 minutes. Only the two TOTP routes accept it.
   */
  setup_token?: string;
  message: string;
}

export interface LoginResponse {
  status: 'success' | '2fa_required' | '2fa_setup_required';
  user_id?: string;
  email?: string;
  display_name?: string;
  role?: string;
  organization_id?: string;
  requires_2fa?: boolean;
  setup_token?: string;
  setup_url?: string;
  message: string;
}

export interface UserResponse {
  id: string;
  // Anonymized (soft-deleted) users have email and display_name null.
  email: string | null;
  display_name: string | null;
  role: 'guest' | 'participant' | 'coordinator' | 'creator' | 'org_admin';
  organization_id: string;
  is_active: boolean;
  totp_enabled: boolean;
  created_at: string;
  last_login: string | null;
  // Grace delete: soft-deleted users are restorable until the
  // retention sweep anonymizes them 30 days after deleted_at.
  is_deleted?: boolean;
  deleted_at?: string | null;
}

export interface UserListResponse {
  items: UserResponse[];
  total: number;
  skip: number;
  limit: number;
}

export interface UserCreateRequest {
  email: string;
  display_name: string;
  password: string;
  role: 'participant' | 'coordinator' | 'creator' | 'org_admin';
}

export interface UserUpdateRequest {
  role?: 'participant' | 'coordinator' | 'creator' | 'org_admin';
  display_name?: string;
  is_active?: boolean;
  email?: string;
}

// ============================================================================
// Invitations & transactional email
// ============================================================================

export type InvitableRole = 'participant' | 'coordinator' | 'creator' | 'org_admin';

export interface InvitationCreateRequest {
  email: string;
  role: InvitableRole;
  display_name?: string;
  language?: 'de' | 'en';
}

export interface InvitationResponse {
  id: string;
  email: string;
  role: InvitableRole;
  display_name: string | null;
  language: string;
  invited_by_name: string;
  created_at: string;
  expires_at: string;
  /** False when the deployment has no mail configured, or the send failed. */
  mail_delivered: boolean;
}

export interface InvitationListResponse {
  items: InvitationResponse[];
  total: number;
}

export interface InvitationPreviewResponse {
  email: string;
  role: InvitableRole;
  organization_name: string;
  display_name_suggestion: string | null;
  language: string;
  expires_at: string;
}

export interface InvitationAcceptRequest {
  display_name: string;
  password: string;
}

export interface InvitationAcceptResponse {
  message: string;
  email: string;
  role: InvitableRole;
}

export interface MailStatusResponse {
  enabled: boolean;
  from_address?: string | null;
  reply_to?: string | null;
}

export interface MailTestRequest {
  email: string;
  language?: 'de' | 'en';
}

export interface MailTestResponse {
  success: boolean;
  message: string;
  error?: string | null;
}

export interface OrganizationResponse {
  id: string;
  name: string;
  slug: string;
  languages: string[];
  settings: Record<string, unknown>;
  legal_imprint: string | null;
  legal_privacy: string | null;
  legal_accessibility: string | null;
  created_at: string;
  updated_at: string;
}

export interface OrganizationUpdateRequest {
  name?: string;
  slug?: string;
  languages?: string[];
  settings?: Record<string, unknown>;
  // `null` clears the override and falls back to the bundled placeholder.
  // Omit to leave unchanged.
  legal_imprint?: string | null;
  legal_privacy?: string | null;
  legal_accessibility?: string | null;
  /** Explicit null reverts to the default of the installation. */
  backup_retention_days?: number | null;
}

// ---------------------------------------------------------------------------
// Retention configuration
// ---------------------------------------------------------------------------

export type RetentionClass = 'LK_A' | 'LK_B' | 'LK_C' | 'LK_H';

export interface RetentionPeriodInfo {
  deletion_class: RetentionClass;
  days: number;
  source: 'override' | 'instance' | 'default';
  description: string;
}

export interface RetentionConfigResponse {
  periods: RetentionPeriodInfo[];
  min_days: number;
  max_days: number;
  /** Days a backup of the installation can still hold deleted data. The operator sets it. */
  backup_retention_days: number;
  /** 'default' means the operator has not stated the period yet. */
  backup_retention_source: 'instance' | 'default';
  /** Longest of the app, error and access log periods, in days. */
  technical_log_days: number;
  /** Security log retention period, in days. */
  audit_log_days: number;
  /** Days after the start (or creation) at which a run that never ended is swept. */
  abandoned_run_days: number;
  /** Days a published result link stays resolvable, restarted on every re-publish. */
  result_link_days: number;
}

export interface RetentionConfigUpdate {
  // The override in days, or `null` to revert the class to the instance or
  // code default. All four are sent together and replace the whole set.
  lk_a_days: number | null;
  lk_b_days: number | null;
  lk_c_days: number | null;
  lk_h_days: number | null;
}

export interface ProfileUpdateRequest {
  display_name?: string;
}

export interface ProfileUpdateResponse {
  id: string;
  email: string | null;
  display_name: string;
  role: string;
  organization_id: string;
  message: string;
}

// ============================================================================
// 2FA — TOTP
// ============================================================================

export interface TotpSetupResponse {
  secret: string;
  provisioning_uri: string;
  recovery_codes: string[];
  message: string;
}

export interface TotpVerifyResponse {
  message: string;
  status?: string;
  user_id?: string;
  email?: string;
  display_name?: string;
  role?: string;
}

export interface TotpAuthenticateRequest {
  email: string;
  code: string;
}

export interface TotpAuthenticateResponse {
  status: string;
  user_id: string;
  email: string;
  display_name: string;
  role: string;
  recovery_code_used?: boolean;
  remaining_recovery_codes?: number;
  message?: string;
}

export interface TotpDisableRequest {
  password: string;
}

// ============================================================================
// Password
// ============================================================================

export interface PasswordChangeRequest {
  old_password: string;
  new_password: string;
}

export interface PasswordForgotRequest {
  email: string;
}

export interface PasswordResetRequest {
  token: string;
  new_password: string;
}

// ============================================================================
// Content — Rallyes, Stations, Challenges
// ============================================================================

export type MultilingualString = Record<string, string>;

/** A single link in a station's tips & links popup. */
export interface StationTipLink {
  label: MultilingualString;
  url: string;
}

/**
 * Optional tips and links popup of a station: 1 to 10 links, http(s) URLs
 * only. `null` on Station and RunStation means no popup.
 */
export interface StationTips {
  header: MultilingualString;
  links: StationTipLink[];
}

export type ChallengeType =
  | 'photo'
  | 'text'
  | 'qr_scan'
  | 'quiz'
  | 'creative'
  | 'audio'
  | 'video';

export interface ScoringRules {
  max_points?: number;
  auto_approve?: boolean;
  options?: string[];
  correct_option?: string;
  expected_value?: string;
  time_bonus?: number;
  [extra: string]: unknown;
}

export interface Rallye {
  id: string;
  organization_id: string;
  title: MultilingualString;
  description: MultilingualString;
  is_published: boolean;
  is_deleted?: boolean;
  deleted_at?: string | null;
  created_at: string;
  updated_at: string;
  created_by_user_id: string | null;
  created_by_display_name: string | null;
}

export interface RallyeCreate {
  title: MultilingualString;
  description: MultilingualString;
}

export interface RallyeUpdate {
  title?: MultilingualString;
  description?: MultilingualString;
  is_published?: boolean;
}

// ----------------------------------------------------------------------------
// Library — org-wide stations/challenges composed into rallyes by reference.
// ----------------------------------------------------------------------------

// Sort and order query parameters of the library list endpoints.
export type LibrarySortOrder = 'asc' | 'desc';
export type StationSortKey = 'title' | 'created_at' | 'usage' | 'author';
export type ChallengeSortKey = StationSortKey | 'points' | 'type' | 'duration';

/**
 * Where a library station is used: the rallyes that reference it. Challenges
 * are matched per rallye-station, so a library station has no challenge,
 * points or duration aggregates. Embedded on Station and the body of
 * DELETE /stations/{id}, which soft-deletes and unlinks.
 */
export interface StationUsage {
  rallye_ids: string[];
  rallye_titles: MultilingualString[];
}

/**
 * Where a library challenge is used: the rallye-stations that match it, and
 * the rallyes those stations belong to. Returned embedded on Challenge and as
 * the body of DELETE /challenges/{id}.
 */
export interface ChallengeUsage {
  station_ids: string[];
  station_titles: MultilingualString[];
  rallye_ids: string[];
  rallye_titles: MultilingualString[];
}

export interface Station {
  id: string;
  organization_id: string;
  title: MultilingualString;
  description: MultilingualString;
  address: string | null;
  location_lat: number | null;
  location_lng: number | null;
  /** Presigned URL of the station title image, if set. */
  title_image_url: string | null;
  /** Optional tips & links popup shown to participants. */
  tips: StationTips | null;
  is_deleted?: boolean;
  deleted_at?: string | null;
  created_at: string;
  updated_at: string;
  created_by_user_id: string | null;
  created_by_display_name: string | null;
  usage?: StationUsage;
  /** Distinct non-archived rallyes using this station; equals usage.rallye_ids.length. */
  usage_count?: number;
}

export interface StationCreate {
  title: MultilingualString;
  description: MultilingualString;
  address?: string | null;
  location_lat?: number | null;
  location_lng?: number | null;
  tips?: StationTips | null;
}

export interface StationUpdate {
  title?: MultilingualString;
  description?: MultilingualString;
  address?: string | null;
  location_lat?: number | null;
  location_lng?: number | null;
  /** Explicit `null` clears tips. Omitting it keeps them. */
  tips?: StationTips | null;
}

export interface Challenge {
  id: string;
  organization_id: string;
  challenge_type: ChallengeType;
  title: MultilingualString;
  instructions: MultilingualString;
  scoring_rules: ScoringRules;
  time_estimate_minutes: number | null;
  suggested_solution: string | null;
  is_deleted?: boolean;
  deleted_at?: string | null;
  created_at: string;
  updated_at: string;
  created_by_user_id: string | null;
  created_by_display_name: string | null;
  usage?: ChallengeUsage;
  /** Distinct non-archived rallyes using this challenge; equals usage.rallye_ids.length. */
  usage_count?: number;
}

export interface ChallengeCreate {
  challenge_type: ChallengeType;
  title: MultilingualString;
  instructions: MultilingualString;
  scoring_rules: ScoringRules;
  time_estimate_minutes?: number | null;
  suggested_solution?: string | null;
}

export interface ChallengeUpdate {
  challenge_type?: ChallengeType;
  title?: MultilingualString;
  instructions?: MultilingualString;
  scoring_rules?: ScoringRules;
  time_estimate_minutes?: number | null;
  suggested_solution?: string | null;
}

// ----------------------------------------------------------------------------
// Composition — link rows joining library stations into rallyes, and library
// challenges into a rallye's station. The link carries the per-composition
// sort_order; the referenced library entity is embedded.
// ----------------------------------------------------------------------------

export interface RallyeStationLink {
  link_id: string;
  rallye_id: string;
  sort_order: number;
  station: Station;
  /** Per-rallye-station aggregates over the challenges matched in this rallye. */
  challenge_count: number;
  total_points: number;
  total_duration_minutes: number;
}

/**
 * A challenge matched to a station within a rallye. The same library station
 * can carry different challenges in different rallyes, so the link is scoped to
 * the rallye-station, not the library station.
 */
export interface RallyeStationChallengeLink {
  link_id: string;
  rallye_station_id: string;
  sort_order: number;
  challenge: Challenge;
}

export interface StationListResponse {
  items: Station[];
  total: number;
  skip: number;
  limit: number;
}

export interface ChallengeListResponse {
  items: Challenge[];
  total: number;
  skip: number;
  limit: number;
}

export interface RallyeStationListResponse {
  items: RallyeStationLink[];
  total: number;
}

export interface RallyeStationChallengeListResponse {
  items: RallyeStationChallengeLink[];
  total: number;
}

export interface PaginatedList<T> {
  items: T[];
  total: number;
  skip: number;
  limit: number;
}

// ============================================================================
// Guest
// ============================================================================

// Discriminated union on `mode`. `team_name` is optional on create-mode so the
// creator can produce a name-pending team and commit the name later via
// PATCH /api/orgs/{slug}/runs/{run_id}/teams/{team_id}.
export interface GuestJoinCreateRequest {
  mode: 'create';
  display_name?: string;
  team_name?: string | null;
  team_color?: string;
}

export interface GuestJoinByCodeRequest {
  mode: 'join';
  display_name?: string;
  team_code: string;
}

export type GuestJoinDiscriminatedRequest = GuestJoinCreateRequest | GuestJoinByCodeRequest;

export interface GuestJoinResponse {
  token: string;
  user_id: string;
  display_name: string;
  team_id: string;
  // Null for name-pending teams.
  team_name: string | null;
  team_share_code: string; // surfaced for the success screen
  run_id: string;
}

// PATCH /api/orgs/{slug}/runs/{run_id}/teams/{team_id} body.
export interface TeamRenameRequest {
  name: string;
}

// ============================================================================
// Runtime — Runs, Teams, Submissions, Scores, Feed, Chat, Feedback
// ============================================================================

export type RunState = 'configured' | 'scheduled' | 'active' | 'paused' | 'ended';

export interface Run {
  id: string;
  organization_id: string;
  rallye_id: string | null;
  name: string;
  state: RunState;
  join_code: string;
  scheduled_for: string | null;
  started_at: string | null;
  paused_at: string | null;
  ended_at: string | null;
  settings: Record<string, unknown>;
  max_teams: number | null;
  content_languages: string[];
  /** Ended with no unscored submission. Optional, so fixtures without it stay valid. */
  grading_complete?: boolean;
  created_at: string;
  updated_at: string;
}

export interface RunCreate {
  rallye_id: string;
  name: string;
  scheduled_for?: string | null;
  settings?: Record<string, unknown> | null;
  max_teams?: number | null;
  content_languages?: string[] | null;
}

export interface RunUpdate {
  name?: string | null;
  scheduled_for?: string | null;
  settings?: Record<string, unknown> | null;
  max_teams?: number | null;
  content_languages?: string[] | null;
}

export interface RunSchedule {
  scheduled_for: string;
}

export interface RunStation {
  id: string;
  organization_id: string;
  run_id: string;
  source_station_id: string | null;
  title: MultilingualString;
  description: MultilingualString;
  location_lat: number | null;
  location_lng: number | null;
  address: string | null;
  /** Presigned URL of the frozen station title image, if any. */
  title_image_url: string | null;
  /** Frozen tips & links popup, if any. */
  tips: StationTips | null;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

/** A run station with its challenges (participant overview). */
export interface RunStationWithChallenges extends RunStation {
  challenges: RunChallenge[];
}

export interface ParticipantOverview {
  stations: RunStationWithChallenges[];
  answered_challenge_ids: string[];
  /** False for a participant a coordinator removed. */
  is_team_member: boolean;
}

export interface RunChallenge {
  id: string;
  organization_id: string;
  run_id: string;
  run_station_id: string;
  source_challenge_id: string | null;
  challenge_type: ChallengeType;
  title: MultilingualString;
  instructions: MultilingualString;
  scoring_rules: ScoringRules;
  time_estimate_minutes: number | null;
  suggested_solution: string | null;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface Team {
  id: string;
  organization_id: string;
  run_id: string;
  name: string;
  color: string | null;
  member_count: number;
  /** Only for members of the team and for coordinator and up, else null. */
  share_code: string | null;
  /** True while the team's coordinator assist window is open (gates coordinator chat access) */
  coordinator_chat_open: boolean;
  /**
   * Members, oldest first. The team list fills it for coordinator and up
   * only, so the run page can name them in each row. Null or absent for every
   * other caller. Only the two keys the row needs are typed, so views with a
   * member type of their own can still extend Team.
   */
  members?: Array<Pick<TeamMember, 'id' | 'display_name'>> | null;
  created_at: string;
  updated_at: string;
}

export interface TeamMember {
  id: string;
  display_name: string;
  is_guest: boolean;
  joined_at: string;
}

export interface Submission {
  id: string;
  organization_id: string;
  run_id: string;
  team_id: string;
  run_challenge_id: string;
  content: Record<string, unknown>;
  submitted_at: string;
  is_scored: boolean;
  /** Filled once the submission is scored; null while it is not. */
  points: number | null;
  max_points: number | null;
  created_at: string;
  updated_at: string;
}

export interface SubmissionCreate {
  run_challenge_id: string;
  content: Record<string, unknown>;
}

export interface SubmissionUpdate {
  content: Record<string, unknown>;
}

export interface Score {
  id: string;
  organization_id: string;
  submission_id: string;
  points: number;
  max_points: number;
  comment: string | null;
  scored_by: string | null;
  scored_at: string;
  created_at: string;
  updated_at: string;
}

export interface ScoreCreate {
  submission_id: string;
  points: number;
  comment?: string | null;
}

export interface ScoreList {
  items: Score[];
  total: number;
}

export interface ScoreboardEntry {
  team_id: string;
  team_name: string;
  total_points: number;
  total_max_points: number;
  scored_count: number;
  last_scored_at: string | null;
  /** Shared rank: teams with equal points carry the same rank (1, 1, 3). */
  rank: number;
  /** True only when the standing is final and the rank is 1. */
  is_winner: boolean;
}

export interface Scoreboard {
  run_id: string;
  entries: ScoreboardEntry[];
  /** The run has ended and every submission is scored. */
  is_final: boolean;
  scored_count: number;
  submission_count: number;
}

// ----------------------------------------------------------------------------
// Result links — public snapshot of the final standing.
// ----------------------------------------------------------------------------

export type ResultLinkStatusType = 'unpublished' | 'published' | 'revoked';

/** GET /api/orgs/{slug}/runs/{run_id}/results/link — coordinator-facing status. */
export interface ResultLinkStatus {
  status: ResultLinkStatusType;
  /** Present when status is 'published'. */
  url: string | null;
  published_at: string | null;
  expires_at: string | null;
}

/** POST .../results/publish — the API stores the snapshot and answers URL + expiry. */
export interface ResultLinkPublish {
  url: string;
  slug: string;
  published_at: string;
  expires_at: string;
}

export type FeedEventType =
  | 'team_update'
  | 'results_available'
  | 'feedback_prompt'
  | 'system'
  | 'announcement'
  | 'milestone'
  | 'countdown'
  | 'reminder'
  | 'alert';

export interface FeedEvent {
  id: string;
  organization_id: string;
  run_id: string;
  event_type: FeedEventType;
  content: Record<string, unknown>;
  author_id: string | null;
  target_team_id: string | null;
  created_at: string;
}

// Payload of the `feed_changed` WS event on the run socket.
// Sent after a result is published or withdrawn: notices were replaced or
// removed, which a `feed_event` cannot say, so the page reads its feed again.
export interface FeedChangedEvent {
  run_id: string;
}

export interface FeedEventCreate {
  event_type: FeedEventType;
  content: Record<string, unknown>;
  target_team_id?: string | null;
}

export interface TeamProgressEntry {
  team_id: string;
  team_name: string;
  member_count: number;
  stations_completed: number;
  stations_total: number;
  challenges_submitted: number;
  challenges_total: number;
  challenges_scored: number;
  total_points: number;
  total_max_points: number;
  requested_help: boolean;
}

export interface TeamProgressResponse {
  run_id: string;
  teams: TeamProgressEntry[];
}

/**
 * The body of 409 TEAM_WOULD_DISSOLVE: the step would dissolve the
 * team and was sent without `confirm_dissolution`. Nothing was changed.
 */
export interface TeamWouldDissolve {
  /** The members the team has now, the one to take out included. */
  member_count: number;
  /** The answers that would leave the ranking. */
  answer_count: number;
  /** Whether the run has a live public result. */
  result_published: boolean;
  /** Whether the run has ended. */
  run_ended: boolean;
}

export interface RunHistoryEntry {
  run_id: string;
  run_name: string;
  rallye_title: MultilingualString;
  team_id: string;
  team_name: string;
  started_at: string | null;
  ended_at: string | null;
  state: RunState;
  placement: number | null;
  total_teams: number | null;
  total_points: number;
}

export interface RunHistoryResponse {
  items: RunHistoryEntry[];
}

export interface ChatMessage {
  id: string;
  organization_id: string;
  team_id: string;
  author_id: string | null;
  content: string;
  is_system_message: boolean;
  author_role: 'guest' | 'participant' | 'coordinator' | 'creator' | 'org_admin' | null;
  /** Resolved on the server. Null for system messages and deleted users. */
  author_display_name: string | null;
  created_at: string;
}

export interface ChatMessageCreate {
  content: string;
}

export interface Feedback {
  id: string;
  run_id: string;
  rating: number;
  comment: string | null;
  created_at: string;
}

export interface FeedbackCreate {
  rating: number;
  comment?: string | null;
}

export interface FeedbackSummary {
  average_rating: number;
  total_count: number;
  distribution: Record<number, number>;
}

export interface FileUploadResponse {
  object_key: string;
  url: string;
  content_type: string;
  size: number;
}

// ============================================================================
// Admin — Log Viewer
// ============================================================================

export type LogCategory = 'audit' | 'app' | 'error' | 'access';

export interface LogFilters {
  category?: LogCategory[];
  from_date?: string;
  to_date?: string;
  search?: string;
  /** Event keys whose label matches `search`. An entry with one of them matches the search too. */
  search_event?: string[];
  event_type?: string;
  skip?: number;
  limit?: number;
  /** Sort by time. The server default is newest first. */
  order?: 'asc' | 'desc';
  format?: 'json' | 'csv';
}

export interface LogEntry {
  timestamp: string | null;
  category: string | null;
  event: string | null;
  actor: Record<string, unknown> | null;
  target: Record<string, unknown> | null;
  changes: Record<string, unknown> | null;
  request_id: string | null;
  integrity_hash: string | null;
  organization_id: string | null;
}

export interface LogListResponse {
  items: LogEntry[];
  total: number;
  skip: number;
  limit: number;
}

export interface CoordinatorRequestResponse {
  message: string;
}

// ============================================================================
// Run Analytics — coordinator+ only. Aggregated and free of personal data: no
// display name, e-mail or id of a user anywhere in this shape. Team names are
// allowed, because leaderboards show them already.
// ============================================================================

export interface RunAnalyticsParticipation {
  team_count: number;
  participant_count: number;
  guest_count: number;
  registered_count: number;
}

/** Per-station completion funnel entry. */
export interface RunAnalyticsStation {
  run_station_id: string;
  title: MultilingualString;
  teams_attempted: number;
  teams_scored: number;
}

/** Per-challenge submission/scoring stats. */
export interface RunAnalyticsChallenge {
  run_challenge_id: string;
  run_station_id: string;
  station_title: MultilingualString;
  title: MultilingualString;
  type: ChallengeType;
  submissions: number;
  scored: number;
  /** null until at least one submission is scored */
  avg_points: number | null;
  max_points: number;
  /** Time to the answer over the teams, in seconds. Null without a measured team. */
  time_team_count?: number;
  time_median_seconds?: number | null;
  time_p25_seconds?: number | null;
  time_p75_seconds?: number | null;
  time_min_seconds?: number | null;
  time_max_seconds?: number | null;
}

/** Per-team aggregate summary — team name only, no member-level PII. */
export interface RunAnalyticsTeam {
  team_id: string;
  team_name: string | null;
  member_count: number;
  guest_count: number;
  total_points: number;
  challenges_attempted: number;
  challenges_scored: number;
  /** The team's start, its last answer, and the working time between them. */
  started_at?: string | null;
  last_answer_at?: string | null;
  duration_seconds?: number | null;
  longest_gap_seconds?: number | null;
}

/** Median and spread of the teams' working time, in seconds. */
export interface AnalyticsWorkingTime {
  teams_measured: number;
  median_seconds: number | null;
  p25_seconds: number | null;
  p75_seconds: number | null;
  min_seconds: number | null;
  max_seconds: number | null;
}

/** One 15-minute submissions bucket for the timeline chart. */
export interface RunAnalyticsTimelineBucket {
  bucket_start: string;
  submissions: number;
}

/**
 * Aggregated feedback of the run, in the shape of FeedbackSummary. If the
 * feedback model stores free text only, the aggregate is a count
 * (`average_rating: 0`, `distribution: {}`).
 */
export type RunAnalyticsFeedback = FeedbackSummary;

/** GET /api/orgs/{slug}/runs/{run_id}/analytics response body. */
export interface RunAnalytics {
  run_id: string;
  participation: RunAnalyticsParticipation;
  stations: RunAnalyticsStation[];
  challenges: RunAnalyticsChallenge[];
  teams: RunAnalyticsTeam[];
  timeline: RunAnalyticsTimelineBucket[];
  /** Null when the run has no feedback yet — the section is hidden. */
  feedback: RunAnalyticsFeedback | null;
  /** Optional, so an older server's answer still renders. */
  started_at?: string | null;
  ended_at?: string | null;
  working_time?: AnalyticsWorkingTime | null;
}

/**
 * `table` query param of GET .../analytics/export. `teams` and `challenges`
 * keep their columns.
 */
export type RunAnalyticsExportTable = 'teams' | 'challenges' | 'working_time' | 'task_times';

// ---------------------------------------------------------------------------
// Date-range analytics — org-scoped timeseries
// ---------------------------------------------------------------------------

export type AnalyticsBucketGranularity = 'day' | 'week' | 'month';

/** The eight scalar metrics for a bucket (or the range total). */
export interface AnalyticsTimeseriesMetrics {
  run_count: number;
  participant_count: number;
  guest_count: number;
  registered_count: number;
  team_count: number;
  submission_count: number;
  scored_count: number;
  unscored_count: number;
}

/** Points and working time of a bucket or of the whole period. */
export interface AnalyticsPeriodPoints {
  total_points: number;
  avg_points_per_team: number | null;
  avg_points_per_answer: number | null;
  points_percent: number | null;
  teams_measured: number;
  median_duration_seconds: number | null;
}

export interface AnalyticsTimeseriesBucket extends AnalyticsTimeseriesMetrics, Partial<AnalyticsPeriodPoints> {
  bucket_start: string;
}

/** One task over every run of the period that holds it. */
export interface AnalyticsPeriodTask {
  key: string;
  station_title: MultilingualString;
  title: MultilingualString;
  type: ChallengeType;
  run_count: number;
  submissions: number;
  scored: number;
  avg_points: number | null;
  max_points: number | null;
  time_team_count: number;
  time_median_seconds: number | null;
  time_p25_seconds: number | null;
  time_p75_seconds: number | null;
}

/** One team of a run of the period. Team level only. */
export interface AnalyticsPeriodTeam {
  run_id: string;
  run_name: string;
  run_date: string | null;
  team_id: string;
  team_name: string | null;
  points: number;
  answers: number;
  scored: number;
  duration_seconds: number | null;
}

export interface AnalyticsTimeseriesRange {
  date_from: string;
  date_to: string;
  bucket: AnalyticsBucketGranularity;
}

/** GET /api/orgs/{slug}/analytics/timeseries response body. */
export interface AnalyticsTimeseries {
  range: AnalyticsTimeseriesRange;
  totals: AnalyticsTimeseriesMetrics & Partial<AnalyticsPeriodPoints>;
  buckets: AnalyticsTimeseriesBucket[];
  /** Optional, so an older server's answer still renders. */
  tasks?: AnalyticsPeriodTask[];
  teams?: AnalyticsPeriodTeam[];
}

/** `table` query param of the period CSV. `metrics` is the server's default. */
export type AnalyticsPeriodExportTable = 'metrics' | 'points' | 'tasks' | 'teams';

// ---------------------------------------------------------------------------
// Run comparison: two to six runs side by side
// ---------------------------------------------------------------------------

export interface AnalyticsCompareRun {
  run_id: string;
  name: string;
  state: RunState;
  run_date: string | null;
  started_at: string | null;
  ended_at: string | null;
  station_count: number;
  task_count: number;
  team_count: number;
  participant_count: number;
  guest_count: number;
  registered_count: number;
  submission_count: number;
  scored_count: number;
  completion_percent: number | null;
  teams_finished: number;
  total_points: number;
  achievable_points: number;
  avg_points_per_team: number | null;
  median_points_per_team: number | null;
  points_percent: number | null;
  working_time: AnalyticsWorkingTime;
}

interface AnalyticsCompareStationCell {
  team_count: number;
  teams_attempted: number;
  percent: number | null;
}

export interface AnalyticsCompareStation {
  key: string;
  title: MultilingualString;
  /** run_id → cell. A run that does not hold the station has no cell. */
  cells: Record<string, AnalyticsCompareStationCell>;
}

interface AnalyticsCompareTaskCell {
  team_count: number;
  submissions: number;
  percent: number | null;
  scored: number;
  avg_points: number | null;
  max_points: number | null;
  time_team_count: number;
  time_median_seconds: number | null;
}

export interface AnalyticsCompareTask {
  key: string;
  station_key: string;
  station_title: MultilingualString;
  title: MultilingualString;
  type: ChallengeType;
  cells: Record<string, AnalyticsCompareTaskCell>;
}

/** GET /api/orgs/{slug}/analytics/compare response body. */
export interface AnalyticsComparison {
  runs: AnalyticsCompareRun[];
  stations: AnalyticsCompareStation[];
  tasks: AnalyticsCompareTask[];
}

export type AnalyticsCompareExportTable = 'runs' | 'stations' | 'tasks';

/** The metric keys that can be toggled as stacked series. */
export type AnalyticsMetricKey = keyof AnalyticsTimeseriesMetrics;

// ---------------------------------------------------------------------------
// Instance-operator auth and organizations
// ---------------------------------------------------------------------------

/** POST /api/instance/auth/login body. The code is only needed once TOTP is confirmed. */
export interface InstanceLoginRequest {
  email: string;
  password: string;
  totp_code?: string;
}

/** POST /api/instance/auth/login response — `totp_setup_required` until the
 *  enrollment is confirmed through `/totp/confirm`; the provisioning URI is
 *  handed out on the first login only (null on later unconfirmed logins). */
export interface InstanceLoginResponse {
  status: 'success' | 'totp_setup_required';
  totp_setup_required: boolean;
  provisioning_uri: string | null;
  email?: string | null;
}

/** POST /api/instance/auth/totp/confirm body (stateless: email and password again). */
export interface InstanceTotpConfirmRequest {
  email: string;
  password: string;
  totp_code: string;
}

/** POST /api/instance/auth/totp/confirm response — the operator is logged in. */
export interface InstanceTotpConfirmResponse {
  status: string;
  email: string;
}

/** GET /api/instance/auth/me response. */
export interface InstanceMeResponse {
  id: string;
  email: string;
  totp_confirmed: boolean;
}

/** POST /api/instance/auth/logout response. */
export interface InstanceLogoutResponse {
  message: string;
}

/** One row of GET /api/instance/organizations — counts only, never member data. */
export interface InstanceOrganizationListItem {
  name: string;
  slug: string;
  content_languages: string[];
  created_at: string;
  is_archived: boolean;
  user_count: number;
  rallye_count: number;
  run_count: number;
  org_admin_count: number;
  pending_admin_invitation_count: number;
  active_run_count: number;
  /** Latest submission or run start; null when neither exists yet. */
  last_activity_at: string | null;
  /** When a scheduled deletion will run; null when none is scheduled. */
  deletion_due_at?: string | null;
}

/** GET /api/instance/organizations response. */
export interface InstanceOrganizationListResponse {
  items: InstanceOrganizationListItem[];
  total: number;
}

/** POST /api/instance/organizations body — slug `^[a-z0-9-]+$`, the first
 *  language is the language of the first-admin invitation mail. */
export interface InstanceOrganizationCreateRequest {
  name: string;
  slug: string;
  content_languages: string[];
  first_admin_email: string;
}

/** POST /api/instance/organizations response (201). */
export interface InstanceOrganizationResponse {
  id: string;
  name: string;
  slug: string;
  content_languages: string[];
  created_at: string;
  is_archived: boolean;
}

/** POST /api/instance/organizations/{slug}/archive|restore response. */
export interface InstanceOrganizationActionResponse {
  slug: string;
  is_archived: boolean;
  message: string;
}

/** PATCH /api/instance/organizations/{slug} body — the slug is never accepted here. */
export interface InstanceOrganizationUpdateRequest {
  name?: string;
  content_languages?: string[];
}

/** POST /api/instance/organizations/{slug}/deletion body.
 *  `confirm_slug` must match the organization's own slug exactly. */
export interface InstanceOrganizationDeletionRequest {
  confirm_slug: string;
}

/** POST /api/instance/organizations/{slug}/deletion response — the deletion is scheduled, not immediate. */
export interface InstanceOrganizationDeletionResponse {
  slug: string;
  deletion_due_at: string;
  message: string;
}

/** POST /api/instance/organizations/{slug}/admin-invitations body. */
export interface InstanceOrgAdminInvitationRequest {
  email: string;
  language?: 'de' | 'en';
  /** Current org admins the invited person takes over from. */
  replace_admin_ids?: string[];
}

/** One org admin as the operator sees it, to pick whom to replace. */
export interface InstanceOrgAdminSummary {
  id: string;
  email: string | null;
  display_name: string;
  is_active: boolean;
  last_login: string | null;
}

export interface TakeoverNotice {
  accepted_at: string | null;
  replaced: InstanceOrgAdminSummary[];
}

/** POST /api/instance/organizations/{slug}/admin-invitations response (201). */
export interface InstanceOrgAdminInvitationResponse {
  id: string;
  email: string;
  language: string;
  invited_by_name: string;
  created_at: string;
  expires_at: string;
  mail_delivered: boolean;
}

/** POST /api/instance/auth/password body. */
export interface InstancePasswordChangeRequest {
  old_password: string;
  new_password: string;
}

/** POST /api/instance/auth/password response. */
export interface InstancePasswordChangeResponse {
  message: string;
}

// ---------------------------------------------------------------------------
// Instance audit log and legal texts
// ---------------------------------------------------------------------------

/** One `instance.*` audit event in the operator's own log — carries no organization data. */
export interface InstanceAuditLogEntryResponse {
  timestamp: string | null;
  event: string | null;
  operator_id: string | null;
  operator_email: string | null;
  target_slug: string | null;
  /** The operator account a change concerned. */
  target_email?: string | null;
  /** 'cli' when a server command made the change. */
  actor_type?: string | null;
}

/** GET /api/instance/audit response. */
export interface InstanceAuditLogListResponse {
  items: InstanceAuditLogEntryResponse[];
  total: number;
  offset: number;
  limit: number;
}

/** GET/PUT /api/instance/legal response — every field null until the first save. */
export interface InstanceLegalResponse {
  legal_imprint: string | null;
  legal_privacy: string | null;
  legal_accessibility: string | null;
  updated_at: string | null;
  /** Days a backup of the installation can still hold deleted data (effective value). */
  backup_retention_days: number;
  /** 'default' means the operator has not stated the period yet. */
  backup_retention_source: 'instance' | 'default';
  /** The installation's default retention period per class, in days. */
  retention_days: Record<string, number>;
  /** Longest of the app, error and access log periods, in days. */
  technical_log_days: number;
  /** Security log retention period, in days. */
  audit_log_days: number;
  /** Days after the start (or creation) at which a run that never ended is swept. */
  abandoned_run_days: number;
  /** Days a published result link stays resolvable, restarted on every re-publish. */
  result_link_days: number;
  /** The tile server both maps load from, for the privacy template. */
  map_tile_url: string;
  /** Where the address search takes its hits from, as /api/geocode/config answers. */
  address_search_sources: ('local' | 'nominatim' | 'photon')[];
}

/** PUT /api/instance/legal body — an omitted field is left untouched, an explicit null clears it. */
export interface InstanceLegalUpdateRequest {
  legal_imprint?: string | null;
  legal_privacy?: string | null;
  legal_accessibility?: string | null;
}
