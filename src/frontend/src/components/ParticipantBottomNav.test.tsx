import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, renderHook, screen, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import ParticipantBottomNav from './ParticipantBottomNav';
import type { ParticipantTab } from './participantNav';
import { useUnreadNotices, __resetNoticeStoreForTests } from '../hooks/useUnreadNotices';
import type { FeedEvent, FeedEventType } from '../api/types';

const makeEvent = (id: string, type: FeedEventType, createdAt: string): FeedEvent => ({
  id,
  organization_id: 'org-1',
  run_id: 'run-1',
  event_type: type,
  content: {},
  author_id: null,
  target_team_id: null,
  created_at: createdAt,
});

/** Mirrors the current URL, so a test can prove where a tab navigated. */
const LocationProbe = () => {
  const location = useLocation();
  return <div>{`at:${location.pathname}`}</div>;
};

const renderNav = (props: { runId: string | null; active: ParticipantTab | false }) =>
  render(
    <MemoryRouter initialEntries={['/runs/run-1/participant']}>
      <LocationProbe />
      <ParticipantBottomNav {...props} />
    </MemoryRouter>,
  );

/** Seeds the shared notice registry the way the run shell's WS handler would,
 *  and hands back the feeder so a test can mark seen later. */
const seedNotices = (...events: FeedEvent[]) => {
  const { result } = renderHook(() => useUnreadNotices('run-1'));
  act(() => result.current.recordNotices(events));
  return result;
};

describe('ParticipantBottomNav', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    __resetNoticeStoreForTests();
  });

  it('renders the three tabs', () => {
    renderNav({ runId: 'run-1', active: 'rallye' });

    expect(screen.getByRole('button', { name: 'Mitteilungen' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Rallye' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Team' })).toBeInTheDocument();
  });

  // The bar is a named navigation landmark and the only one.
  it('renders the bar as a navigation landmark named "Teilnahme" holding the three entries', () => {
    renderNav({ runId: 'run-1', active: 'rallye' });

    const nav = screen.getByRole('navigation', { name: 'Teilnahme' });
    expect(screen.getAllByRole('navigation')).toHaveLength(1);
    expect(within(nav).getByRole('button', { name: 'Mitteilungen' })).toBeInTheDocument();
    expect(within(nav).getByRole('button', { name: 'Rallye' })).toBeInTheDocument();
    expect(within(nav).getByRole('button', { name: 'Team' })).toBeInTheDocument();
  });

  it('keeps the unread count in the name of "Mitteilungen" inside the landmark', () => {
    seedNotices(makeEvent('a1', 'announcement', '2026-06-01T12:05:00Z'));
    renderNav({ runId: 'run-1', active: 'rallye' });

    const nav = screen.getByRole('navigation', { name: 'Teilnahme' });
    expect(within(nav).getByRole('button', { name: 'Mitteilungen, 1 neue Meldung' })).toBeInTheDocument();
  });

  // Unseen coordination notices show up as a badge on the activity
  // tab, with the count in the accessible name (the digit itself is decor).
  it('shows the unread badge with an accessible label for unseen notices', () => {
    seedNotices(
      makeEvent('a1', 'announcement', '2026-06-01T12:05:00Z'),
      makeEvent('a2', 'reminder', '2026-06-01T12:06:00Z'),
    );
    renderNav({ runId: 'run-1', active: 'rallye' });

    expect(
      screen.getByRole('button', { name: 'Mitteilungen, 2 neue Meldungen' }),
    ).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('shows no badge when everything is seen', () => {
    const feeder = seedNotices(makeEvent('a1', 'announcement', '2026-06-01T12:05:00Z'));
    act(() => feeder.current.markSeen());
    renderNav({ runId: 'run-1', active: 'rallye' });

    expect(screen.getByRole('button', { name: 'Mitteilungen' })).toBeInTheDocument();
  });

  it('navigates into the run when a tab is tapped', () => {
    renderNav({ runId: 'run-1', active: 'rallye' });

    fireEvent.click(screen.getByRole('button', { name: 'Mitteilungen' }));

    expect(screen.getByText('at:/runs/run-1/participant/feed')).toBeInTheDocument();
  });

  it('disables the tabs when there is no run', () => {
    renderNav({ runId: null, active: false });

    expect(screen.getByRole('button', { name: 'Mitteilungen' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Rallye' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Team' })).toBeDisabled();
  });
});
