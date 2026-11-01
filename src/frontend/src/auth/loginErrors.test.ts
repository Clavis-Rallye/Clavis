import { describe, it, expect, beforeAll } from 'vitest';
import i18n from '../i18n';
import { ApiError } from '../api/client';
import { loginErrorMessage } from './loginErrors';

/**
 * The sign-in must not show the API's English texts ("Invalid credentials",
 * "Account locked until <ISO>", "This organization is archived.") to any role.
 * Each refusal reads in the UI language, and a lock names its end time as a
 * clock time.
 */
describe('loginErrorMessage', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('de');
  });

  const msg = (err: unknown) => loginErrorMessage(err, i18n.t, 'de');

  it('words a wrong address or password', () => {
    expect(msg(new ApiError(401, 'Invalid credentials', 'INVALID_CREDENTIALS'))).toBe(
      'E-Mail-Adresse oder Passwort ist falsch.',
    );
    // An uncoded 401 from an older server reads the same.
    expect(msg(new ApiError(401, 'Invalid credentials'))).toBe(
      'E-Mail-Adresse oder Passwort ist falsch.',
    );
  });

  it('names the end of a lock as a clock time in the UI language', () => {
    const until = new Date(2026, 10, 18, 14, 32, 7, 123).toISOString();
    const text = msg(
      new ApiError(423, `Account locked until ${until}.`, 'ACCOUNT_LOCKED', { locked_until: until }),
    );
    expect(text).toBe(
      'Nach zu vielen Fehlversuchen ist dieses Konto bis 14:32 Uhr gesperrt. Bitte versuche es danach erneut.',
    );
    expect(text).not.toMatch(/\d{4}-\d{2}-\d{2}T/);
  });

  it('falls back to a lock without a time when the end time is missing', () => {
    expect(msg(new ApiError(423, 'Account locked until …'))).toMatch(/vorübergehend gesperrt/);
  });

  it('words a deactivated account, an archived organization and the rate limit', () => {
    expect(msg(new ApiError(401, 'Account is disabled', 'ACCOUNT_DISABLED'))).toMatch(/deaktiviert/);
    expect(msg(new ApiError(403, 'This organization is archived.', 'ORG_ARCHIVED'))).toBe(
      'Diese Organisation ist archiviert. Eine Anmeldung ist nicht mehr möglich.',
    );
    expect(msg(new ApiError(429, 'Rate limit exceeded'))).toMatch(/Zu viele Anmeldeversuche/);
  });

  it('words a lost connection and anything unknown without English', () => {
    expect(msg(new ApiError(0, 'Network Error'))).toMatch(/Keine Verbindung/);
    expect(msg(new ApiError(500, 'Internal Server Error'))).toBe('Ein unerwarteter Fehler ist aufgetreten');
    expect(msg(new Error('boom'))).toBe('Ein unerwarteter Fehler ist aufgetreten');
  });

  it('speaks English on an English page', () => {
    expect(
      loginErrorMessage(new ApiError(401, 'x', 'INVALID_CREDENTIALS'), i18n.getFixedT('en'), 'en'),
    ).toBe('Email address or password is wrong.');
  });
});
