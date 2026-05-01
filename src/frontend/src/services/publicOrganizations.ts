import apiClient from '../api/client';

export interface PublicOrganization {
  slug: string;
  name: string;
}

// A slow server gets a fair chance before the picker offers a retry.
const FETCH_TIMEOUT_MS = 8000;

/**
 * List all active organisations. The OrgPicker of ProfileSetupStep uses it
 * when the user opens `/register` directly, without a slug from Welcome.
 *
 * Throws on any failure, so the caller can tell "the server did not answer"
 * from "no organization exists". `[]` for both would make a slow server read
 * as an empty installation.
 */
export async function listAll(
  signal?: AbortSignal,
): Promise<PublicOrganization[]> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener('abort', () => controller.abort(), { once: true });
  }

  try {
    const { data } = await apiClient.get<{ organizations: PublicOrganization[] }>(
      '/api/public/organizations',
      { signal: controller.signal },
    );
    return data.organizations ?? [];
  } finally {
    clearTimeout(timeoutId);
  }
}
