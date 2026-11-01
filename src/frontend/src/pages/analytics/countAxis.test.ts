import { describe, expect, it } from 'vitest';
import { countAxis } from './countAxis';

// The value axis of the statistics charts.
describe('countAxis', () => {
  it('starts at zero and steps in whole numbers', () => {
    const axis = countAxis('Teams', [2, 5], 'de');
    expect(axis).toMatchObject({ label: 'Teams', min: 0, tickMinStep: 1 });
    expect(axis).not.toHaveProperty('max');
  });

  it('gives a chart without a value the range 0 to 1', () => {
    expect(countAxis('Teams', [0, 0], 'de')).toMatchObject({ min: 0, max: 1 });
    expect(countAxis('Teams', [], 'de')).toMatchObject({ min: 0, max: 1 });
  });

  it('writes the ticks in the reader’s format', () => {
    expect(countAxis('Punkte', [1], 'de').valueFormatter(1200)).toBe('1.200');
    expect(countAxis('Points', [1], 'en').valueFormatter(2.5)).toBe('2.5');
    expect(countAxis('Punkte', [1], 'de').valueFormatter(null)).toBe('');
  });
});
