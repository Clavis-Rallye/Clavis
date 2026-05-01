/**
 * Per-session onboarding state across Welcome → ProfileSetup → TeamFlow.
 *
 * `TeamFlowState` (mode, codes, team id, errors) is kept as
 * `clavis_wizard_team_state` in sessionStorage, so a reload on any team-flow
 * step restores the wizard. Email and password are NEVER stored, because a
 * credential must not sit in browser storage.
 */
import React, {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { useAuth } from '../auth/useAuth';
import type { JoinDestination } from '../routes/useResolveJoinDestination';

export type WizardPathKind = 'no-code' | 'with-code';

/** Team-flow runtime state. */
export interface TeamFlowState {
  /** Find-step selection. */
  mode: 'create' | 'join' | null;

  /** Join-path: raw code typed/scanned by the user (already uppercased). */
  enteredCode: string;
  joinError:
    | 'not_found'
    | 'full'
    | 'not_started'
    | 'rate_limited'
    | 'already_in_team'
    | 'unknown'
    | null;

  /** Create-path: locally typed name (committed via PATCH on submit). */
  teamName: string;
  teamNameError: 'forbidden' | 'unknown' | null;
  /** The raw server detail (403 detail, 422 validation, …), shown for
   * diagnosis beside the i18n message of teamNameError. */
  teamNameErrorDetail: string | null;

  /** From the backend, set after the team is created or joined. */
  teamId: string | null;
  teamShareCode: string | null;
  teamNameFromServer: string | null;
  /** The run UUID returned with the join, for PATCH and the WS subscription. */
  runId: string | null;

  /**
   * True if this user created the team (used for routing & RBAC).
   *
   * The server owns this answer (`is_creator` on my-team).
   * `useAdoptResolvedTeam` overwrites the local value with it, because a
   * browser back or a reload can drop the flag the create-path set here.
   */
  isCreator: boolean;
}

export interface WizardState {
  runCode: string | null;
  organizationSlug: string | null;
  displayName: string;
  guestMode: boolean;
  email: string;
  password: string;
  pathKind: WizardPathKind;

  teamFlow: TeamFlowState;
}

export interface WizardStateActions {
  setDisplayName(name: string): void;
  setGuestMode(on: boolean): void;
  setEmail(email: string): void;
  setPassword(pw: string): void;
  setOrganizationSlug(slug: string | null): void;
  setRunCode(code: string | null): void;
  setPathKind(kind: WizardPathKind): void;
  /** Partial update of team-flow state. */
  patchTeamFlow(patch: Partial<TeamFlowState>): void;
  resetTeamFlow(): void;
  reset(): void;
}

export type WizardStateContextValue = WizardState & WizardStateActions;

const SESSION_KEY_DISPLAY_NAME = 'clavis_wizard_displayName';
const SESSION_KEY_ORG_SLUG = 'clavis_wizard_org_slug';
const SESSION_KEY_TEAM_FLOW = 'clavis_wizard_team_state';
const SESSION_KEY_RUN_CODE = 'clavis_wizard_run_code';
const SESSION_KEY_PATH_KIND = 'clavis_wizard_path_kind';

const isWizardPathKind = (value: string | null): value is WizardPathKind =>
  value === 'no-code' || value === 'with-code';

const emptyTeamFlow = (): TeamFlowState => ({
  mode: null,
  enteredCode: '',
  joinError: null,
  teamName: '',
  teamNameError: null,
  teamNameErrorDetail: null,
  teamId: null,
  teamShareCode: null,
  teamNameFromServer: null,
  runId: null,
  isCreator: false,
});

const initialState = (): WizardState => {
  let displayName = '';
  let organizationSlug: string | null = null;
  let runCode: string | null = null;
  let pathKind: WizardPathKind = 'no-code';
  let teamFlow: TeamFlowState = emptyTeamFlow();
  if (typeof window !== 'undefined' && window.sessionStorage) {
    try {
      displayName = window.sessionStorage.getItem(SESSION_KEY_DISPLAY_NAME) ?? '';
      organizationSlug =
        window.sessionStorage.getItem(SESSION_KEY_ORG_SLUG) || null;
      runCode = window.sessionStorage.getItem(SESSION_KEY_RUN_CODE) || null;
      const rawPathKind = window.sessionStorage.getItem(SESSION_KEY_PATH_KIND);
      if (isWizardPathKind(rawPathKind)) {
        pathKind = rawPathKind;
      }
      const rawTeam = window.sessionStorage.getItem(SESSION_KEY_TEAM_FLOW);
      if (rawTeam) {
        const parsed = JSON.parse(rawTeam) as Partial<TeamFlowState>;
        teamFlow = { ...emptyTeamFlow(), ...parsed };
      }
    } catch {
      // Storage is blocked, as in some private-mode browsers.
    }
  }
  return {
    runCode,
    organizationSlug,
    displayName,
    guestMode: false,
    email: '',
    password: '',
    pathKind,
    teamFlow,
  };
};

// eslint-disable-next-line react-refresh/only-export-components
export const WizardStateContext = createContext<WizardStateContextValue | null>(
  null,
);

interface ProviderProps {
  children: React.ReactNode;
}

export const WizardStateProvider: React.FC<ProviderProps> = ({ children }) => {
  const [state, setState] = useState<WizardState>(initialState);
  const { isAuthenticated, user } = useAuth();
  const wasAuthenticated = useRef(isAuthenticated);

  // Reset on logout (auth → unauth transition).
  useEffect(() => {
    if (wasAuthenticated.current && !isAuthenticated) {
      setState(initialState());
    }
    wasAuthenticated.current = isAuthenticated;
  }, [isAuthenticated]);

  // Backfill organizationSlug from the authenticated user. WelcomeStep and
  // OrganizationPicker normally set it. A user who lands directly on a later
  // step (deep link, reload on /team/invite) skips them, and API calls that
  // need the slug then break silently, such as `advanceTeamStage` in
  // InviteTeamStep, which moves joiners on through the WS event.
  // `/api/auth/me` carries `organization_slug`, so it fills the gap.
  useEffect(() => {
    if (user?.organization_slug && !state.organizationSlug) {
      setState((s) => ({ ...s, organizationSlug: user.organization_slug }));
    }
  }, [user?.organization_slug, state.organizationSlug]);

  // Only these reach sessionStorage: displayName, organizationSlug, runCode,
  // pathKind and the team flow.
  useEffect(() => {
    if (typeof window === 'undefined' || !window.sessionStorage) return;
    try {
      if (state.displayName) {
        window.sessionStorage.setItem(SESSION_KEY_DISPLAY_NAME, state.displayName);
      } else {
        window.sessionStorage.removeItem(SESSION_KEY_DISPLAY_NAME);
      }
      if (state.organizationSlug) {
        window.sessionStorage.setItem(SESSION_KEY_ORG_SLUG, state.organizationSlug);
      } else {
        window.sessionStorage.removeItem(SESSION_KEY_ORG_SLUG);
      }
      // The join code and the path it came in on must survive 2FA and a
      // reload, as displayName and organizationSlug do.
      if (state.runCode) {
        window.sessionStorage.setItem(SESSION_KEY_RUN_CODE, state.runCode);
      } else {
        window.sessionStorage.removeItem(SESSION_KEY_RUN_CODE);
      }
      window.sessionStorage.setItem(SESSION_KEY_PATH_KIND, state.pathKind);
      // Persist team-flow state (no PII, just IDs/codes/flags).
      const tf = state.teamFlow;
      const hasTeamProgress =
        tf.mode !== null ||
        tf.teamId !== null ||
        tf.teamShareCode !== null ||
        tf.enteredCode !== '' ||
        tf.teamName !== '';
      if (hasTeamProgress) {
        window.sessionStorage.setItem(SESSION_KEY_TEAM_FLOW, JSON.stringify(tf));
      } else {
        window.sessionStorage.removeItem(SESSION_KEY_TEAM_FLOW);
      }
    } catch {
      // ignore
    }
  }, [
    state.displayName,
    state.organizationSlug,
    state.runCode,
    state.pathKind,
    state.teamFlow,
  ]);

  const actions = useMemo<WizardStateActions>(
    () => ({
      setDisplayName: (name) =>
        setState((s) => ({ ...s, displayName: name })),
      setGuestMode: (on) => setState((s) => ({ ...s, guestMode: on })),
      setEmail: (email) => setState((s) => ({ ...s, email })),
      setPassword: (password) => setState((s) => ({ ...s, password })),
      setOrganizationSlug: (slug) =>
        setState((s) => ({ ...s, organizationSlug: slug })),
      setRunCode: (code) => setState((s) => ({ ...s, runCode: code })),
      setPathKind: (kind) => setState((s) => ({ ...s, pathKind: kind })),
      patchTeamFlow: (patch) =>
        setState((s) => ({ ...s, teamFlow: { ...s.teamFlow, ...patch } })),
      resetTeamFlow: () =>
        setState((s) => ({ ...s, teamFlow: emptyTeamFlow() })),
      reset: () => {
        setState({
          runCode: null,
          organizationSlug: null,
          displayName: '',
          guestMode: false,
          email: '',
          password: '',
          pathKind: 'no-code',
          teamFlow: emptyTeamFlow(),
        });
        if (typeof window !== 'undefined' && window.sessionStorage) {
          try {
            window.sessionStorage.removeItem(SESSION_KEY_DISPLAY_NAME);
            window.sessionStorage.removeItem(SESSION_KEY_ORG_SLUG);
            window.sessionStorage.removeItem(SESSION_KEY_RUN_CODE);
            window.sessionStorage.removeItem(SESSION_KEY_PATH_KIND);
            window.sessionStorage.removeItem(SESSION_KEY_TEAM_FLOW);
          } catch {
            // ignore
          }
        }
      },
    }),
    [],
  );

  const value = useMemo<WizardStateContextValue>(
    () => ({ ...state, ...actions }),
    [state, actions],
  );

  return (
    <WizardStateContext.Provider value={value}>
      {children}
    </WizardStateContext.Provider>
  );
};

/**
 * Adopt the my-team resolution into the team-flow state.
 *
 * After browser back, a user arrives on an earlier wizard step with a team
 * that exists on the server, while the wizard state may be empty (a reload,
 * another tab, a cleared sessionStorage). The resolution carries the server
 * truth, so it is written in before the caller redirects. The invite step then
 * recognises the existing team instead of creating a second one, and the
 * waiting step can subscribe to the run socket.
 *
 * Only a `team-pending` resolution seeds anything. Outside a
 * `<WizardStateProvider>` this is a no-op, so a guard stays renderable in a
 * test that mounts no provider.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function useAdoptResolvedTeam(destination: JoinDestination): void {
  const ctx = useContext(WizardStateContext);
  const patchTeamFlow = ctx?.patchTeamFlow;
  const pending = destination.kind === 'team-pending' ? destination : null;
  const teamId = pending?.teamId ?? null;
  const runId = pending?.runId ?? null;
  const teamShareCode = pending?.teamShareCode ?? null;
  const isCreator = pending?.isCreator ?? false;

  useEffect(() => {
    if (!patchTeamFlow || !teamId || !runId) return;
    patchTeamFlow({
      mode: isCreator ? 'create' : 'join',
      teamId,
      runId,
      teamShareCode,
      isCreator,
    });
  }, [patchTeamFlow, teamId, runId, teamShareCode, isCreator]);
}
