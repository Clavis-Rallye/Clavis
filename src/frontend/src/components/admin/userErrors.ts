import type { TFunction } from 'i18next';
import { ApiError } from '../../api/client';

/**
 * A refused user create, edit or invitation in the admin's language.
 *
 * The API's English detail is never shown. The deleted-account case names the
 * toggle as the German interface calls it, "Gelöschte anzeigen", not 'show
 * deleted'. A validation error (422) arrives already localized from the API
 * client, so it stays as it is.
 */
export function userErrorMessage(err: unknown, t: TFunction): string {
  if (!(err instanceof ApiError)) return t('admin:users.errors.generic');
  switch (err.errorCode) {
    case 'EMAIL_BELONGS_TO_DELETED_USER':
      return t('admin:users.invite.errors.emailBelongsToDeletedUser');
    case 'EMAIL_BELONGS_TO_INACTIVE_USER':
      return t('admin:users.invite.errors.emailBelongsToInactiveUser');
    case 'EMAIL_BELONGS_TO_USER':
      return t('admin:users.invite.errors.emailBelongsToUser');
    default:
      break;
  }
  if (err.status === 422) return err.detail;
  return t('admin:users.errors.generic');
}
