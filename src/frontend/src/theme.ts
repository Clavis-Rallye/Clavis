import { createTheme } from '@mui/material/styles';

// Extend MUI palette to include custom colors
declare module '@mui/material/styles' {
  interface Palette {
    tertiary: Palette['primary'];
    sectionBackground: string;
  }
  interface PaletteOptions {
    tertiary?: PaletteOptions['primary'];
    sectionBackground?: string;
  }
}

// Lets the prop types of the components that use ChipPropsColorOverrides
// (Chip, Badge) accept `color="tertiary"`.
declare module '@mui/material/Chip' {
  interface ChipPropsColorOverrides {
    tertiary: true;
  }
}

// Pill shape for content-width primary buttons and chips. A radius far above
// any height always gives a full pill.
export const RADIUS_FULL = 9999;

const elevation1 =
  '0px 1px 2px rgba(0, 0, 0, 0.3), 0px 1px 3px 1px rgba(0, 0, 0, 0.15)';
const elevation2 =
  '0px 1px 2px rgba(0, 0, 0, 0.3), 0px 2px 6px 2px rgba(0, 0, 0, 0.15)';
const elevation3 =
  '0px 4px 8px 3px rgba(0, 0, 0, 0.15), 0px 1px 3px rgba(0, 0, 0, 0.3)';
const elevation4 =
  '0px 6px 10px 4px rgba(0, 0, 0, 0.15), 0px 2px 3px rgba(0, 0, 0, 0.3)';
const elevation5 =
  '0px 8px 12px 6px rgba(0, 0, 0, 0.15), 0px 4px 4px rgba(0, 0, 0, 0.3)';

const theme = createTheme({
  palette: {
    primary: {
      main: '#1D47A4',       // --color-primary-40
      light: '#6E84C4',      // --color-primary-70 — non-text UI only; body text fails WCAG AA
      dark: '#002378',       // --color-primary-10
      contrastText: '#FFFFFF',
    },
    secondary: {
      main: '#FBD916',       // --color-secondary-40
      light: '#FDF597',      // --color-secondary-80
      dark: '#F97C00',       // --color-secondary-10 — #FFFFFF on dark is 2.65:1 (fail); use #1D1B20
      contrastText: '#1D1B20',
    },
    tertiary: {
      main: '#649664',       // --color-tertiary-40
      light: '#8AC38D',      // --color-tertiary-70  — non-text UI only; white text fails WCAG AA
      dark: '#1A251A',       // --color-tertiary-10
      contrastText: '#1D1B20',
    },
    error: {
      main: '#B3261E',       // --color-error-40
      light: '#F2B8B5',      // --color-error-80
      dark: '#601410',       // --color-error-20
      contrastText: '#FFFFFF',
    },
    // The design system has no warning token, so main/light/contrastText are
    // MUI's light-mode defaults — icons, chips and warning Alerts keep them.
    // MUI replaces a palette entry wholesale, so all four values are pinned.
    warning: {
      main: '#ED6C02',       // MUI default — 3.07:1 on white, icons/chips only
      light: '#FF9800',      // MUI default (orange 500)
      dark: '#9A4A00',       // 6.26:1 on white — text actions
      contrastText: '#FFFFFF',
    },
    background: {
      default: '#FFFFFF',    // --color-surface
      paper: '#FFFFFF',
    },
    text: {
      primary: '#1D1B20',    // --color-on-surface
      secondary: '#49454F',  // --color-on-surface-variant
    },
    divider: '#CAC4D0',      // --color-outline-variant
    sectionBackground: '#F5F5F5',  // --color-section-background
  },
  typography: {
    fontFamily: "'Twemoji Country Flags', 'Noto Sans', sans-serif",
    h1: {
      fontFamily: "'Twemoji Country Flags', 'Noto Sans', sans-serif",
      fontSize: '32px',
      fontWeight: 600,
      lineHeight: '40px'
    },
    h2: {
      fontFamily: "'Twemoji Country Flags', 'Noto Sans', sans-serif",
      fontSize: '28px',
      fontWeight: 600,
      lineHeight: '36px'
    },
    h3: {
      fontFamily: "'Roboto', sans-serif",
      fontSize: '24px',
      fontWeight: 600,
      lineHeight: '32px'
    },
    h4: {
      fontFamily: "'Twemoji Country Flags', 'Noto Sans', sans-serif",
      fontSize: '22px',
      fontWeight: 400,
      lineHeight: '28px'
    },
    h5: {
      fontFamily: "'Twemoji Country Flags', 'Noto Sans', sans-serif",
      fontSize: '16px',
      fontWeight: 500,
      lineHeight: '24px'
    },
    h6: {
      fontFamily: "'Twemoji Country Flags', 'Noto Sans', sans-serif",
      fontSize: '14px',
      fontWeight: 500,
      lineHeight: '20px'
    },
    subtitle1: {
      fontFamily: "'Twemoji Country Flags', 'Noto Sans', sans-serif",
      fontSize: '16px',
      fontWeight: 500,
      lineHeight: '24px'
    },
    subtitle2: {
      fontFamily: "'Twemoji Country Flags', 'Noto Sans', sans-serif",
      fontSize: '14px',
      fontWeight: 500,
      lineHeight: '20px'
    },
    body1: {
      fontFamily: "'Twemoji Country Flags', 'Noto Sans', sans-serif",
      fontSize: '16px',
      fontWeight: 400,
      lineHeight: '24px'
    },
    body2: {
      fontFamily: "'Twemoji Country Flags', 'Noto Sans', sans-serif",
      fontSize: '14px',
      fontWeight: 400,
      lineHeight: '20px'
    },
    button: {
      fontFamily: "'Twemoji Country Flags', 'Noto Sans', sans-serif",
      fontSize: '14px',
      fontWeight: 500,
      lineHeight: '20px',
      textTransform: 'none',
    },
    caption: {
      fontFamily: "'Twemoji Country Flags', 'Noto Sans', sans-serif",
      fontSize: '12px',
      fontWeight: 400,
      lineHeight: '16px'
    },
    overline: {
      fontFamily: "'Twemoji Country Flags', 'Noto Sans', sans-serif",
      fontSize: '11px',
      fontWeight: 500,
      lineHeight: '16px',
      textTransform: 'none',
    },
  },
  spacing: 8,
  shape: {
    borderRadius: 12,
  },
  shadows: [
    'none',
    elevation1,
    elevation2,
    elevation3,
    elevation4,
    elevation5,
    elevation5, elevation5, elevation5, elevation5, elevation5,
    elevation5, elevation5, elevation5, elevation5, elevation5,
    elevation5, elevation5, elevation5, elevation5, elevation5,
    elevation5, elevation5, elevation5, elevation5,
  ],
  components: {
    MuiCssBaseline: {
      styleOverrides: `
        @media (prefers-reduced-motion: reduce) {
          *, *::before, *::after {
            animation-duration: 0.01ms !important;
            animation-iteration-count: 1 !important;
            transition-duration: 0.01ms !important;
            scroll-behavior: auto !important;
          }
        }
      `,
    },
    // No scroll lock anywhere. Opening a Dialog, Select, Menu or Popover would
    // set `overflow:hidden` and `padding-right:15px` on <body>, and the whole
    // page would jump sideways by the scrollbar width. Dialog, Menu, Popover
    // and Drawer all render a Modal, so these four cover every case.
    MuiModal: {
      defaultProps: { disableScrollLock: true },
    },
    MuiPopover: {
      defaultProps: { disableScrollLock: true },
    },
    MuiMenu: {
      defaultProps: { disableScrollLock: true },
    },
    MuiDrawer: {
      defaultProps: { disableScrollLock: true },
    },
    MuiTextField: {
      defaultProps: {
        variant: 'outlined',
      },
    },
    MuiButton: {
      defaultProps: {
        disableElevation: true,
      },
      styleOverrides: {
        root: {
          borderRadius: RADIUS_FULL,
          paddingInline: 20,
          paddingBlock: 8,
          '&:focus-visible': {
            outline: '2px solid #1D47A4',
            outlineOffset: 2,
          },
        },
        sizeSmall: {
          paddingInline: 12,
          paddingBlock: 4,
          fontSize: '13px',
        },
        sizeLarge: {
          paddingInline: 24,
          paddingBlock: 10,
          fontSize: '16px',
        },
      },
    },
    MuiIconButton: {
      styleOverrides: {
        root: {
          borderRadius: RADIUS_FULL,
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          borderRadius: RADIUS_FULL,
          fontWeight: 500,
        },
      },
    },
    MuiCard: {
      defaultProps: {
        variant: 'outlined',
      },
      styleOverrides: {
        root: {
          borderRadius: 16,
          borderColor: '#CAC4D0',
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        rounded: {
          borderRadius: 12,
        },
      },
    },
    // The library paints a placeholder in the text colour at opacity 0.42 (2.63:1 on
    // white). The input slot sits on <input> and <textarea> alike.
    MuiInputBase: {
      styleOverrides: {
        input: {
          '&::placeholder': {
            color: '#787679', // 4.50:1 on white
            opacity: 1,
          },
        },
      },
    },
    MuiOutlinedInput: {
      styleOverrides: {
        root: {
          borderRadius: 12,
          '& .MuiOutlinedInput-notchedOutline': {
            borderColor: '#9C90A7', // 3.02:1 on white. #CAC4D0 would be 1.70:1.
          },
          '&:hover .MuiOutlinedInput-notchedOutline': {
            borderColor: '#79747E',
          },
          '&.Mui-error .MuiOutlinedInput-notchedOutline': {
            borderColor: '#B3261E',
            borderWidth: 2,
          },
          '&.Mui-focused.Mui-error .MuiOutlinedInput-notchedOutline': {
            borderColor: '#B3261E',
            borderWidth: 2,
          },
          // Single focus indicator: the notched outline itself becomes a 2px
          // primary border. An `outline` here, plus the global *:focus-visible
          // ring on the inner control, would stack two concentric rings.
          '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
            borderColor: '#1D47A4',
            borderWidth: 2,
          },
        },
      },
    },
    MuiAlert: {
      styleOverrides: {
        root: {
          borderRadius: 12,
        },
      },
    },
    MuiDialog: {
      styleOverrides: {
        paper: {
          borderRadius: 24,
        },
      },
    },
    MuiAppBar: {
      defaultProps: {
        elevation: 1,
      },
      styleOverrides: {
        colorPrimary: {
          backgroundColor: '#FFFFFF',
          color: '#1D1B20',
        },
      },
    },
    MuiToolbar: {
      styleOverrides: {
        root: {
          minHeight: 56,
        },
      },
    },
    MuiBottomNavigation: {
      styleOverrides: {
        root: {
          backgroundColor: '#FFFFFF',
          borderTop: '1px solid #CAC4D0',
        },
      },
    },
    MuiListItemButton: {
      styleOverrides: {
        root: {
          borderRadius: 12,
          marginInline: 8,
          marginBlock: 2,
        },
      },
    },
  },
});

export default theme;
