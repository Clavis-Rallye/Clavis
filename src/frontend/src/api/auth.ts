import apiClient from './client';
import type {
  RegisterRequest,
  RegisterResponse,
  ProfileUpdateRequest,
  ProfileUpdateResponse,
  UserResponse,
  TotpAuthenticateRequest,
  TotpAuthenticateResponse,
  TotpDisableRequest,
  PasswordChangeRequest,
  PasswordForgotRequest,
  PasswordResetRequest,
  InvitationAcceptRequest,
  InvitationAcceptResponse,
  InvitationPreviewResponse,
} from './types';

export async function register(body: RegisterRequest): Promise<RegisterResponse> {
  const { data } = await apiClient.post<RegisterResponse>('/api/auth/register', body);
  return data;
}

export async function getProfile(): Promise<UserResponse> {
  const { data } = await apiClient.get<UserResponse>('/api/auth/me');
  return data;
}

export async function updateProfile(body: ProfileUpdateRequest): Promise<ProfileUpdateResponse> {
  const { data } = await apiClient.put<ProfileUpdateResponse>('/api/auth/profile', body);
  return data;
}

export async function changePassword(body: PasswordChangeRequest): Promise<{ message: string }> {
  const { data } = await apiClient.post<{ message: string }>('/api/auth/password-change', body);
  return data;
}

export async function requestPasswordReset(body: PasswordForgotRequest): Promise<{ message: string }> {
  const { data } = await apiClient.post<{ message: string }>('/api/auth/password-forgot', body);
  return data;
}

export async function resetPassword(body: PasswordResetRequest): Promise<{ message: string }> {
  const { data } = await apiClient.post<{ message: string }>('/api/auth/password-reset', body);
  return data;
}

export async function disableTotp(body: TotpDisableRequest): Promise<{ message: string }> {
  const { data } = await apiClient.delete<{ message: string }>('/api/auth/2fa/totp', { data: body });
  return data;
}

export async function verify2faCode(body: TotpAuthenticateRequest): Promise<TotpAuthenticateResponse> {
  const { data } = await apiClient.post<TotpAuthenticateResponse>('/api/auth/2fa/totp/authenticate', body);
  return data;
}


// ============================================================================
// Invitations — unauthenticated: the invitee has no account yet
// ============================================================================

/**
 * Read what an invitation link offers, so the accept page can name the
 * organisation, the address and the role before asking for a password.
 */
export async function previewInvitation(token: string): Promise<InvitationPreviewResponse> {
  const { data } = await apiClient.get<InvitationPreviewResponse>(
    `/api/invitations/${encodeURIComponent(token)}`,
  );
  return data;
}

/** Redeem an invitation: create the account with the invited role. */
export async function acceptInvitation(
  token: string,
  body: InvitationAcceptRequest,
): Promise<InvitationAcceptResponse> {
  const { data } = await apiClient.post<InvitationAcceptResponse>(
    `/api/invitations/${encodeURIComponent(token)}/accept`,
    body,
  );
  return data;
}
