import apiClient from './client';
import type { RunHistoryResponse } from './types';

export async function getMyRunHistory(): Promise<RunHistoryResponse> {
  const { data } = await apiClient.get<RunHistoryResponse>('/api/me/run-history');
  return data;
}

/**
 * GDPR Art. 20 data portability: the caller's own data as a JSON Blob.
 * Registered users only (guests get 403).
 */
export async function exportMyData(): Promise<Blob> {
  const { data } = await apiClient.get('/api/me/export', { responseType: 'blob' });
  return data as Blob;
}
