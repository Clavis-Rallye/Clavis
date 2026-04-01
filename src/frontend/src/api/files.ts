import apiClient from './client';
import type { FileUploadResponse } from './types';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '';

export async function uploadFile(
  orgSlug: string,
  runId: string,
  file: File,
  onProgress?: (percent: number) => void,
): Promise<FileUploadResponse> {
  const form = new FormData();
  form.append('file', file);
  // No Content-Type: axios sets `multipart/form-data` with the boundary.
  // A fixed header drops the boundary and breaks the upload.
  const { data } = await apiClient.post<FileUploadResponse>(
    `/api/orgs/${orgSlug}/runs/${runId}/submissions/upload/`,
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
}

/**
 * The WebP variants the backend generates beside a decodable photo: `thumb`
 * is the 400 px list thumbnail, `display` the 1600 px full view. The server
 * answers the original for a missing variant, so callers need not know which
 * variants exist.
 */
type FileVariant = 'thumb' | 'display';

export function getFileUrl(key: string, variant?: FileVariant): string {
  const base = `${API_BASE}/api/files/${key}`;
  return variant ? `${base}?variant=${variant}` : base;
}

/**
 * The URL the browser loads for an API path the backend hands out, such as a
 * station title image (`/api/files/{key}`). The path is same-origin in
 * production. With VITE_API_URL set, it gets the same base as answer media.
 */
export function apiPathUrl(path: string): string {
  return path.startsWith('/') ? `${API_BASE}${path}` : path;
}
