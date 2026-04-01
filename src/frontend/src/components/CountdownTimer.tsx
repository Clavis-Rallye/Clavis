import React, { useEffect, useRef, useState } from 'react';
import { Box, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { formatTime } from '../utils/formatDate';
import { visuallyHiddenSx } from '../styles/visuallyHidden';

interface CountdownTimerProps {
  /** ISO 8601 time at which the countdown reaches zero. */
  targetTime: string;
  /**
   * Renders the label instead of the default. Gets the remaining ms and
   * whether the target has passed.
   */
  renderLabel?: (ms: number, expired: boolean) => React.ReactNode;
  /** Custom "expired" label; defaults to i18n `runs.participant.feed.timesUp`. */
  expiredLabel?: React.ReactNode;
  /**
   * Speak the remaining time at sensible moments: when the countdown appears,
   * at 10, 5 and 1 minutes left, and when it runs out. Only one countdown on
   * a screen may do this, or a screen reader hears it twice.
   */
  announce?: boolean;
}

function pad(n: number): string {
  return n.toString().padStart(2, '0');
}

function formatRemaining(ms: number): string {
  if (ms <= 0) return '00:00';
  const total = Math.floor(ms / 1000);
  const hours = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) return `${pad(hours)}:${pad(mins)}:${pad(secs)}`;
  return `${pad(mins)}:${pad(secs)}`;
}

// Minutes left at which the announcing countdown speaks once each.
const MILESTONES_MIN = [10, 5, 1];

/** The smallest milestone the remaining time has reached, or null above all. */
function milestoneOf(ms: number): number | null {
  const reached = MILESTONES_MIN.filter((m) => ms <= m * 60_000);
  return reached.length ? Math.min(...reached) : null;
}

/**
 * A live countdown. It ticks every second until the target passes, then
 * clears its interval.
 *
 * The ticking number is a `timer` without a live region. A live region that
 * changes every second makes a screen reader chatter or queue the time. The
 * name of the timer says what it counts down to. An `announce` countdown
 * speaks through a separate hidden region, only at the milestones.
 */
const CountdownTimer: React.FC<CountdownTimerProps> = ({
  targetTime,
  renderLabel,
  expiredLabel,
  announce = false,
}) => {
  const { t, i18n } = useTranslation('runs');
  const targetMs = new Date(targetTime).getTime();
  const [remaining, setRemaining] = useState(() =>
    Math.max(0, targetMs - Date.now()),
  );

  useEffect(() => {
    if (targetMs - Date.now() <= 0) return;

    const id = setInterval(() => {
      const left = targetMs - Date.now();
      setRemaining(Math.max(0, left));
      if (left <= 0) clearInterval(id);
    }, 1000);

    return () => clearInterval(id);
  }, [targetMs]);

  const expired = remaining <= 0;

  // What the hidden region says. It changes only when a milestone is crossed,
  // so the screen reader hears each one once.
  const spokenFor = (ms: number, milestone: number | null): string => {
    if (ms <= 0) return t('participant.countdown.expired');
    const minutes = milestone ?? Math.ceil(ms / 60_000);
    return t('participant.countdown.remaining', { count: minutes });
  };
  const lastMilestone = useRef<number | null | 'expired' | undefined>(undefined);
  const [spoken, setSpoken] = useState('');
  useEffect(() => {
    if (!announce) return;
    const key = expired ? 'expired' : milestoneOf(remaining);
    if (key === lastMilestone.current) return;
    // The first value is the time left, rounded up: 5:17 speaks "noch 6
    // Minuten", not the milestone 10 that 5:17 has already passed. Later
    // values are the milestone just crossed.
    const first = lastMilestone.current === undefined;
    lastMilestone.current = key;
    setSpoken(spokenFor(remaining, first || key === 'expired' ? null : key));
    // spokenFor reads only t, which is stable per language.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [announce, expired, remaining]);

  if (renderLabel) {
    return <>{renderLabel(remaining, expired)}</>;
  }

  return (
    <>
      <Box
        role="timer"
        aria-label={t('participant.countdown.label', {
          time: formatTime(targetTime, i18n.language),
        })}
      >
        <Typography
          component="span"
          sx={{
            fontVariantNumeric: 'tabular-nums',
            fontWeight: 600,
            fontFamily: 'monospace',
          }}
        >
          {expired ? (expiredLabel ?? t('participant.feed.timesUp')) : formatRemaining(remaining)}
        </Typography>
      </Box>
      {announce && (
        <Box component="span" role="status" aria-live="polite" sx={visuallyHiddenSx}>
          {spoken}
        </Box>
      )}
    </>
  );
};

export default CountdownTimer;
