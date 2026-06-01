/**
 * Shared sx fragments for onboarding custom buttons (`Box component="button"`
 * with `all: 'unset'`). The reset strips the user-agent and MUI focus rings,
 * so each custom button puts one back, or keyboard users see no focus.
 *
 * Spread this at the end of the button's sx so it wins over earlier rules.
 */

export const pillButtonFocusRing = {
  // A 2 px primary-40 outline with an offset reads against both the brand-blue
  // filled pills and the white outline pills.
  '&:focus-visible': {
    outline: '2px solid var(--color-primary-40, #1D47A4)',
    outlineOffset: '2px',
  },
};

// The geometry of the onboarding action pills (40 px tall, Roboto 14/20, 100 px
// radius). ProfileSetupStep and OrganizationPicker sit in the same /register
// flow, so their Back and Continue buttons must render identically.
const onboardingPillBase = {
  all: 'unset',
  boxSizing: 'border-box',
  minHeight: '40px',
  paddingInline: '16px',
  borderRadius: '100px',
  fontFamily: "'Roboto', sans-serif",
  fontSize: '14px',
  fontWeight: 500,
  lineHeight: '20px',
  letterSpacing: '0.1px',
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  cursor: 'pointer',
  ...pillButtonFocusRing,
} as const;

/** Outline "Back" pill. The stroke is an inset box-shadow, so the pill stays 40 px. */
export const onboardingBackPillSx = {
  ...onboardingPillBase,
  boxShadow: 'inset 0 0 0 1px var(--color-outline-variant, #CAC4D0)',
  color: 'text.secondary',
  '&:hover': { backgroundColor: 'rgba(73, 69, 79, 0.04)' },
  '&:disabled': { cursor: 'not-allowed', opacity: 0.6 },
} as const;

/** Filled brand-blue "Continue" pill. */
export const onboardingPrimaryPillSx = {
  ...onboardingPillBase,
  backgroundColor: '#4D69B9',
  color: '#FFFFFF',
  '&:hover': { backgroundColor: '#3D54A0' },
  '&:disabled': { cursor: 'not-allowed', opacity: 0.6 },
} as const;
