import apiClient, { ApiError } from './client';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '';

interface OrgMediaUploadResponse {
  /** Object key inside the bucket, `{org_id}/media/{uuid}_{name}`. */
  key: string;
  /** The application route the document references, `/api/orgs/{slug}/media/{key}`. */
  url: string;
}

/** Image types the media endpoint accepts. Mirrors `routers/media.py`. */
export const ORG_MEDIA_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'];

// The coded refusals of an image upload by a creator: the rich-text upload here
// and the station title image share `clean_image_upload` on the server. The
// server's `detail` is an English literal, so the pages show these keys instead.
const IMAGE_REFUSAL_KEYS: Record<string, string> = {
  IMAGE_NOT_DECODABLE: 'content:imageRefused.notDecodable',
  IMAGE_TOO_LARGE: 'content:imageRefused.tooLarge',
};

/**
 * The i18n key (with namespace) that says why the server refused an image, or
 * null when the failure carries no refusal code this build knows.
 */
export function imageRefusalKey(err: unknown): string | null {
  return imageRefusalKeyForCode(imageRefusalCode(err));
}

/**
 * The refusal code of a failed image upload, or null when the failure carries
 * none this build knows. A page that navigates away after the failure carries
 * the code, and the next page maps it with `imageRefusalKeyForCode`.
 */
export function imageRefusalCode(err: unknown): string | null {
  if (!(err instanceof ApiError) || !err.errorCode) return null;
  return Object.hasOwn(IMAGE_REFUSAL_KEYS, err.errorCode) ? err.errorCode : null;
}

/** The i18n key (with namespace) for a refusal code, or null for anything else. */
export function imageRefusalKeyForCode(code: unknown): string | null {
  if (typeof code !== 'string' || !Object.hasOwn(IMAGE_REFUSAL_KEYS, code)) return null;
  return IMAGE_REFUSAL_KEYS[code];
}

type OrgMediaRefusalListener = (messageKey: string) => void;
const refusalListeners = new Set<OrgMediaRefusalListener>();

/**
 * Hear about a coded refusal of `uploadOrgMedia`. The image dialog of the
 * editor shows one generic message for every failed upload. The editor that
 * opened it listens here to tell the creator the reason. Returns the unsubscribe.
 */
export function onOrgMediaRefused(listener: OrgMediaRefusalListener): () => void {
  refusalListeners.add(listener);
  return () => {
    refusalListeners.delete(listener);
  };
}

/**
 * Upload an image for use inside rich text of the organisation.
 * Creator role or above. The returned `url` is what the editor stores as `src`.
 */
export async function uploadOrgMedia(
  orgSlug: string,
  file: File,
  onProgress?: (percent: number) => void,
): Promise<OrgMediaUploadResponse> {
  const form = new FormData();
  form.append('file', file);
  // No explicit Content-Type: axios sets the multipart boundary itself.
  try {
    const { data } = await apiClient.post<OrgMediaUploadResponse>(
      `/api/orgs/${orgSlug}/media/`,
      form,
      onProgress
        ? {
            onUploadProgress: (e) => {
              if (e.total) onProgress(Math.round((e.loaded / e.total) * 100));
            },
          }
        : undefined,
    );
    return data;
  } catch (err) {
    const key = imageRefusalKey(err);
    if (key) refusalListeners.forEach((listener) => listener(key));
    throw err;
  }
}

/** Absolute URL for a media route when the API lives on another origin (dev only). */
export function resolveMediaUrl(src: string): string {
  return API_BASE && src.startsWith('/api/') ? `${API_BASE}${src}` : src;
}
