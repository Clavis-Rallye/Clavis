import apiClient, { API_BASE_URL } from './client';
import type {
  TakeoverNotice,
  CoordinatorRequestResponse,
  InvitationCreateRequest,
  InvitationListResponse,
  InvitationResponse,
  LogFilters,
  LogListResponse,
  MailStatusResponse,
  MailTestRequest,
  MailTestResponse,
  OrganizationResponse,
  OrganizationUpdateRequest,
  RetentionConfigResponse,
  RetentionConfigUpdate,
  UserCreateRequest,
  UserListResponse,
  UserResponse,
  UserUpdateRequest,
} from './types';

// ============================================================================
// Admin — User Management
// ============================================================================

interface ListUsersFilters {
  skip?: number;
  limit?: number;
  role?: 'guest' | 'participant' | 'coordinator' | 'creator' | 'org_admin';
  is_active?: boolean;
  include_deleted?: boolean;
}

export async function listUsers(orgSlug: string, filters: ListUsersFilters = {}): Promise<UserListResponse> {
  const params: Record<string, unknown> = {};
  if (filters.skip !== undefined) params['skip'] = filters.skip;
  if (filters.limit !== undefined) params['limit'] = filters.limit;
  if (filters.role !== undefined) params['role'] = filters.role;
  if (filters.is_active !== undefined) params['is_active'] = filters.is_active;
  if (filters.include_deleted !== undefined) params['include_deleted'] = filters.include_deleted;
  const { data } = await apiClient.get<UserListResponse>(`/api/orgs/${orgSlug}/users/`, { params });
  return data;
}

export async function createUser(orgSlug: string, body: UserCreateRequest): Promise<UserResponse> {
  const { data } = await apiClient.post<UserResponse>(`/api/orgs/${orgSlug}/users/`, body);
  return data;
}

export async function updateUser(
  orgSlug: string,
  userId: string,
  body: UserUpdateRequest,
): Promise<UserResponse> {
  const { data } = await apiClient.put<UserResponse>(`/api/orgs/${orgSlug}/users/${userId}`, body);
  return data;
}

export async function deleteUser(
  orgSlug: string,
  userId: string,
  mode: 'grace' | 'immediate' = 'grace',
): Promise<void> {
  await apiClient.delete(`/api/orgs/${orgSlug}/users/${userId}`, { params: { mode } });
}

export async function restoreUser(orgSlug: string, userId: string): Promise<UserResponse> {
  const { data } = await apiClient.post<UserResponse>(`/api/orgs/${orgSlug}/users/${userId}/restore`);
  return data;
}

// ============================================================================
// Admin — Organization Settings
// ============================================================================

export async function getOrg(orgSlug: string): Promise<OrganizationResponse> {
  const { data } = await apiClient.get<OrganizationResponse>(`/api/orgs/${orgSlug}`);
  return data;
}

export async function updateOrg(
  orgSlug: string,
  body: OrganizationUpdateRequest,
): Promise<OrganizationResponse> {
  const { data } = await apiClient.put<OrganizationResponse>(`/api/orgs/${orgSlug}`, body);
  return data;
}

// ============================================================================
// Admin — Retention Configuration
// ============================================================================

export async function getRetentionConfig(orgSlug: string): Promise<RetentionConfigResponse> {
  const { data } = await apiClient.get<RetentionConfigResponse>(`/api/orgs/${orgSlug}/admin/retention`);
  return data;
}

export async function updateRetentionConfig(
  orgSlug: string,
  body: RetentionConfigUpdate,
): Promise<RetentionConfigResponse> {
  const { data } = await apiClient.put<RetentionConfigResponse>(`/api/orgs/${orgSlug}/admin/retention`, body);
  return data;
}

// ============================================================================
// Admin — Log Viewer
// ============================================================================

/** The query parameters the log view and its export share. */
function logParams(filters: Omit<LogFilters, 'format'>): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  if (filters.category && filters.category.length > 0) params['category'] = filters.category;
  if (filters.from_date !== undefined) params['from_date'] = filters.from_date;
  if (filters.to_date !== undefined) params['to_date'] = filters.to_date;
  if (filters.search !== undefined) params['search'] = filters.search;
  if (filters.search_event && filters.search_event.length > 0) params['search_event'] = filters.search_event;
  if (filters.event_type !== undefined) params['event_type'] = filters.event_type;
  if (filters.skip !== undefined) params['skip'] = filters.skip;
  if (filters.limit !== undefined) params['limit'] = filters.limit;
  if (filters.order !== undefined) params['order'] = filters.order;
  return params;
}

export async function listLogs(orgSlug: string, filters: LogFilters = {}): Promise<LogListResponse> {
  const { data } = await apiClient.get<LogListResponse>(`/api/orgs/${orgSlug}/admin/logs`, {
    params: logParams(filters),
  });
  return data;
}

/**
 * The address of the CSV export for the filters of the view. The server
 * streams every matching entry of the period, oldest first, and ignores
 * paging. The browser downloads it straight to disk, so a long period is
 * never held in the page's memory.
 */
export function logsExportUrl(
  orgSlug: string,
  filters: Omit<LogFilters, 'format' | 'skip' | 'limit' | 'order'> = {},
): string {
  const query = new URLSearchParams({ format: 'csv' });
  for (const [key, value] of Object.entries(logParams(filters))) {
    for (const item of Array.isArray(value) ? value : [value]) query.append(key, String(item));
  }
  return `${API_BASE_URL}/api/orgs/${encodeURIComponent(orgSlug)}/admin/logs?${query.toString()}`;
}

// ============================================================================
// Coordinator Request
// ============================================================================

export async function requestCoordinator(
  orgSlug: string,
  runId: string,
  teamId: string,
): Promise<CoordinatorRequestResponse> {
  const { data } = await apiClient.post<CoordinatorRequestResponse>(
    `/api/orgs/${orgSlug}/runs/${runId}/teams/${teamId}/chat/request-coordinator`,
  );
  return data;
}

/**
 * A coordinator leaves the team chat and closes the team's assist window. A
 * coordinator may leave at any time. Idempotent on a closed window.
 */
export async function leaveCoordinatorChat(
  orgSlug: string,
  runId: string,
  teamId: string,
): Promise<CoordinatorRequestResponse> {
  const { data } = await apiClient.post<CoordinatorRequestResponse>(
    `/api/orgs/${orgSlug}/runs/${runId}/teams/${teamId}/chat/leave-coordinator`,
  );
  return data;
}


// ============================================================================
// Admin — Invitations
// ============================================================================

/** The accounts the caller took over through an operator invitation. */
export async function getTakeoverNotice(orgSlug: string): Promise<TakeoverNotice> {
  const { data } = await apiClient.get<TakeoverNotice>(`/api/orgs/${orgSlug}/invitations/takeover`);
  return data;
}

export async function listInvitations(orgSlug: string): Promise<InvitationListResponse> {
  const { data } = await apiClient.get<InvitationListResponse>(`/api/orgs/${orgSlug}/invitations/`);
  return data;
}

export async function createInvitation(
  orgSlug: string,
  body: InvitationCreateRequest,
): Promise<InvitationResponse> {
  const { data } = await apiClient.post<InvitationResponse>(`/api/orgs/${orgSlug}/invitations/`, body);
  return data;
}

export async function resendInvitation(
  orgSlug: string,
  invitationId: string,
): Promise<InvitationResponse> {
  const { data } = await apiClient.post<InvitationResponse>(
    `/api/orgs/${orgSlug}/invitations/${invitationId}/resend`,
  );
  return data;
}

export async function revokeInvitation(orgSlug: string, invitationId: string): Promise<void> {
  await apiClient.delete(`/api/orgs/${orgSlug}/invitations/${invitationId}`);
}

/**
 * Ask the backend to mail this user a password-reset link. The link itself is
 * never returned — only the user's inbox ever sees it.
 */
export async function sendPasswordReset(orgSlug: string, userId: string): Promise<{ message: string }> {
  const { data } = await apiClient.post<{ message: string }>(
    `/api/orgs/${orgSlug}/users/${userId}/send-password-reset`,
  );
  return data;
}

// ============================================================================
// Admin — Mail configuration probe
// ============================================================================

export async function getMailStatus(orgSlug: string): Promise<MailStatusResponse> {
  const { data } = await apiClient.get<MailStatusResponse>(`/api/orgs/${orgSlug}/admin/mail/status`);
  return data;
}

export async function sendTestMail(orgSlug: string, body: MailTestRequest): Promise<MailTestResponse> {
  const { data } = await apiClient.post<MailTestResponse>(`/api/orgs/${orgSlug}/admin/mail/test`, body);
  return data;
}

// ============================================================================
// Admin — Media purge
// ============================================================================

export type MediaPurgeType = 'photo' | 'video' | 'audio';

export interface MediaSummaryResponse {
  photo: number;
  video: number;
  audio: number;
}

interface MediaPurgeRequest {
  type: MediaPurgeType;
  confirm_slug: string;
}

interface MediaPurgeResponse {
  type: MediaPurgeType;
  purged: number;
}

export async function getMediaSummary(orgSlug: string): Promise<MediaSummaryResponse> {
  const { data } = await apiClient.get<MediaSummaryResponse>(
    `/api/orgs/${orgSlug}/admin/media/summary`,
  );
  return data;
}

export async function purgeOrgMedia(
  orgSlug: string,
  body: MediaPurgeRequest,
): Promise<MediaPurgeResponse> {
  const { data } = await apiClient.post<MediaPurgeResponse>(
    `/api/orgs/${orgSlug}/admin/media/purge`,
    body,
  );
  return data;
}
