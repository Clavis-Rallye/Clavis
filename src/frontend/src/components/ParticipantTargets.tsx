import React, { createContext, useContext, useMemo } from 'react';
import { Outlet } from 'react-router';
import { Box } from '@mui/material';
import { ThemeProvider, createTheme, useTheme } from '@mui/material/styles';
import type { Theme } from '@mui/material/styles';

/** The smallest height and width, in CSS px, of anything a participant taps. */
const PARTICIPANT_TARGET_PX = 44;

const MIN = PARTICIPANT_TARGET_PX;
const BOTH = { minHeight: MIN, minWidth: MIN };

/**
 * Size overrides for the MUI components a participant taps. Minimum sizes
 * only: no colour, font, padding or margin.
 *
 * Left out on purpose:
 * - Button gets no min-width: its own 64 px is already above 44.
 * - Tab (48 px) and BottomNavigationAction (56 px) are already above 44.
 * - Chip: the chips on participant pages are labels, none is clickable, and
 *   44 px would grow every row they sit in.
 * - An IconButton inside a text field (clear, show password) is raised like
 *   any other: measured at 390 px, the 56 px field stays 56 px and the button
 *   stays inside it; the input only loses 10 px of width.
 */
// eslint-disable-next-line react-refresh/only-export-components
export const PARTICIPANT_TARGET_OVERRIDES = {
  MuiButton: { styleOverrides: { root: { minHeight: MIN } } },
  MuiIconButton: { styleOverrides: { root: BOTH } },
  MuiToggleButton: { styleOverrides: { root: BOTH } },
  MuiMenuItem: { styleOverrides: { root: { minHeight: MIN } } },
  MuiListItemButton: { styleOverrides: { root: { minHeight: MIN } } },
  MuiRadio: { styleOverrides: { root: BOTH } },
  MuiCheckbox: { styleOverrides: { root: BOTH } },
  // The stars of the feedback form are 40 px (2.5rem) and tapped.
  MuiRating: { styleOverrides: { root: { '& .MuiRating-icon': BOTH } } },
  // A text field below 44 px, such as the chat message input at 36. The field
  // is centred in the box, so only its height changes.
  MuiInputBase: { styleOverrides: { root: { minHeight: MIN } } },
} as const;

/** The outer theme with the minimum sizes above and nothing else changed. */
// eslint-disable-next-line react-refresh/only-export-components
export function participantTargetsTheme(outer: Theme): Theme {
  return createTheme(outer, { components: PARTICIPANT_TARGET_OVERRIDES });
}

// Custom elements the theme cannot reach: `Box component="button"` with
// `all: 'unset'` and its own minHeight (styles/onboardingButtons.ts), plain
// buttons, role="button" and anchors. A descendant selector plus `:not()` is
// more specific than the one class the pill carries, so it wins wherever the
// two rules sit in the sheet. Every MUI control is a ButtonBase and is left to
// the theme, so a Button (min-width 64) or a Tab (min-height 48) is never
// lowered to 44. The skip link hides itself at top -40 px and must keep its
// height.
const CUSTOM_TARGET = ':not(.MuiButtonBase-root):not(.skip-link)';
const scopedRule = {
  [`& button${CUSTOM_TARGET}, & [role="button"]${CUSTOM_TARGET}, & a${CUSTOM_TARGET}`]: {
    minHeight: `${MIN}px`,
    minWidth: `${MIN}px`,
  },
};

const InsideContext = createContext(false);

/** True below a ParticipantTargets wrapper. */
// eslint-disable-next-line react-refresh/only-export-components
export const useInParticipantTargets = (): boolean => useContext(InsideContext);

/**
 * Participant-only target sizes. Mounted in RunShell and around the join
 * routes in App.tsx, nowhere else: the app theme, the onboarding shell and
 * every staff page keep their sizes. Menus and dialogs that portal out of the
 * page still get the theme part through context.
 *
 * `display: contents` keeps the wrapper out of the layout it wraps.
 */
const ParticipantTargets: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const outer = useTheme();
  const theme = useMemo(() => participantTargetsTheme(outer), [outer]);
  return (
    <ThemeProvider theme={theme}>
      <InsideContext.Provider value={true}>
        <Box sx={{ display: 'contents', ...scopedRule }}>{children}</Box>
      </InsideContext.Provider>
    </ThemeProvider>
  );
};

/** Layout route form of the wrapper, for the join routes in App.tsx. */
export const ParticipantTargetsLayout: React.FC = () => (
  <ParticipantTargets>
    <Outlet />
  </ParticipantTargets>
);

export default ParticipantTargets;
