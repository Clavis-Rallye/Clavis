/**
 * Shared sx fragments for the auth screens inside the onboarding chrome
 * (OnboardingShell + ClavisTextField). These screens have no design of their
 * own. The values mirror Login.tsx, so the secondary auth flows (forgot and
 * reset password, register, 2FA) read as part of the same app. Login keeps its
 * own inlined styles.
 */
import type { SxProps, Theme } from '@mui/material/styles';

import { pillButtonFocusRing } from './onboardingButtons';

/** Page heading: Roboto 24/600/32, brand-capable via a <Trans> span. */
export const authHeadingSx: SxProps<Theme> = {
  fontFamily: "'Roboto', sans-serif",
  fontSize: '24px',
  fontWeight: 600,
  lineHeight: '32px',
  color: 'text.primary',
  margin: 0,
};

/** Sub-heading / supporting copy under the title. */
export const authSubtitleSx: SxProps<Theme> = {
  fontFamily: "'Noto Sans', sans-serif",
  fontSize: '16px',
  fontWeight: 500,
  lineHeight: '24px',
  letterSpacing: '0.15px',
  color: '#322F35',
  marginBlockEnd: '16px',
};

/** Field label sitting above each ClavisTextField. */
export const authLabelSx: SxProps<Theme> = {
  fontFamily: "'Noto Sans', sans-serif",
  fontSize: '16px',
  fontWeight: 500,
  lineHeight: '24px',
  letterSpacing: '0.15px',
  color: 'text.primary',
};

/** Inline link styled like Login's "forgot password" / "register" links. */
export const authLinkSx: SxProps<Theme> = {
  fontFamily: "'Noto Sans', sans-serif",
  fontSize: '16px',
  fontWeight: 500,
  lineHeight: '24px',
  letterSpacing: '0.15px',
  color: 'primary.main',
};

/** Primary pill button: brand fill, restored disabled style, focus ring. */
export const authPrimaryButtonSx: SxProps<Theme> = {
  marginBlockStart: '16px',
  alignSelf: 'flex-start',
  minHeight: '40px',
  paddingInline: '24px',
  borderRadius: '100px',
  backgroundColor: '#4D69B9',
  color: '#FFFFFF',
  textTransform: 'none',
  fontFamily: "'Roboto', sans-serif",
  fontSize: '14px',
  fontWeight: 500,
  lineHeight: '20px',
  letterSpacing: '0.1px',
  '&:hover': { backgroundColor: '#3D54A0' },
  // MUI sets pointer-events:none on disabled buttons. The doubled `&&`
  // out-specifies MUI's contained-disabled background, so the design fill holds.
  '&&.Mui-disabled': {
    backgroundColor: '#9AA6CF',
    color: '#FFFFFF',
  },
  ...pillButtonFocusRing,
};

/** The form column, as Login's stack. It has no horizontal padding, because
 *  the shell provides the 40 px gutters for these no-hero screens. */
export const authFormSx: SxProps<Theme> = {
  display: 'flex',
  flexDirection: 'column',
  gap: '8px',
  paddingBlockStart: '24px',
};
