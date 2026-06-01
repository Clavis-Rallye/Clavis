import { describe, it, expect, beforeAll, beforeEach, afterEach, vi, type Mock } from 'vitest';
import i18n from '../i18n';
import { AxiosHeaders, type AxiosError, type InternalAxiosRequestConfig } from 'axios';
import apiClient, { ApiError, flattenDetail, navigation } from './client';

/**
 * The raw Pydantic `msg` of a FastAPI 422 is English and starts with a `field:`
 * prefix. flattenDetail shows a translated message without the prefix, chosen by
 * the error `type`.
 */
describe('flattenDetail', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('de');
  });

  it('passes through a plain string detail unchanged', () => {
    expect(flattenDetail('Anmeldung fehlgeschlagen', 'fallback')).toBe(
      'Anmeldung fehlgeschlagen',
    );
  });

  it('localizes an email value_error instead of echoing the Pydantic msg', () => {
    const detail = [
      {
        type: 'value_error',
        loc: ['body', 'email'],
        msg: 'value is not a valid email address: An email address must have an @-sign.',
      },
    ];
    expect(flattenDetail(detail, 'fallback')).toBe('Ungültige E-Mail-Adresse');
  });

  it("shows the API's own message of a value_error, without Pydantic's prefix", () => {
    const detail = [
      { type: 'value_error', loc: ['body', 'tips', 'header'], msg: 'Value error, Popup header must be at most 2000 characters' },
    ];
    expect(flattenDetail(detail, 'fallback')).toBe(
      'Tipps & Links, Popup-Überschrift: Popup header must be at most 2000 characters',
    );
  });

  it('keeps the generic text for a value_error without a usable message', () => {
    const detail = [{ type: 'value_error', loc: ['body', 'x'], msg: 'Value error, ' }];
    expect(flattenDetail(detail, 'fallback')).toBe('Ungültige Eingabe');
  });

  it('localizes a missing required field', () => {
    const detail = [{ type: 'missing', loc: ['body', 'password'], msg: 'Field required' }];
    expect(flattenDetail(detail, 'fallback')).toBe('Passwort: Dieses Feld ist erforderlich');
  });

  it('interpolates the min length for string_too_short', () => {
    const detail = [
      {
        type: 'string_too_short',
        loc: ['body', 'password'],
        msg: 'String should have at least 8 characters',
        ctx: { min_length: 8 },
      },
    ];
    expect(flattenDetail(detail, 'fallback')).toBe('Passwort: Mindestens 8 Zeichen erforderlich');
  });

  it('falls back to a generic message for unknown error types', () => {
    const detail = [{ type: 'some_future_type', loc: ['body', 'x'], msg: 'whatever' }];
    expect(flattenDetail(detail, 'fallback')).toBe('Ungültige Eingabe');
  });

  it('de-duplicates identical messages across fields', () => {
    const detail = [
      { type: 'missing', loc: ['body', 'a'], msg: 'Field required' },
      { type: 'missing', loc: ['body', 'b'], msg: 'Field required' },
    ];
    expect(flattenDetail(detail, 'fallback')).toBe('Dieses Feld ist erforderlich');
  });

  it('names the refused field, from the most specific path it knows', () => {
    const header = [{ type: 'value_error', loc: ['body', 'tips', 'header'], msg: '' }];
    expect(flattenDetail(header, 'fallback')).toBe('Tipps & Links, Popup-Überschrift: Ungültige Eingabe');
    const label = [{ type: 'value_error', loc: ['body', 'tips', 'links', 0, 'label'] }];
    expect(flattenDetail(label, 'fallback')).toBe('Tipps & Links, Beschriftung eines Links: Ungültige Eingabe');
    const title = [{ type: 'string_too_long', loc: ['body', 'title', 'de'], msg: 'x', ctx: { max_length: 200 } }];
    expect(flattenDetail(title, 'fallback')).toBe('Titel: Höchstens 200 Zeichen erlaubt');
    const links = [{ type: 'too_long', loc: ['body', 'tips', 'links'], msg: 'x', ctx: { max_length: 10 } }];
    expect(flattenDetail(links, 'fallback')).toBe('Tipps & Links, Links: Höchstens 10 Einträge erlaubt');
  });

  it('two fields that fail the same way are both named', () => {
    const detail = [
      { type: 'missing', loc: ['body', 'title'], msg: 'Field required' },
      { type: 'missing', loc: ['body', 'address'], msg: 'Field required' },
    ];
    expect(flattenDetail(detail, 'fallback')).toBe(
      'Titel: Dieses Feld ist erforderlich; Adresse: Dieses Feld ist erforderlich',
    );
  });

  it('returns the fallback for an empty array', () => {
    expect(flattenDetail([], 'Netzwerkfehler')).toBe('Netzwerkfehler');
  });
});

// The interceptor's rejection handler, taken off the axios instance so the tests call it directly.
const rejected = (
  apiClient.interceptors.response as unknown as {
    handlers: { rejected: (e: AxiosError) => Promise<never> }[];
  }
).handlers[0]!.rejected;

const axiosErrorWith = (status: number, detail: unknown, errorCode?: string) =>
  ({
    message: 'Request failed',
    response: { status, data: { detail, error_code: errorCode } },
  }) as unknown as AxiosError;

/**
 * Every failed response reaches callers as an ApiError, the type the app branches on.
 * On a public path a 401 keeps the backend's reason: a wrong 2FA code reads
 * "Invalid TOTP code", not the generic "Ein unerwarteter Fehler ist aufgetreten".
 */
describe('response interceptor error normalisation', () => {
  it('wraps a 401 on a public path in an ApiError carrying the backend detail', async () => {
    window.history.pushState({}, '', '/2fa-verify');

    await expect(rejected(axiosErrorWith(401, 'Invalid TOTP code'))).rejects.toSatisfy(
      (err: unknown) =>
        err instanceof ApiError && err.status === 401 && err.detail === 'Invalid TOTP code',
    );
  });

  it('does not navigate away from a public path on a 401', async () => {
    window.history.pushState({}, '', '/2fa-verify');
    await rejected(axiosErrorWith(401, 'Invalid TOTP code')).catch(() => {});
    expect(window.location.pathname).toBe('/2fa-verify');
  });

  // The visitor of the public result page (/results#<slug>) has no account. A 401,
  // for example from the app-wide auth probe, must not send them to the login page.
  it('does not navigate away from /results on a 401', async () => {
    window.history.pushState({}, '', '/results');
    await rejected(axiosErrorWith(401, 'Not authenticated')).catch(() => {});
    expect(window.location.pathname).toBe('/results');
  });

  // The operator console keeps its own session and its own login. A 401 on /instance
  // or /instance/login (operator probe, wrong operator password) must not bounce the
  // operator to the organization login. useInstanceAuth redirects to /instance/login instead.
  it.each(['/instance', '/instance/login'])(
    'does not navigate away from %s on a 401',
    async (path) => {
      window.history.pushState({}, '', path);
      await rejected(axiosErrorWith(401, 'Not authenticated')).catch(() => {});
      expect(window.location.pathname).toBe(path);
    },
  );

  it('wraps a non-401 failure in an ApiError too', async () => {
    window.history.pushState({}, '', '/login');

    await expect(rejected(axiosErrorWith(404, 'Not found'))).rejects.toSatisfy(
      (err: unknown) => err instanceof ApiError && err.status === 404,
    );
  });

  it('renders a coded 403 inline once already on the setup screen', async () => {
    // Off the setup screen this status bounces the user into the enrollment
    // flow. On it, the error must reach the page as a typed ApiError.
    window.history.pushState({}, '', '/2fa-setup');

    await expect(
      rejected(axiosErrorWith(403, 'Setup required', '2FA_SETUP_REQUIRED')),
    ).rejects.toSatisfy(
      (err: unknown) => err instanceof ApiError && err.errorCode === '2FA_SETUP_REQUIRED',
    );
  });
});

/**
 * A 401 with SESSION_REVOKED means a password change ended the session. From any
 * route but the two login pages, the interceptor sends the person to the login of
 * the request's realm with `reason=session_revoked`. jsdom never navigates, so the
 * tests spy on `navigation.go`.
 */
describe('SESSION_REVOKED redirect', () => {
  let go: Mock;

  beforeEach(() => {
    go = vi.spyOn(navigation, 'go');
  });

  afterEach(() => {
    go.mockRestore();
  });

  it('sends a revoked organization session to /login with the reason', async () => {
    window.history.pushState({}, '', '/dashboard');

    await rejected(axiosErrorWith(401, 'Session revoked', 'SESSION_REVOKED')).catch(() => {});

    expect(go).toHaveBeenCalledWith('/login?reason=session_revoked');
  });

  // A revocation has no public-path exemption, because the session is gone there too.
  // Only the login pages keep it inline.
  it('also redirects from a public path', async () => {
    window.history.pushState({}, '', '/results');

    await rejected(axiosErrorWith(401, 'Session revoked', 'SESSION_REVOKED')).catch(() => {});

    expect(go).toHaveBeenCalledWith('/login?reason=session_revoked');
  });

  it('sends a revoked operator session to the instance login', async () => {
    window.history.pushState({}, '', '/instance/audit');

    await rejected(axiosErrorWith(401, 'Session revoked', 'SESSION_REVOKED')).catch(() => {});

    expect(go).toHaveBeenCalledWith('/instance/login?reason=session_revoked');
  });

  it.each(['/login', '/instance/login'])(
    'keeps %s on screen when the revoked session answers there',
    async (path) => {
      window.history.pushState({}, '', path);

      await rejected(axiosErrorWith(401, 'Session revoked', 'SESSION_REVOKED')).catch(() => {});

      expect(go).not.toHaveBeenCalled();
      expect(window.location.pathname).toBe(path);
    },
  );

  it('keeps a plain 401 on the bare login redirect, without a reason', async () => {
    window.history.pushState({}, '', '/dashboard');

    await expect(rejected(axiosErrorWith(401, 'Not authenticated'))).rejects.toSatisfy(
      (err: unknown) => err instanceof ApiError && err.status === 401,
    );
    expect(go).toHaveBeenCalledWith('/login');
  });

  // Every public page, imprint, privacy and the accessibility statement among them,
  // opens without a session. The app-wide auth probe answers 401 there, and that 401
  // must not send the visitor to /login.
  it.each([
    '/',
    '/legal/impressum',
    '/legal/privacy',
    '/legal/accessibility',
    '/no-runs',
    '/results',
    '/welcome',
    '/join/ABC234',
    '/login',
    '/register',
    '/instance',
  ])('does not redirect a plain 401 away from the public page %s', async (path) => {
    window.history.pushState({}, '', path);

    await rejected(axiosErrorWith(401, 'Not authenticated')).catch(() => {});

    expect(go).not.toHaveBeenCalled();
  });

  it('still redirects a plain 401 from a lookalike path such as /legalese', async () => {
    window.history.pushState({}, '', '/legalese');

    await rejected(axiosErrorWith(401, 'Not authenticated')).catch(() => {});

    expect(go).toHaveBeenCalledWith('/login');
  });
});

/**
 * The backend writes mail in the language the user reads in Clavis. The browser's
 * Accept-Language follows the OS, not that choice.
 */
describe('X-Clavis-Language request header', () => {
  it('carries the active i18n language on every request', async () => {
    await i18n.changeLanguage('de');
    const handler = (
      apiClient.interceptors.request as unknown as {
        handlers: { fulfilled: (c: InternalAxiosRequestConfig) => InternalAxiosRequestConfig }[];
      }
    ).handlers[0]!.fulfilled;

    const config = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
    expect(handler(config).headers.get('X-Clavis-Language')).toBe('de');

    await i18n.changeLanguage('en');
    const englishConfig = { headers: new AxiosHeaders() } as InternalAxiosRequestConfig;
    expect(handler(englishConfig).headers.get('X-Clavis-Language')).toBe('en');

    await i18n.changeLanguage('de');
  });
});

// A member of an archived organization must see a reason on /login.
// The 403 ORG_ARCHIVED carries it.
describe('ORG_ARCHIVED redirect', () => {
  let go: Mock;

  beforeEach(() => {
    go = vi.spyOn(navigation, 'go');
  });

  afterEach(() => {
    go.mockRestore();
  });

  it('sends a member of an archived organization to the login with the reason', async () => {
    window.history.pushState({}, '', '/dashboard');

    await rejected(axiosErrorWith(403, 'This organization is archived.', 'ORG_ARCHIVED')).catch(() => {});

    expect(go).toHaveBeenCalledWith('/login?reason=org_archived');
  });

  it('keeps the reason inline on a public page such as the login form', async () => {
    window.history.pushState({}, '', '/login');

    await expect(
      rejected(axiosErrorWith(403, 'This organization is archived.', 'ORG_ARCHIVED')),
    ).rejects.toSatisfy((err: unknown) => err instanceof ApiError && err.errorCode === 'ORG_ARCHIVED');
    expect(go).not.toHaveBeenCalled();
  });
});

// A deactivated person's open session must show a reason on /login.
// The 401 ACCOUNT_DISABLED carries it.
describe('ACCOUNT_DISABLED redirect', () => {
  let go: Mock;

  beforeEach(() => {
    go = vi.spyOn(navigation, 'go');
  });

  afterEach(() => {
    go.mockRestore();
  });

  it('sends a deactivated person to the login with the reason', async () => {
    window.history.pushState({}, '', '/dashboard');

    await rejected(axiosErrorWith(401, 'User account is disabled', 'ACCOUNT_DISABLED')).catch(() => {});

    expect(go).toHaveBeenCalledWith('/login?reason=account_disabled');
    expect(go).toHaveBeenCalledTimes(1);
  });
});
