import i18n from '../i18n';

/**
 * The label the server writes into a team member row once the person's name
 * is removed (backend `ANONYMIZED_MEMBER_NAME` in `services/gdpr.py`).
 */
const ANONYMIZED_MEMBER_NAME = 'Deleted member';

/** A team member's name for display. A removed name shows in the UI language. */
export function memberDisplayName(displayName: string): string {
  return displayName === ANONYMIZED_MEMBER_NAME ? i18n.t('common:removedMemberName') : displayName;
}
