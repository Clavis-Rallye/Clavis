import type { ChallengeType, ScoringRules } from '../api/types';

// Which scoring keys mean anything for which challenge type.
//
// `auto_approve` acts only on QUIZ and QR_SCAN, the two types the backend can
// score on its own (services/auto_score.py). On any other type the flag is
// inert: the challenge still waits for a coordinator while the editor shows a
// ticked box. The create form keeps its scoring_rules across a type switch, so
// a quiz that is saved as text would carry the quiz keys along.
const TYPE_SPECIFIC_KEYS: Partial<Record<ChallengeType, readonly string[]>> = {
  quiz: ['auto_approve', 'options', 'correct_option'],
  qr_scan: ['auto_approve', 'expected_value'],
};

/** Keys that are meaningful for every challenge type. */
const UNIVERSAL_KEYS: readonly string[] = ['max_points', 'time_bonus'];

/**
 * Drop scoring keys that carry no meaning for `type`.
 *
 * Silent by design: a type switch is a normal editing move, not a mistake. It
 * raises no warning and leaves the challenge matching what the form shows.
 */
export function pruneScoringRules(
  type: ChallengeType | null,
  rules: ScoringRules,
): ScoringRules {
  if (!type) return rules;
  const allowed = new Set([...UNIVERSAL_KEYS, ...(TYPE_SPECIFIC_KEYS[type] ?? [])]);
  const pruned: ScoringRules = {};
  for (const [key, value] of Object.entries(rules)) {
    if (allowed.has(key)) pruned[key] = value;
  }
  return pruned;
}

/**
 * Drop the old type's configuration when an existing challenge changes type.
 * Mirrors `prune_scoring_rules_for_type_change` in the backend
 * (services/scoring_rules.py): only `max_points` and `time_bonus` survive a
 * real type change.
 *
 * `auto_approve` goes too, although QUIZ and QR_SCAN both honour it. It is one
 * half of a pair, and the key it scores with (`correct_option` for QUIZ,
 * `expected_value` for QR_SCAN) belongs to the old type. Sent alone, the flag
 * makes a change from quiz to qr_scan answer 422 over a value the creator never
 * entered.
 */
export function pruneScoringRulesForTypeChange(
  from: ChallengeType | null,
  to: ChallengeType | null,
  rules: ScoringRules,
): ScoringRules {
  if (!to) return rules;
  if (!from || from === to) return pruneScoringRules(to, rules);
  const pruned: ScoringRules = {};
  for (const [key, value] of Object.entries(rules)) {
    if (UNIVERSAL_KEYS.includes(key)) pruned[key] = value;
  }
  return pruned;
}

// The order the type-change confirmation names the discarded keys in: the
// answer options and the correct answer, the expected QR value, then automatic
// approval.
const DISCARDABLE_KEYS: readonly string[] = [
  'options',
  'correct_option',
  'expected_value',
  'auto_approve',
];

/** Whether a rule value is a configuration the creator would miss. */
function isConfigured(value: unknown): boolean {
  if (value === undefined || value === null || value === false || value === '') return false;
  if (Array.isArray(value)) return value.some((entry) => String(entry).trim() !== '');
  return true;
}

/**
 * The configured scoring keys a change from `from` to `to` throws away.
 *
 * The editor names them one by one before it saves, so a creator learns what
 * the new type costs while the old type is still selected.
 * A key that carries nothing (unticked, empty, an array of empty strings) is
 * left out: naming it would promise a loss that does not happen.
 */
export function discardedScoringKeys(
  from: ChallengeType | null,
  to: ChallengeType | null,
  rules: ScoringRules,
): string[] {
  const kept = pruneScoringRulesForTypeChange(from, to, rules);
  const dropped = Object.keys(rules).filter((key) => !(key in kept) && isConfigured(rules[key]));
  return [
    ...DISCARDABLE_KEYS.filter((key) => dropped.includes(key)),
    ...dropped.filter((key) => !DISCARDABLE_KEYS.includes(key)),
  ];
}

/** Whether the auto-approve affordance should be offered for this type at all. */
export function supportsAutoApprove(type: ChallengeType | null): boolean {
  return type === 'quiz' || type === 'qr_scan';
}
