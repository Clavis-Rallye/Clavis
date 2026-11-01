import { describe, expect, it } from 'vitest';
import { validatePasswordPolicy } from './passwordPolicy';

describe('validatePasswordPolicy', () => {
  it('rejects a password below 8 characters even with all four classes', () => {
    expect(validatePasswordPolicy('Ab1!xyz')).toBe(false);
  });

  it('rejects a password with only two of the four character classes', () => {
    expect(validatePasswordPolicy('abcdefg1')).toBe(false);
  });

  it('accepts a password with three character classes', () => {
    expect(validatePasswordPolicy('Abcdefg1')).toBe(true);
  });
});
