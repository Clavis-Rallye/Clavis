/**
 * The step number and total of each onboarding page. Every page calls
 * `getStepInfo(stepId, pathKind)` for its progress indicator, so the bar grows
 * monotonically across the whole flow (Welcome → ProfileSetup → TeamFlow).
 * The `with-code` path has its own map. Every other path kind uses the
 * `no-code` map.
 *
 * The legacy GuestJoin step IDs stay in the type and in both maps. The page
 * they belonged to no longer exists.
 */

import type { WizardPathKind } from './WizardStateContext';

type WizardStepId =
  | 'welcome'
  | 'entry-choice'
  | 'org-picker'
  | 'profile-setup'
  // Team-flow step IDs.
  | 'team-find'
  | 'team-join'
  | 'team-pick-name'
  | 'team-name-pending'
  | 'team-waiting'
  | 'team-invite'
  | 'team-ready'
  // Legacy GuestJoin sub-steps.
  | 'legacy-code-name'
  | 'legacy-choose-mode'
  | 'legacy-create-or-join'
  | 'legacy-success';

const NO_CODE_TOTAL = 4;
const WITH_CODE_TOTAL = 7;

// `/welcome` → EntryChoice → register: Welcome (1), EntryChoice (2), ProfileSetup
// or OrgPicker (3), and the Dashboard (4), which shows no bar.
const NO_CODE_STEP_NUMBER: Record<WizardStepId, number> = {
  'welcome': 1,
  'entry-choice': 2,
  'org-picker': 3,
  'profile-setup': 3,
  'team-find': 3,
  'team-join': 3,
  'team-pick-name': 3,
  'team-name-pending': 3,
  'team-waiting': 3,
  'team-invite': 3,
  'team-ready': 3,
  'legacy-code-name': 3,
  'legacy-choose-mode': 3,
  'legacy-create-or-join': 3,
  'legacy-success': 3,
};

/**
 * `/join/:code` → EntryChoice → ProfileSetup → TeamFlow → run, in 7 steps.
 * EntryChoice offers a new account or the login. Join and Invite exclude each
 * other.
 *
 * Creator path:
 *   1 Welcome → 2 EntryChoice → 3 Profile → 4 Find → 5 Invite (members join
 *   here, QR + code) → 6 PickTeamName (member list + name + Start Rallye)
 *   → 7 Ready
 *
 * Joiner path:
 *   1 Welcome → 2 EntryChoice → 3 Profile → 4 Find → 5 Join
 *   → 6 Pending|Waiting → 7 Ready
 *
 * The wait screens (Pending, Waiting) hold the bar at step 6. They update in
 * place when the WebSocket sends `team.name_updated`.
 */
const WITH_CODE_STEP_NUMBER: Record<WizardStepId, number> = {
  'welcome': 1,
  'entry-choice': 2,
  'org-picker': 3,
  'profile-setup': 3,
  'team-find': 4,
  'team-invite': 5,
  'team-join': 5,
  'team-pick-name': 6,
  'team-name-pending': 6,
  'team-waiting': 6,
  'team-ready': 7,
  'legacy-code-name': 4,
  'legacy-choose-mode': 5,
  'legacy-create-or-join': 6,
  'legacy-success': 7,
};

interface WizardStepInfo {
  current: number;
  total: number;
}

export function getStepInfo(
  stepId: WizardStepId,
  pathKind: WizardPathKind,
): WizardStepInfo {
  if (pathKind === 'with-code') {
    return { current: WITH_CODE_STEP_NUMBER[stepId], total: WITH_CODE_TOTAL };
  }
  return { current: NO_CODE_STEP_NUMBER[stepId], total: NO_CODE_TOTAL };
}
