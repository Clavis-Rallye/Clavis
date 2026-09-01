import type { ChallengeType } from '../api/types';

/**
 * Every challenge type a creator can choose, in the order the UI offers them.
 * The create form and the editor share the list, so they cannot offer
 * different types. Media first, then the answer types, then the two that need
 * a configuration.
 *
 * `creative` is not in the list. A photo, video or audio task takes a text
 * answer as a fallback instead, and the API refuses the type on create and on a
 * change. The type stays in `ChallengeType`, because an older run may carry it.
 */
export const CHALLENGE_TYPES: readonly ChallengeType[] = [
  'photo',
  'video',
  'audio',
  'text',
  'qr_scan',
  'quiz',
];
