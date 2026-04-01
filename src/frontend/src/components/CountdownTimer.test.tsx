import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { axe } from 'vitest-axe';
import CountdownTimer from './CountdownTimer';
import '../i18n';

describe('CountdownTimer', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T12:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders mm:ss under one hour', () => {
    render(<CountdownTimer targetTime="2026-01-01T12:05:30Z" />);
    expect(screen.getByRole('timer')).toHaveTextContent('05:30');
  });

  it('renders hh:mm:ss over one hour', () => {
    render(<CountdownTimer targetTime="2026-01-01T14:30:15Z" />);
    expect(screen.getByRole('timer')).toHaveTextContent('02:30:15');
  });

  it('ticks down every second', () => {
    render(<CountdownTimer targetTime="2026-01-01T12:00:10Z" />);
    expect(screen.getByRole('timer')).toHaveTextContent('00:10');

    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByRole('timer')).toHaveTextContent('00:09');

    act(() => vi.advanceTimersByTime(3000));
    expect(screen.getByRole('timer')).toHaveTextContent('00:06');
  });

  it('shows the expired label once the target passes', () => {
    render(<CountdownTimer targetTime="2026-01-01T12:00:02Z" />);
    expect(screen.getByRole('timer')).toHaveTextContent('00:02');

    act(() => vi.advanceTimersByTime(3000));
    expect(screen.getByRole('timer')).toHaveTextContent('Zeit abgelaufen!');
  });

  it('shows the expired label immediately for a past target', () => {
    render(<CountdownTimer targetTime="2026-01-01T11:59:00Z" />);
    expect(screen.getByRole('timer')).toHaveTextContent('Zeit abgelaufen!');
  });

  it('honours a custom renderLabel callback', () => {
    render(
      <CountdownTimer
        targetTime="2026-01-01T12:00:10Z"
        renderLabel={(ms, expired) =>
          expired ? <span>done</span> : <span>{`${Math.round(ms / 1000)}s left`}</span>
        }
      />,
    );
    expect(screen.getByText('10s left')).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(11_000));
    expect(screen.getByText('done')).toBeInTheDocument();
  });

  it('honours a custom expired label', () => {
    render(
      <CountdownTimer
        targetTime="2026-01-01T11:59:00Z"
        expiredLabel={<span>Ended</span>}
      />,
    );
    expect(screen.getByText('Ended')).toBeInTheDocument();
  });

  it('has no axe accessibility violations on initial render', async () => {
    // axe runs async timers internally; switch to real timers just for this assertion
    // and restore fake timers via the describe-level afterEach.
    vi.useRealTimers();
    const { container } = render(<CountdownTimer targetTime="2026-01-01T12:05:30Z" />);
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  // The ticking number must not be a polite live region that changes every
  // second, and the timer needs a name.
  it('is a named timer without a live region of its own', () => {
    render(<CountdownTimer targetTime="2026-01-01T12:05:30Z" />);
    const timer = screen.getByRole('timer');
    expect(timer).not.toHaveAttribute('aria-live');
    // The name carries the end time as a clock time in the UI language.
    expect(timer).toHaveAccessibleName(/^Countdown bis \d{1,2}:\d{2} Uhr$/);
    // Without `announce` there is no spoken region at all.
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('speaks at the start, at 10, 5 and 1 minutes left and at the end, never in between', () => {
    render(<CountdownTimer targetTime="2026-01-01T12:12:00Z" announce />);
    const status = screen.getByRole('status');
    expect(status).toHaveTextContent('Countdown: noch 12 Minuten');

    act(() => vi.advanceTimersByTime(30_000));
    expect(status).toHaveTextContent('Countdown: noch 12 Minuten');

    act(() => vi.advanceTimersByTime(90_000)); // 10:00 left
    expect(status).toHaveTextContent('Countdown: noch 10 Minuten');

    act(() => vi.advanceTimersByTime(60_000)); // 9:00 left, no milestone
    expect(status).toHaveTextContent('Countdown: noch 10 Minuten');

    act(() => vi.advanceTimersByTime(240_000)); // 5:00 left
    expect(status).toHaveTextContent('Countdown: noch 5 Minuten');

    act(() => vi.advanceTimersByTime(240_000)); // 1:00 left
    expect(status).toHaveTextContent('Countdown: noch 1 Minute');

    act(() => vi.advanceTimersByTime(61_000));
    expect(status).toHaveTextContent('Countdown: Die Zeit ist abgelaufen.');
  });

  // A countdown that appears between two milestones first speaks the time left,
  // not the higher milestone it has already passed.
  it('speaks the time left when it appears between milestones', () => {
    render(<CountdownTimer targetTime="2026-01-01T12:05:17Z" announce />);
    const status = screen.getByRole('status');
    expect(status).toHaveTextContent('Countdown: noch 6 Minuten');

    act(() => vi.advanceTimersByTime(17_000)); // 5:00 left
    expect(status).toHaveTextContent('Countdown: noch 5 Minuten');

    act(() => vi.advanceTimersByTime(60_000)); // 4:00 left, no milestone
    expect(status).toHaveTextContent('Countdown: noch 5 Minuten');

    act(() => vi.advanceTimersByTime(180_000)); // 1:00 left
    expect(status).toHaveTextContent('Countdown: noch 1 Minute');

    act(() => vi.advanceTimersByTime(61_000));
    expect(status).toHaveTextContent('Countdown: Die Zeit ist abgelaufen.');
  });
});
