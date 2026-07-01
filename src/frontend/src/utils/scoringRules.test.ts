import { describe, it, expect } from 'vitest';
import {
  discardedScoringKeys,
  pruneScoringRules,
  pruneScoringRulesForTypeChange,
  supportsAutoApprove,
} from './scoringRules';

describe('pruneScoringRules', () => {
  // The exact shape that reached production: a quiz configured, then saved as
  // text, carrying an auto_approve that the backend never honours.
  it('drops auto_approve when the type cannot be auto-scored', () => {
    const pruned = pruneScoringRules('text', {
      max_points: 10,
      auto_approve: true,
      correct_option: 'a',
      options: ['a', 'b'],
    });
    expect(pruned).toEqual({ max_points: 10 });
  });

  it('keeps quiz keys on a quiz', () => {
    const rules = { max_points: 20, auto_approve: true, options: ['a', 'b'], correct_option: 'a' };
    expect(pruneScoringRules('quiz', rules)).toEqual(rules);
  });

  it('keeps the expected value on a QR scan but not quiz options', () => {
    expect(
      pruneScoringRules('qr_scan', {
        max_points: 10,
        auto_approve: true,
        expected_value: 'ABC',
        options: ['a'],
        correct_option: 'a',
      }),
    ).toEqual({ max_points: 10, auto_approve: true, expected_value: 'ABC' });
  });

  it('strips a leftover empty options array from a photo challenge', () => {
    // The other shape found live — harmless, but it makes stored content lie
    // about what the challenge is.
    expect(pruneScoringRules('photo', { options: [''], max_points: 10 })).toEqual({
      max_points: 10,
    });
  });

  it('always keeps the universal keys', () => {
    expect(pruneScoringRules('video', { max_points: 30, time_bonus: 5 })).toEqual({
      max_points: 30,
      time_bonus: 5,
    });
  });

  it('leaves rules untouched while no type is chosen yet', () => {
    const rules = { max_points: 10, auto_approve: true };
    expect(pruneScoringRules(null, rules)).toEqual(rules);
  });
});

describe('pruneScoringRulesForTypeChange', () => {
  // A quiz changed to qr_scan kept auto_approve, and the
  // backend then demanded an expected_value nobody had entered.
  it('drops auto_approve on quiz to qr_scan, although both types honour it', () => {
    expect(
      pruneScoringRulesForTypeChange('quiz', 'qr_scan', {
        max_points: 10,
        time_bonus: 2,
        auto_approve: true,
        options: ['a', 'b'],
        correct_option: 'a',
      }),
    ).toEqual({ max_points: 10, time_bonus: 2 });
  });

  it('keeps only the universal keys on any other type change', () => {
    expect(
      pruneScoringRulesForTypeChange('qr_scan', 'text', {
        max_points: 5,
        auto_approve: true,
        expected_value: 'ABC',
      }),
    ).toEqual({ max_points: 5 });
  });

  it('prunes against the type itself when the type does not change', () => {
    const rules = { max_points: 10, auto_approve: true, expected_value: 'ABC', options: ['a'] };
    expect(pruneScoringRulesForTypeChange('qr_scan', 'qr_scan', rules)).toEqual({
      max_points: 10,
      auto_approve: true,
      expected_value: 'ABC',
    });
  });

  it('leaves the rules untouched while no new type is chosen', () => {
    const rules = { max_points: 10, auto_approve: true };
    expect(pruneScoringRulesForTypeChange('quiz', null, rules)).toEqual(rules);
  });
});

describe('discardedScoringKeys', () => {
  it('names the configured keys the new type throws away, in the confirmation order', () => {
    expect(
      discardedScoringKeys('quiz', 'text', {
        max_points: 10,
        time_bonus: 2,
        auto_approve: true,
        options: ['a', 'b'],
        correct_option: 'a',
      }),
    ).toEqual(['options', 'correct_option', 'auto_approve']);
  });

  it('names auto_approve on quiz to qr_scan as well', () => {
    expect(
      discardedScoringKeys('quiz', 'qr_scan', { max_points: 10, auto_approve: true }),
    ).toEqual(['auto_approve']);
  });

  it('leaves out a key that carries no configuration', () => {
    // An unticked box and an array of empty option fields cost the creator
    // nothing, so promising their loss would be a lie.
    expect(
      discardedScoringKeys('quiz', 'text', {
        max_points: 10,
        auto_approve: false,
        options: ['', ''],
      }),
    ).toEqual([]);
  });

  it('is empty when the change discards nothing', () => {
    expect(discardedScoringKeys('photo', 'video', { max_points: 10, time_bonus: 2 })).toEqual([]);
  });
});

describe('supportsAutoApprove', () => {
  it('is true exactly for the two types the backend can score itself', () => {
    expect(supportsAutoApprove('quiz')).toBe(true);
    expect(supportsAutoApprove('qr_scan')).toBe(true);
    for (const t of ['text', 'photo', 'video', 'audio', 'creative'] as const) {
      expect(supportsAutoApprove(t)).toBe(false);
    }
  });
});
