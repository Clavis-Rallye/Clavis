import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import {
  Box,
  Button,
  IconButton,
  InputAdornment,
  Menu,
  MenuItem,
  Rating,
  TextField,
} from '@mui/material';
import { ThemeProvider } from '@mui/material/styles';
import appTheme from '../theme';
import { onboardingPrimaryPillSx } from '../styles/onboardingButtons';
import ParticipantTargets, {
  PARTICIPANT_TARGET_OVERRIDES,
  participantTargetsTheme,
  useInParticipantTargets,
} from './ParticipantTargets';

const px = (value: string) => parseFloat(value) || 0;
const minHeight = (el: HTMLElement) => px(getComputedStyle(el).minHeight);
const minWidth = (el: HTMLElement) => px(getComputedStyle(el).minWidth);

const Targets = () => (
  <>
    <Button size="small">Klein</Button>
    <IconButton aria-label="Symbol">x</IconButton>
    <Box component="button" sx={onboardingPrimaryPillSx}>
      Pille
    </Box>
  </>
);

describe('ParticipantTargets', () => {
  it('raises a small Button, an IconButton and an onboarding pill to 44 px inside the wrapper', () => {
    render(
      <ThemeProvider theme={appTheme}>
        <ParticipantTargets>
          <Targets />
        </ParticipantTargets>
      </ThemeProvider>,
    );
    for (const name of ['Klein', 'Symbol', 'Pille']) {
      const el = screen.getByRole('button', { name });
      expect(minHeight(el), `${name} min-height`).toBeGreaterThanOrEqual(44);
      expect(minWidth(el), `${name} min-width`).toBeGreaterThanOrEqual(44);
    }
  });

  it('leaves the same three at their own sizes outside the wrapper', () => {
    render(
      <ThemeProvider theme={appTheme}>
        <Targets />
      </ThemeProvider>,
    );
    expect(minHeight(screen.getByRole('button', { name: 'Klein' }))).toBeLessThan(44);
    expect(minHeight(screen.getByRole('button', { name: 'Symbol' }))).toBeLessThan(44);
    expect(minHeight(screen.getByRole('button', { name: 'Pille' }))).toBe(40);
  });

  it('beats the minHeight of the onboarding pill by specificity, not by order', () => {
    render(
      <ThemeProvider theme={appTheme}>
        <ParticipantTargets>
          <Targets />
        </ParticipantTargets>
      </ThemeProvider>,
    );
    const pill = screen.getByRole('button', { name: 'Pille' });
    const own = [...pill.classList].find((c) => c.startsWith('css-'))!;
    const css = [...document.querySelectorAll('style')].map((s) => s.textContent).join('\n');
    // The pill's own rule is one class; the wrapper's rule is a class plus an
    // element, so it wins wherever the two sit in the sheet.
    expect(css).toMatch(new RegExp(`\\.${own}\\{[^}]*min-height:40px`));
    expect(css).toMatch(/\.css-[\w-]+ button:not\(\.MuiButtonBase-root\)[^{]*\{[^}]*min-height:44px/);
  });

  it('never lowers a MUI control or the skip link', () => {
    // A text Button has min-width 64, a Tab min-height 48: neither is set here.
    expect(Object.keys(PARTICIPANT_TARGET_OVERRIDES.MuiButton.styleOverrides.root)).toEqual(['minHeight']);
    expect(PARTICIPANT_TARGET_OVERRIDES).not.toHaveProperty('MuiTab');
    expect(PARTICIPANT_TARGET_OVERRIDES).not.toHaveProperty('MuiChip');
    render(
      <ThemeProvider theme={appTheme}>
        <ParticipantTargets>
          <Button>Gross</Button>
          <a className="skip-link" href="#main-content">
            Zum Inhalt
          </a>
        </ParticipantTargets>
      </ThemeProvider>,
    );
    expect(minWidth(screen.getByRole('button', { name: 'Gross' }))).toBe(64);
    expect(minHeight(screen.getByRole('link', { name: 'Zum Inhalt' }))).toBe(0);
  });

  it('gives a Menu opened inside the wrapper items of at least 44 px', () => {
    render(
      <ThemeProvider theme={appTheme}>
        <ParticipantTargets>
          <Menu open anchorEl={document.body}>
            <MenuItem>Eins</MenuItem>
          </Menu>
        </ParticipantTargets>
      </ThemeProvider>,
    );
    expect(minHeight(screen.getByRole('menuitem', { name: 'Eins' }))).toBeGreaterThanOrEqual(44);
  });

  it('raises the stars of a rating to 44 px', () => {
    const { container } = render(
      <ThemeProvider theme={appTheme}>
        <ParticipantTargets>
          <Rating name="stars" sx={{ fontSize: '2.5rem' }} />
        </ParticipantTargets>
      </ThemeProvider>,
    );
    const icons = container.querySelectorAll<HTMLElement>('.MuiRating-icon');
    expect(icons.length).toBeGreaterThan(0);
    for (const icon of icons) {
      expect(minHeight(icon)).toBeGreaterThanOrEqual(44);
      expect(minWidth(icon)).toBeGreaterThanOrEqual(44);
    }
  });

  it('raises the icon button inside a text field to 44 px', () => {
    render(
      <ThemeProvider theme={appTheme}>
        <ParticipantTargets>
          <TextField
            label="Passwort"
            slotProps={{
              input: {
                endAdornment: (
                  <InputAdornment position="end">
                    <IconButton aria-label="Anzeigen" size="small">
                      x
                    </IconButton>
                  </InputAdornment>
                ),
              },
            }}
          />
        </ParticipantTargets>
      </ThemeProvider>,
    );
    const eye = screen.getByRole('button', { name: 'Anzeigen' });
    expect(minHeight(eye)).toBeGreaterThanOrEqual(44);
    expect(minWidth(eye)).toBeGreaterThanOrEqual(44);
  });

  it('gives the input base of a TextField a min-height of at least 44 px', () => {
    const { container } = render(
      <ThemeProvider theme={appTheme}>
        <ParticipantTargets>
          <TextField label="Nachricht" size="small" />
        </ParticipantTargets>
      </ThemeProvider>,
    );
    const base = container.querySelector<HTMLElement>('.MuiInputBase-root');
    expect(base).not.toBeNull();
    expect(minHeight(base as HTMLElement)).toBeGreaterThanOrEqual(44);
  });

  it('raises minimum sizes only: the provided theme differs from the outer one in size overrides alone', () => {
    const derived = participantTargetsTheme(appTheme);
    expect(derived.palette).toEqual(appTheme.palette);
    expect(derived.typography).toEqual(appTheme.typography);
    expect(derived.shape).toEqual(appTheme.shape);
    expect(derived.shadows).toEqual(appTheme.shadows);
    expect(derived.spacing(3)).toBe(appTheme.spacing(3));
    expect(derived.breakpoints.values).toEqual(appTheme.breakpoints.values);

    const leaves = (node: unknown, path: string[] = []): string[] =>
      node && typeof node === 'object'
        ? Object.entries(node).flatMap(([k, v]) => leaves(v, [...path, k]))
        : [path.join('.')];
    const keys = leaves(PARTICIPANT_TARGET_OVERRIDES);
    expect(keys.length).toBeGreaterThan(0);
    for (const key of keys) {
      expect(key, key).toMatch(/\.(minHeight|minWidth)$/);
    }
    // Every other component override of the app theme survives unchanged.
    expect(derived.components?.MuiButton?.styleOverrides).toMatchObject(
      appTheme.components!.MuiButton!.styleOverrides as object,
    );
  });
});

// The wiring in App.tsx: the join path sits inside the wrapper, the shared
// sign-in page does not. The pages are replaced by probes; the router, the
// layout route and the wrapper are the real ones.
vi.mock('../auth/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../auth/AuthContext')>();
  const signedOut = {
    user: null,
    isAuthenticated: false,
    isLoading: false,
    login: async () => {},
    logout: async () => {},
    checkAuth: async () => {},
    needsTwoFactorSetup: () => false,
  };
  return {
    ...actual,
    AuthProvider: ({ children }: { children: React.ReactNode }) => (
      <actual.AuthContext.Provider value={signedOut as never}>{children}</actual.AuthContext.Provider>
    ),
  };
});
vi.mock('../qa/QaFeedbackMount', () => ({ default: () => null }));
vi.mock('../pages/Login', () => ({
  default: () => <InsideProbe name="login" />,
}));
vi.mock('../pages/teamFlow/FindTeamStep', () => ({
  default: () => <InsideProbe name="find-team" />,
}));
vi.mock('../pages/WelcomeStep', () => ({
  default: () => <InsideProbe name="welcome" />,
}));
vi.mock('../pages/NoRunsAvailable', () => ({
  default: () => <InsideProbe name="no-runs" />,
}));
vi.mock('../pages/participant/RunHistory', () => ({
  default: () => <InsideProbe name="run-history" />,
}));
vi.mock('../auth/ProtectedRoute', async () => ({
  default: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

function InsideProbe({ name }: { name: string }) {
  return <p>{`${name}:${useInParticipantTargets() ? 'inside' : 'outside'}`}</p>;
}

describe('ParticipantTargets mounting in App', () => {
  it('wraps a join route and leaves /login outside', async () => {
    const { default: App } = await import('../App');
    window.history.pushState({}, '', '/join/ABC123/team/find');
    const join = render(<App />);
    expect(await screen.findByText('find-team:inside')).toBeInTheDocument();
    join.unmount();

    window.history.pushState({}, '', '/login');
    render(<App />);
    expect(await screen.findByText('login:outside')).toBeInTheDocument();
  });

  it('wraps /welcome, /no-runs and /run-history and leaves / outside', async () => {
    const { default: App } = await import('../App');
    for (const [path, probe] of [
      ['/welcome', 'welcome:inside'],
      ['/no-runs', 'no-runs:inside'],
      ['/run-history', 'run-history:inside'],
      ['/', 'welcome:outside'],
    ] as const) {
      window.history.pushState({}, '', path);
      const view = render(<App />);
      expect(await screen.findByText(probe), path).toBeInTheDocument();
      view.unmount();
    }
  });
});
