import type { TFunction } from 'i18next';
import { ApiError } from '../api/client';
import { formatTime } from '../utils/formatDate';

/**
 * The sign-in refusal in the reader's language.
 *
 * The API answers in English for the log and with a code for the client.
 * This function never returns that English text, because participants read
 * it on a phone. A lock names its end time in the UI language, not as a UTC
 * timestamp with microseconds.
 */
export function loginErrorMessage(err: unknown, t: TFunction, lang: string): string {
  if (!(err instanceof ApiError)) return t('auth:login.unexpectedError');
  switch (err.errorCode) {
    case 'INVALID_CREDENTIALS':
      return t('auth:login.errors.invalidCredentials');
    case 'ACCOUNT_DISABLED':
      return t('auth:login.errors.accountDisabled');
    case 'ACCOUNT_LOCKED': {
      const until = err.payload.locked_until;
      return typeof until === 'string'
        ? t('auth:login.errors.accountLockedUntil', { time: formatTime(until, lang) })
        : t('auth:login.errors.accountLocked');
    }
    case 'ORG_ARCHIVED':
      return t('auth:login.errors.orgArchived');
    default:
      break;
  }
  if (err.status === 401) return t('auth:login.errors.invalidCredentials');
  if (err.status === 423) return t('auth:login.errors.accountLocked');
  if (err.status === 429) return t('auth:login.errors.rateLimited');
  if (err.status === 0) return t('auth:login.errors.network');
  return t('auth:login.unexpectedError');
}
