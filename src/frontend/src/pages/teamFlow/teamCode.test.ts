import { describe, expect, it } from 'vitest';
import { extractTeamCode } from './teamCode';

describe('extractTeamCode', () => {
  it('reads the code from a team join link', () => {
    expect(extractTeamCode('https://app.example.org/join/6XEXHH?team=k54fa')).toBe('K54FA');
  });

  it('takes a bare code', () => {
    expect(extractTeamCode(' uxfe3 ')).toBe('UXFE3');
  });

  it('refuses a link without a team', () => {
    expect(extractTeamCode('https://app.example.org/join/6XEXHH')).toBeNull();
    expect(extractTeamCode('https://www.augsburg.de/rathaus')).toBeNull();
  });

  it('refuses the text of another QR code', () => {
    expect(extractTeamCode('clavis-qr:station-7:finish-line-secret')).toBeNull();
    expect(extractTeamCode('ZIELLINIE-AUGSBURG-2026')).toBeNull();
    expect(extractTeamCode('')).toBeNull();
  });
});
