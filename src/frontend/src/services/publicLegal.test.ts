import { describe, it, expect, vi, beforeEach } from 'vitest';
import apiClient from '../api/client';
import { fetchLegalText } from './publicLegal';

vi.mock('../api/client', async (importActual) => {
  const actual = await importActual<typeof import('../api/client')>();
  return { ...actual, default: { get: vi.fn() } };
});

const mockGet = apiClient.get as unknown as ReturnType<typeof vi.fn>;

describe('publicLegal.fetchLegalText', () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  it('fetches without an org param when no slug is given', async () => {
    const payload = { kind: 'privacy', source: 'instance', text: '<p>Instance text</p>' };
    mockGet.mockResolvedValueOnce({ data: payload });

    await expect(fetchLegalText('privacy')).resolves.toEqual(payload);
    expect(mockGet).toHaveBeenCalledWith('/api/public/legal/privacy', { params: {} });
  });

  it('passes the org slug as a query param when given', async () => {
    const payload = { kind: 'privacy', source: 'organization', text: '<p>Org text</p>' };
    mockGet.mockResolvedValueOnce({ data: payload });

    await expect(fetchLegalText('privacy', 'test-org')).resolves.toEqual(payload);
    expect(mockGet).toHaveBeenCalledWith('/api/public/legal/privacy', {
      params: { org: 'test-org' },
    });
  });

  it('returns the none source with a null text untouched', async () => {
    const payload = { kind: 'accessibility', source: 'none', text: null };
    mockGet.mockResolvedValueOnce({ data: payload });

    await expect(fetchLegalText('accessibility')).resolves.toEqual(payload);
  });

  it('returns null on a network error', async () => {
    mockGet.mockRejectedValueOnce(new Error('boom'));
    await expect(fetchLegalText('imprint')).resolves.toBeNull();
  });
});
