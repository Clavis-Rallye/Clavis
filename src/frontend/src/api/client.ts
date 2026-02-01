import axios, { AxiosError } from 'axios';
import i18n from '../i18n';

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly errorCode?: string;
  /** The whole error body, for coded context such as `locked_until`. */
  readonly payload: Record<string, unknown>;

  constructor(
    status: number,
    detail: string,
    errorCode?: string,
    payload: Record<string, unknown> = {},
  ) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.errorCode = errorCode;
    this.payload = payload;
  }
}

// Empty means same-origin requests to `/api/...`, the production setup behind
// a reverse proxy. Never default to a localhost URL: every API call of that
// bundle would fail in production.
/** Origin of the API, empty for the same origin. Also used for plain links such as the log export. */
export const API_BASE_URL = import.meta.env.VITE_API_URL || '';

// The enrollment screen that the 2FA_SETUP_REQUIRED interceptor sends the user
// back to. One name keeps the redirect target, the "already there" guard, the
// public-path list and the route in App.tsx in step.
const TWO_FA_SETUP_PATH = '/2fa-setup';

// Paths that a 401 never redirects to /login. A 401 on /api/auth/me is
// expected there, because the user is not signed in yet.
const PUBLIC_PATHS = [
  '/login',
  '/register',
  '/forgot-password',
  '/reset-password',
  '/accept-invite',
  TWO_FA_SETUP_PATH,
  '/2fa-verify',
  '/join',
  '/welcome',
  // The public result page, for visitors without an account.
  '/results',
  // Imprint, privacy and accessibility statement. A public body must keep
  // them reachable from the open web.
  '/legal',
  // The codeless landing states a fact and reveals nothing.
  '/no-runs',
  // The operator console has its own login and session cookie.
  // `useInstanceAuth` sends a 401 of the operator probe to `/instance/login`,
  // never to the organization login.
  '/instance',
  '/instance/login',
];

function isOnPublicPath(pathname: string): boolean {
  // Bare `/` is the Welcome screen for guests.
  if (pathname === '/') return true;
  return PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(p + '/'));
}

// The operator console lives under /instance and keeps its own login. A
// session revoked by a password change returns to the login of its realm.
// A bare startsWith would also catch paths like '/instances'.
export function isUnderInstance(pathname: string): boolean {
  return pathname === '/instance' || pathname.startsWith('/instance/');
}

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  // Array params as repeated `key=v1&key=v2`. FastAPI ignores the default
  // `key[]=v1&key[]=v2`.
  paramsSerializer: { indexes: null },
  // No default Content-Type. axios picks application/json for objects and
  // multipart/form-data with its boundary for FormData. A default here
  // breaks FormData uploads.
});

// FastAPI's 422 sends `detail` as an array of validation errors, other
// statuses send a string. `ApiError.detail` is always a string, so callers
// can `setError(err.detail)` without crashing React.
type ValidationErrorItem = {
  type?: string;
  msg?: string;
  loc?: (string | number)[];
  ctx?: { min_length?: number; max_length?: number; [key: string]: unknown };
};
type ErrorPayload = { detail?: string | ValidationErrorItem[]; error_code?: string };

// The `msg` of a 422 item is Pydantic's English text with the raw field name
// in front. So each Pydantic error `type` maps to a translated message, and an
// unknown type gets a generic one.

/**
 * The name of the refused field as the forms call it, or '' when none is
 * known. Without it, a form with twenty fields would only say "Ungültige
 * Eingabe". The path is tried from its longest tail down to its last key, so
 * `tips.header` wins over `header`. Language keys and list positions are not
 * part of a field's name.
 */
function fieldLabel(loc: ValidationErrorItem['loc']): string {
  if (!Array.isArray(loc)) return '';
  const path = loc.filter(
    (part): part is string =>
      typeof part === 'string' && !['body', 'query', 'path'].includes(part) && !/^[a-z]{2}$/.test(part),
  );
  for (let start = 0; start < path.length; start += 1) {
    const key = `common:validation.fields.${path.slice(start).join('_')}`;
    if (i18n.exists(key)) return i18n.t(key);
  }
  return '';
}

function localizeValidationItem(item: ValidationErrorItem): string {
  const message = validationMessage(item);
  const label = fieldLabel(item?.loc);
  // The email sentence names its field already.
  if (!label || message === i18n.t('common:validation.email')) return message;
  return i18n.t('common:validation.withField', { field: label, message });
}

function validationMessage(item: ValidationErrorItem): string {
  const field = Array.isArray(item?.loc)
    ? item.loc.filter((s) => s !== 'body').pop()
    : undefined;
  const ctx = item?.ctx ?? {};
  switch (item?.type) {
    case 'missing':
      return i18n.t('common:validation.required');
    case 'string_too_short':
      return i18n.t('common:validation.tooShort', { min: ctx.min_length ?? 0 });
    case 'string_too_long':
      return i18n.t('common:validation.tooLong', { max: ctx.max_length ?? 0 });
    case 'too_long':
      return i18n.t('common:validation.tooMany', { max: ctx.max_length ?? 0 });
    case 'value_error': {
      // email-validator surfaces as `value_error`; target the email field.
      if (field === 'email' || /email/i.test(item.msg ?? '')) {
        return i18n.t('common:validation.email');
      }
      // A validator of the API's own states what is wrong ("Popup header must
      // be at most 2000 characters"). The UI shows it without Pydantic's
      // "Value error, " prefix. Without a usable message it stays generic.
      const own = (item.msg ?? '').replace(/^value error,\s*/i, '').trim();
      return own || i18n.t('common:validation.invalid');
    }
    default:
      return i18n.t('common:validation.invalid');
  }
}

export function flattenDetail(detail: ErrorPayload['detail'], fallback: string): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    // Two fields that fail the same way give one sentence.
    const parts = [
      ...new Set(detail.map(localizeValidationItem).filter(Boolean)),
    ];
    if (parts.length > 0) return parts.join('; ');
  }
  return fallback;
}

// Request interceptor: tell the backend the language the user reads in. The
// server composes mail, and the browser's `Accept-Language` reflects the OS,
// not the language the person picked in Clavis.
apiClient.interceptors.request.use((config) => {
  const language = i18n.resolvedLanguage ?? i18n.language;
  if (language) {
    config.headers.set('X-Clavis-Language', language);
  }
  return config;
});

// Full-document navigation of the response interceptor, on a plain object so
// tests can observe the target. jsdom does not navigate, and `window.location`
// is unforgeable there, so neither an `href` assignment nor `location.assign`
// can be asserted.
export const navigation = {
  /**
   * True once go() has started a full-document navigation. The route guard
   * then waits for it instead of pushing a bare /login over it. That push
   * could win the race and drop the reason parameter, and a deactivated
   * person would see /login without a word.
   */
  pending: false,
  go(url: string): void {
    this.pending = true;
    window.location.href = url;
  },
};

// Response interceptor: the redirects of session and account refusals, then
// every failure as a typed ApiError.
apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ErrorPayload>) => {
    // No answer at all, and not because the caller gave up: the connection
    // may be dead. The offline banner listens and asks the server.
    if (!error.response && error.code !== 'ERR_CANCELED' && typeof window !== 'undefined') {
      window.dispatchEvent(new Event('clavis:network-error'));
    }

    // A 401 from a protected route means the session is gone, so go to login.
    // On a public path the 401 answers the form just submitted (wrong
    // password, wrong TOTP code). It stays on screen and falls through to the
    // ApiError wrapping below like every other status. A raw AxiosError would
    // fail every `err instanceof ApiError` check and hide the real reason.
    //
    // A 401 with SESSION_REVOKED means a password change ended the session,
    // not wrong credentials. The person goes to the login of their realm with
    // `reason=session_revoked`, and the login page shows a notice. On the two
    // login pages the answer belongs to the form, and a reload would loop.
    // A revoked session on a public route is gone too, so this branch has no
    // public-path condition.
    if (
      error.response?.status === 401 &&
      error.response.data?.error_code === 'SESSION_REVOKED' &&
      window.location.pathname !== '/login' &&
      window.location.pathname !== '/instance/login'
    ) {
      const loginPath = isUnderInstance(window.location.pathname)
        ? '/instance/login'
        : '/login';
      navigation.go(`${loginPath}?reason=session_revoked`);
    } else if (
      error.response?.status === 401 &&
      error.response.data?.error_code === 'ACCOUNT_DISABLED' &&
      !isOnPublicPath(window.location.pathname)
    ) {
      // An admin deactivated the account while it was signed in. The login
      // page says so instead of a silent bounce.
      navigation.go('/login?reason=account_disabled');
    } else if (error.response?.status === 401 && !isOnPublicPath(window.location.pathname)) {
      // The else stops a plain 401 from overwriting the reason parameter of
      // the branches above. `navigation.go` lets a test tell a redirect from
      // none.
      navigation.go('/login');
    }

    // A member of an organization that the operator archived keeps a session
    // cookie, and the next request answers 403 ORG_ARCHIVED. Outside the
    // public pages, where the form shows the reason inline, the person goes
    // to the login, and the login page shows a notice.
    if (
      error.response?.status === 403 &&
      error.response.data?.error_code === 'ORG_ARCHIVED' &&
      !isOnPublicPath(window.location.pathname)
    ) {
      navigation.go('/login?reason=org_archived');
    }

    // A restricted 2fa_setup token used outside the setup flow never surfaces
    // as a bare 403. Its coded answer sends the user back to enrollment with
    // an explanation. On the setup screen itself the errors render inline.
    //
    // A full document navigation is deliberate, as in the 401 handler. The
    // interceptor lives outside the React tree with no router, and the app
    // state of the restricted token should go anyway.
    if (
      error.response?.status === 403 &&
      error.response.data?.error_code === '2FA_SETUP_REQUIRED' &&
      window.location.pathname !== TWO_FA_SETUP_PATH
    ) {
      window.location.href = `${TWO_FA_SETUP_PATH}?reason=required`;
      return Promise.reject(error);
    }

    if (error.response) {
      const { status, data } = error.response;
      const detail = flattenDetail(data?.detail, error.message);
      const errorCode = data?.error_code;
      const payload =
        data && typeof data === 'object' ? (data as unknown as Record<string, unknown>) : {};
      return Promise.reject(new ApiError(status, detail, errorCode, payload));
    }

    // Network error or timeout
    return Promise.reject(new ApiError(0, error.message ?? 'Network error'));
  }
);

export default apiClient;

