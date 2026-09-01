import apiClient from '../api/client';

export type LegalKind = 'imprint' | 'privacy' | 'accessibility';

/** `GET /api/public/legal/{kind}`: the best-known text and its source. */
export interface PublicLegalText {
  kind: LegalKind;
  source: 'organization' | 'instance' | 'none';
  text: string | null;
}

/**
 * Fetch the legal text for `kind`. The server prefers the organization's own
 * text when `orgSlug` has one. Returns `null` on any failure, and callers then
 * show their bundled placeholder.
 */
export async function fetchLegalText(
  kind: LegalKind,
  orgSlug?: string | null,
  signal?: AbortSignal,
): Promise<PublicLegalText | null> {
  try {
    const { data } = await apiClient.get<PublicLegalText>(
      `/api/public/legal/${encodeURIComponent(kind)}`,
      { params: orgSlug ? { org: orgSlug } : {}, ...(signal ? { signal } : {}) },
    );
    return data;
  } catch {
    return null;
  }
}
