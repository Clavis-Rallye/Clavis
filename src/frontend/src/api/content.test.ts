import { describe, it, expect, vi, beforeEach } from 'vitest';
import apiClient from './client';
import { listStations, listChallenges } from './content';
import type { StationListResponse, ChallengeListResponse } from './types';

vi.mock('./client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const emptyStationList: StationListResponse = { items: [], total: 0, skip: 0, limit: 100 };
const emptyChallengeList: ChallengeListResponse = { items: [], total: 0, skip: 0, limit: 100 };

beforeEach(() => {
  vi.mocked(apiClient.get).mockReset();
});

describe('listStations', () => {
  it('sends the unchanged request when called without options', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: emptyStationList });
    await listStations('test-org');
    expect(apiClient.get).toHaveBeenCalledTimes(1);
    expect(apiClient.get).toHaveBeenCalledWith('/api/orgs/test-org/stations/', { params: {} });
  });

  it('maps q, sort, order and lang onto query params', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: emptyStationList });
    await listStations('test-org', {
      skip: 25,
      limit: 50,
      include_deleted: true,
      q: 'rathaus',
      sort: 'title',
      order: 'asc',
      lang: 'en',
    });
    expect(apiClient.get).toHaveBeenCalledWith('/api/orgs/test-org/stations/', {
      params: {
        skip: 25,
        limit: 50,
        include_deleted: true,
        q: 'rathaus',
        sort: 'title',
        order: 'asc',
        lang: 'en',
      },
    });
  });

  it('trims q before sending', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: emptyStationList });
    await listStations('test-org', { q: '  rathaus  ' });
    expect(vi.mocked(apiClient.get).mock.calls[0][1]).toEqual({ params: { q: 'rathaus' } });
  });

  it('does not send an empty or blank q', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: emptyStationList });
    await listStations('test-org', { q: '' });
    expect(vi.mocked(apiClient.get).mock.calls[0][1]).toEqual({ params: {} });
    await listStations('test-org', { q: '   ' });
    expect(vi.mocked(apiClient.get).mock.calls[1][1]).toEqual({ params: {} });
  });

  it('returns the response data', async () => {
    const list: StationListResponse = {
      items: [
        {
          id: 's1',
          organization_id: 'org-1',
          title: { de: 'Rathaus' },
          description: {},
          address: null,
          location_lat: null,
          location_lng: null,
          title_image_url: null,
          tips: null,
          created_at: '2026-09-20T08:15:00Z',
          updated_at: '2026-09-20T08:15:00Z',
          created_by_user_id: null,
          created_by_display_name: 'Sarah',
          usage_count: 2,
        },
      ],
      total: 1,
      skip: 0,
      limit: 100,
    };
    vi.mocked(apiClient.get).mockResolvedValue({ data: list });
    await expect(listStations('test-org')).resolves.toBe(list);
  });
});

describe('listChallenges', () => {
  it('sends the unchanged request when called without options', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: emptyChallengeList });
    await listChallenges('test-org');
    expect(apiClient.get).toHaveBeenCalledTimes(1);
    expect(apiClient.get).toHaveBeenCalledWith('/api/orgs/test-org/challenges/', { params: {} });
  });

  it('maps q, sort, order, lang and type onto query params', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: emptyChallengeList });
    await listChallenges('test-org', {
      skip: 0,
      limit: 25,
      q: 'fountain',
      sort: 'points',
      order: 'desc',
      lang: 'de',
      type: 'photo',
    });
    expect(apiClient.get).toHaveBeenCalledWith('/api/orgs/test-org/challenges/', {
      params: {
        skip: 0,
        limit: 25,
        q: 'fountain',
        sort: 'points',
        order: 'desc',
        lang: 'de',
        type: 'photo',
      },
    });
  });

  it('does not send a type of all', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: emptyChallengeList });
    await listChallenges('test-org', { type: 'all' });
    expect(vi.mocked(apiClient.get).mock.calls[0][1]).toEqual({ params: {} });
  });

  it('does not send an empty q alongside a type', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: emptyChallengeList });
    await listChallenges('test-org', { q: '  ', type: 'quiz' });
    expect(vi.mocked(apiClient.get).mock.calls[0][1]).toEqual({ params: { type: 'quiz' } });
  });
});
