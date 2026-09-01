import i18n from '../i18n';
import apiClient, { ApiError } from './client';
import type {
  InstanceOrgAdminSummary,
  InstanceAuditLogListResponse,
  InstanceLegalResponse,
  InstanceLegalUpdateRequest,
  InstanceLoginRequest,
  InstanceLoginResponse,
  InstanceLogoutResponse,
  InstanceMeResponse,
  InstanceOrgAdminInvitationRequest,
  InstanceOrgAdminInvitationResponse,
  InstanceOrganizationActionResponse,
  InstanceOrganizationCreateRequest,
  InstanceOrganizationDeletionRequest,
  InstanceOrganizationDeletionResponse,
  InstanceOrganizationListResponse,
  InstanceOrganizationResponse,
  InstanceOrganizationUpdateRequest,
  InstancePasswordChangeRequest,
  InstancePasswordChangeResponse,
  InstanceTotpConfirmRequest,
  InstanceTotpConfirmResponse,
} from './types';

// ============================================================================
// Instance-operator auth and organizations
// ============================================================================

export async function instanceLogin(body: InstanceLoginRequest): Promise<InstanceLoginResponse> {
  const { data } = await apiClient.post<InstanceLoginResponse>('/api/instance/auth/login', body);
  return data;
}

export async function instanceTotpConfirm(
  body: InstanceTotpConfirmRequest,
): Promise<InstanceTotpConfirmResponse> {
  const { data } = await apiClient.post<InstanceTotpConfirmResponse>(
    '/api/instance/auth/totp/confirm',
    body,
  );
  return data;
}

export async function instanceMe(): Promise<InstanceMeResponse> {
  const { data } = await apiClient.get<InstanceMeResponse>('/api/instance/auth/me');
  return data;
}

export async function instanceLogout(): Promise<InstanceLogoutResponse> {
  const { data } = await apiClient.post<InstanceLogoutResponse>('/api/instance/auth/logout');
  return data;
}

export async function instancePasswordChange(
  body: InstancePasswordChangeRequest,
): Promise<InstancePasswordChangeResponse> {
  const { data } = await apiClient.post<InstancePasswordChangeResponse>(
    '/api/instance/auth/password',
    body,
  );
  return data;
}

export async function listInstanceOrganizations(): Promise<InstanceOrganizationListResponse> {
  const { data } = await apiClient.get<InstanceOrganizationListResponse>(
    '/api/instance/organizations',
  );
  return data;
}

export async function createInstanceOrganization(
  body: InstanceOrganizationCreateRequest,
): Promise<InstanceOrganizationResponse> {
  const { data } = await apiClient.post<InstanceOrganizationResponse>(
    '/api/instance/organizations',
    body,
  );
  return data;
}

export async function updateInstanceOrganization(
  slug: string,
  body: InstanceOrganizationUpdateRequest,
): Promise<InstanceOrganizationResponse> {
  const { data } = await apiClient.patch<InstanceOrganizationResponse>(
    `/api/instance/organizations/${slug}`,
    body,
  );
  return data;
}

/** The organization's admins, to pick whom an invitation replaces. */
export async function listInstanceOrgAdmins(slug: string): Promise<InstanceOrgAdminSummary[]> {
  const { data } = await apiClient.get<{ items: InstanceOrgAdminSummary[] }>(
    `/api/instance/organizations/${slug}/admins`,
  );
  return data.items;
}

export async function inviteInstanceOrgAdmin(
  slug: string,
  body: InstanceOrgAdminInvitationRequest,
): Promise<InstanceOrgAdminInvitationResponse> {
  const { data } = await apiClient.post<InstanceOrgAdminInvitationResponse>(
    `/api/instance/organizations/${slug}/admin-invitations`,
    body,
  );
  return data;
}

export async function archiveInstanceOrganization(
  slug: string,
): Promise<InstanceOrganizationActionResponse> {
  const { data } = await apiClient.post<InstanceOrganizationActionResponse>(
    `/api/instance/organizations/${slug}/archive`,
  );
  return data;
}

export async function restoreInstanceOrganization(
  slug: string,
): Promise<InstanceOrganizationActionResponse> {
  const { data } = await apiClient.post<InstanceOrganizationActionResponse>(
    `/api/instance/organizations/${slug}/restore`,
  );
  return data;
}

export async function requestInstanceOrganizationDeletion(
  slug: string,
  body: InstanceOrganizationDeletionRequest,
): Promise<InstanceOrganizationDeletionResponse> {
  const { data } = await apiClient.post<InstanceOrganizationDeletionResponse>(
    `/api/instance/organizations/${slug}/deletion`,
    body,
  );
  return data;
}

export async function listInstanceAuditLog(params: {
  event?: string;
  offset?: number;
  limit?: number;
}): Promise<InstanceAuditLogListResponse> {
  const { data } = await apiClient.get<InstanceAuditLogListResponse>('/api/instance/audit', {
    params,
  });
  return data;
}

export async function getInstanceLegal(): Promise<InstanceLegalResponse> {
  const { data } = await apiClient.get<InstanceLegalResponse>('/api/instance/legal');
  return data;
}

export async function updateInstanceLegal(
  body: InstanceLegalUpdateRequest,
): Promise<InstanceLegalResponse> {
  const { data } = await apiClient.put<InstanceLegalResponse>('/api/instance/legal', body);
  return data;
}

/**
 * Map a failed operator-API call to a message from the `instance` namespace.
 * The backend answers coded conflicts (`ORG_SLUG_TAKEN`,
 * `EMAIL_HAS_PENDING_INVITATION`); anything else — including an uncoded
 * status — falls back to the generic message, so an English backend `detail`
 * never reaches the localized page.
 */
export function instanceApiErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const key = `instance:errors.${err.errorCode}`;
    if (err.errorCode && i18n.exists(key)) return i18n.t(key);
    return i18n.t('instance:errors.generic');
  }
  return i18n.t('instance:errors.generic');
}
