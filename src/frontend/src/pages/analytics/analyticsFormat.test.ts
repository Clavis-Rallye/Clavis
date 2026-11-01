import { describe, expect, it } from 'vitest';
import {
  NO_VALUE,
  RUN_COLORS,
  assignSlots,
  formatDuration,
  formatNumber,
  formatPercent,
  formatSpread,
  toMinutes,
} from './analyticsFormat';

// Intl writes a narrow no-break space in "50 %". Compare without it.
const plain = (text: string) => text.replace(/\s/g, ' ');

describe('analyticsFormat', () => {
  it('writes durations in the reader’s words, with a dash for no value', () => {
    expect(formatDuration(null, 'de')).toBe(NO_VALUE);
    expect(formatDuration(150, 'de')).toBe('2,5 Min.');
    expect(formatDuration(2520, 'de')).toBe('42 Min.');
    expect(formatDuration(5700, 'de')).toBe('1 Std. 35 Min.');
    // Rounded first: 59,6 minutes read as one hour, 9,96 as 10 minutes.
    expect(formatDuration(3576, 'de')).toBe('1 Std. 0 Min.');
    expect(formatDuration(597.6, 'de')).toBe('10 Min.');
    expect(formatDuration(150, 'en')).toBe('2.5 min');
    expect(formatDuration(5700, 'en')).toBe('1 h 35 min');
    // Zero minutes is a value, not "no value".
    expect(formatDuration(0, 'de')).toBe('0 Min.');
  });

  it('writes numbers, shares and spreads in the reader’s format', () => {
    expect(formatNumber(10.666, 'de')).toBe('10,7');
    expect(formatNumber(undefined, 'de')).toBe(NO_VALUE);
    expect(plain(formatPercent(66.7, 'de'))).toBe('66,7 %');
    expect(formatPercent(66.7, 'en')).toBe('66.7%');
    // From ten minutes on, whole minutes: 22,5 reads 23.
    expect(formatSpread(1350, 1650, 'de')).toBe('23 Min. bis 28 Min.');
    expect(formatSpread(150, 270, 'en')).toBe('2.5 min to 4.5 min');
    expect(formatSpread(null, 1650, 'de')).toBe(NO_VALUE);
  });

  it('turns seconds into minutes for an axis and keeps a missing value missing', () => {
    expect(toMinutes(1500)).toBe(25);
    expect(toMinutes(1530)).toBe(25.5);
    expect(toMinutes(null)).toBeNull();
  });

  it('keeps a run’s colour when another run leaves', () => {
    const first = assignSlots({}, ['a', 'b', 'c']);
    expect(first).toEqual({ a: 0, b: 1, c: 2 });
    // b leaves: a and c keep their colours, a new run takes the free slot.
    const second = assignSlots(first, ['a', 'c', 'd']);
    expect(second).toEqual({ a: 0, c: 2, d: 1 });
    expect(RUN_COLORS).toHaveLength(6);
  });
});
