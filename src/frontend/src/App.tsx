import { lazy } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router';
import './i18n';
import { AuthProvider } from './auth/AuthContext';
import { WizardStateProvider } from './wizard/WizardStateContext';
import ProtectedRoute from './auth/ProtectedRoute';
import AppShell from './layouts/AppShell';
import ErrorBoundary from './components/ErrorBoundary';
import LazyRoute from './components/LazyRoute';
import IndexRedirect from './components/IndexRedirect';
import RouteFocus from './components/RouteFocus';
import DocumentTitle from './routes/DocumentTitle';
import QaFeedbackMount from './qa/QaFeedbackMount';
import Login from './pages/Login';
import TwoFactorSetup from './pages/TwoFactorSetup';
import TwoFactorVerify from './pages/TwoFactorVerify';
import ForgotPassword from './pages/ForgotPassword';
import ResetPassword from './pages/ResetPassword';
import AcceptInvite from './pages/AcceptInvite';
import Profile from './pages/Profile';
import FindTeamStep from './pages/teamFlow/FindTeamStep';
import JoinTeamStep from './pages/teamFlow/JoinTeamStep';
import PickTeamNameStep from './pages/teamFlow/PickTeamNameStep';
import TeamNamePendingStep from './pages/teamFlow/TeamNamePendingStep';
import TeamWaitingStep from './pages/teamFlow/TeamWaitingStep';
import InviteTeamStep from './pages/teamFlow/InviteTeamStep';
import WelcomeStep from './pages/WelcomeStep';
import WelcomeGuard from './routes/WelcomeGuard';
import DashboardRoute from './routes/DashboardRoute';
import EntryChoiceStep from './pages/EntryChoiceStep';
import EntryChoiceGuard from './routes/EntryChoiceGuard';
import ProfileSetupStep from './pages/ProfileSetupStep';
import ProfileSetupRedirect from './routes/ProfileSetupRedirect';
import { OrgBootstrapRoute } from './pages/Register';
import NoRunsAvailable from './pages/NoRunsAvailable';
import { ParticipantTargetsLayout } from './components/ParticipantTargets';
import RunShell from './layouts/RunShell';
import TeamTab from './layouts/TeamTab';
import FeedbackForm from './pages/participant/FeedbackForm';
import StationList from './pages/participant/StationList';
import StationChallengeList from './pages/participant/StationChallengeList';
import ChallengeView from './pages/participant/ChallengeView';
import TeamChat from './pages/participant/TeamChat';
import TeamInfo from './pages/participant/TeamInfo';
import FeedView from './pages/participant/FeedView';
import ParticipantLeaderboard from './pages/participant/Leaderboard';
import RunHistory from './pages/participant/RunHistory';
import Impressum from './pages/legal/Impressum';
import Privacy from './pages/legal/Privacy';
import Accessibility from './pages/legal/Accessibility';
import PublicResults from './pages/public/PublicResults';

// Staff, operator and content-management pages load on first navigation
// because they pull in the heavy editor (@tiptap) and chart (@mui/x-charts)
// libraries that a participant on a join link never needs.
const RallyeList = lazy(() => import('./pages/rallyes/RallyeList'));
const RallyeCreate = lazy(() => import('./pages/rallyes/RallyeCreate'));
const RallyeDetail = lazy(() => import('./pages/rallyes/RallyeDetail'));
const StationLibraryList = lazy(() => import('./pages/stations/StationLibraryList'));
const StationCreate = lazy(() => import('./pages/stations/StationCreate'));
const StationDetail = lazy(() => import('./pages/stations/StationDetail'));
const ChallengeLibraryList = lazy(() => import('./pages/challenges/ChallengeLibraryList'));
const ChallengeCreate = lazy(() => import('./pages/challenges/ChallengeCreate'));
const ChallengeDetail = lazy(() => import('./pages/challenges/ChallengeDetail'));
const RunList = lazy(() => import('./pages/runs/RunList'));
const RunAnalytics = lazy(() => import('./pages/analytics/RunAnalytics'));
const RunCreate = lazy(() => import('./pages/runs/RunCreate'));
const RunDashboard = lazy(() => import('./pages/runs/RunDashboard'));
const CoordinatorTeamChat = lazy(() => import('./pages/runs/CoordinatorTeamChat'));
const SubmissionReview = lazy(() => import('./pages/runs/SubmissionReview'));
const Scoreboard = lazy(() => import('./pages/runs/Scoreboard'));
const RunResults = lazy(() => import('./pages/runs/RunResults'));
const UserList = lazy(() => import('./pages/admin/UserList'));
const OrgSettings = lazy(() => import('./pages/admin/OrgSettings'));
const LogViewer = lazy(() => import('./pages/admin/LogViewer'));
const InstanceLogin = lazy(() => import('./pages/instance/InstanceLogin'));
const InstanceOrganizations = lazy(() => import('./pages/instance/InstanceOrganizations'));
const InstanceLegal = lazy(() => import('./pages/instance/InstanceLegal'));
const InstanceAudit = lazy(() => import('./pages/instance/InstanceAudit'));
const InstanceAccount = lazy(() => import('./pages/instance/InstanceAccount'));

function App() {
  return (
      <ErrorBoundary>
        <AuthProvider>
          <Router>
            <RouteFocus />
            {/* Sets the title "<page name> · <brand>" on every route. */}
            <DocumentTitle />
            {/* The feedback bar for test sessions, off by default. */}
            <QaFeedbackMount />
            <WizardStateProvider>
            <Routes>
              {/* Public routes */}
              <Route path="/login" element={<Login />} />
              {/* `/register` hosts the consolidated ProfileSetupStep. The operator
                  org-bootstrap form lives at `/register/organization`. */}
              <Route
                path="/register"
                element={
                  <ProfileSetupRedirect>
                    <ProfileSetupStep guestModeDefault={false} />
                  </ProfileSetupRedirect>
                }
              />
              {/* The bootstrap form is route-gated: once an
                  organization exists (and open signups are off) the route
                  answers with the login page, the API with 403. */}
              <Route
                path="/register/organization"
                element={<OrgBootstrapRoute />}
              />
              <Route path="/2fa-setup" element={<TwoFactorSetup />} />
              <Route path="/2fa-verify" element={<TwoFactorVerify />} />
              <Route path="/forgot-password" element={<ForgotPassword />} />
              <Route path="/reset-password" element={<ResetPassword />} />
              {/* Invitees arrive here from their mail with no account yet. */}
              <Route path="/accept-invite" element={<AcceptInvite />} />
              {/* The participant pages, one layout route:
                  tap targets of 44 px. It holds the join path, /welcome,
                  /no-runs and /run-history. / stays outside, it is the staff
                  and public entry. */}
              <Route element={<ParticipantTargetsLayout />}>
              {/* The welcome screen is the public entrypoint. */}
              <Route path="/welcome" element={<WelcomeStep />} />
              <Route
                path="/auth-choice"
                element={
                  <EntryChoiceGuard>
                    <EntryChoiceStep />
                  </EntryChoiceGuard>
                }
              />
              <Route
                path="/join/:code/auth-choice"
                element={
                  <EntryChoiceGuard>
                    <EntryChoiceStep />
                  </EntryChoiceGuard>
                }
              />
              <Route
                path="/join/:code/profile"
                element={
                  <ProfileSetupRedirect>
                    <ProfileSetupStep guestModeDefault />
                  </ProfileSetupRedirect>
                }
              />
              <Route path="/join" element={<Navigate to="/welcome" replace />} />
              <Route
                path="/join/:code"
                element={
                  <WelcomeGuard>
                    <WelcomeStep />
                  </WelcomeGuard>
                }
              />
              {/* Team-flow nested routes. The steps are mutually exclusive
                  children. The user enters via /join/:code/team/find after
                  ProfileSetup, then progresses based on their choice. */}
              <Route path="/join/:code/team" element={<Navigate to="find" replace />} />
              <Route path="/join/:code/team/find" element={<FindTeamStep />} />
              <Route path="/join/:code/team/join" element={<JoinTeamStep />} />
              <Route path="/join/:code/team/name" element={<PickTeamNameStep />} />
              <Route path="/join/:code/team/pending" element={<TeamNamePendingStep />} />
              <Route path="/join/:code/team/waiting" element={<TeamWaitingStep />} />
              <Route path="/join/:code/team/invite" element={<InviteTeamStep />} />
              {/* Landing for a codeless
                  participant on a single-organization instance: explains how
                  to join with a code instead of claiming no run exists.
                  Public: the page states a fact, it reveals nothing. */}
              <Route path="/no-runs" element={<NoRunsAvailable />} />
              <Route
                path="/run-history"
                element={
                  <ProtectedRoute minRole="guest">
                    <RunHistory />
                  </ProtectedRoute>
                }
              />
              </Route>
              <Route path="/legal/impressum" element={<Impressum />} />
              <Route path="/legal/privacy" element={<Privacy />} />
              <Route path="/legal/accessibility" element={<Accessibility />} />
              {/* Public final standing behind /results#<slug>. Outside
                  every guard: the visitor has no account, the slug is a fragment
                  the browser never sends and the page posts it to the lookup API. */}
              <Route path="/results" element={<PublicResults />} />
              {/* The operator console. Its own login and its
                  own session cookie, outside AppShell and ProtectedRoute:
                  useInstanceAuth (not the app-wide auth probe) guards
                  /instance and redirects to /instance/login. */}
              <Route path="/instance/login" element={<LazyRoute><InstanceLogin /></LazyRoute>} />
              <Route path="/instance" element={<LazyRoute><InstanceOrganizations /></LazyRoute>} />
              <Route path="/instance/legal" element={<LazyRoute><InstanceLegal /></LazyRoute>} />
              <Route path="/instance/audit" element={<LazyRoute><InstanceAudit /></LazyRoute>} />
              <Route path="/instance/account" element={<LazyRoute><InstanceAccount /></LazyRoute>} />

              {/* Participant run shell — authenticated (incl. guests) */}
              <Route
                path="/runs/:runId/participant"
                element={
                  <ProtectedRoute minRole="guest">
                    <RunShell />
                  </ProtectedRoute>
                }
              >
                {/* Land on the stations, not the feed. At run start the feed is
                    empty and carries no call to action, so the first thing a
                    participant would see is a blank page. The stations are the
                    run. */}
                <Route index element={<IndexRedirect to="stations" />} />
                <Route path="stations" element={<StationList />} />
                <Route path="feed" element={<FeedView />} />
                <Route path="team" element={<TeamTab />}>
                  {/* The Team tab opens on the leaderboard, as the bottom-nav
                      button does (TAB_ROUTE in participantNav.ts). */}
                  <Route index element={<IndexRedirect to="leaderboard" />} />
                  <Route path="leaderboard" element={<ParticipantLeaderboard />} />
                  <Route path="chat" element={<TeamChat />} />
                  <Route path="info" element={<TeamInfo />} />
                </Route>
                {/* Redirects for the old paths. */}
                <Route path="chat" element={<Navigate to="../team/chat" replace />} />
                <Route path="scoreboard" element={<Navigate to="../team/leaderboard" replace />} />
                <Route path="stations/:stationId" element={<StationChallengeList />} />
                <Route
                  path="stations/:stationId/challenges/:challengeId"
                  element={<ChallengeView />}
                />
                <Route path="feedback" element={<FeedbackForm />} />
              </Route>

              {/* `/` shows the Welcome screen for guests; logged-in
                  users are redirected straight to /dashboard by WelcomeGuard. */}
              <Route
                path="/"
                element={
                  <WelcomeGuard>
                    <WelcomeStep />
                  </WelcomeGuard>
                }
              />

              {/* Protected routes with AppShell — path-less layout so `/` stays
                  free for the Welcome screen above. */}
              <Route
                element={
                  <ProtectedRoute>
                    <AppShell />
                  </ProtectedRoute>
                }
              >
                {/* Participants and guests never see the staff dashboard:
                    DashboardRoute sends them into their run or to /no-runs. */}
                <Route path="dashboard" element={<DashboardRoute />} />
                <Route path="profile" element={<Profile />} />
                <Route
                  path="rallyes"
                  element={
                    <ProtectedRoute minRole="creator">
                      <LazyRoute><RallyeList /></LazyRoute>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="rallyes/new"
                  element={
                    <ProtectedRoute minRole="creator">
                      <LazyRoute><RallyeCreate /></LazyRoute>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="rallyes/:rallyeId"
                  element={
                    <ProtectedRoute minRole="creator">
                      <LazyRoute><RallyeDetail /></LazyRoute>
                    </ProtectedRoute>
                  }
                />
                {/* Global Station library + composition */}
                <Route
                  path="stations"
                  element={
                    <ProtectedRoute minRole="creator">
                      <LazyRoute><StationLibraryList /></LazyRoute>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="stations/new"
                  element={
                    <ProtectedRoute minRole="creator">
                      <LazyRoute><StationCreate /></LazyRoute>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="stations/:stationId"
                  element={
                    <ProtectedRoute minRole="creator">
                      <LazyRoute><StationDetail /></LazyRoute>
                    </ProtectedRoute>
                  }
                />
                {/* Global Challenge library */}
                <Route
                  path="challenges"
                  element={
                    <ProtectedRoute minRole="creator">
                      <LazyRoute><ChallengeLibraryList /></LazyRoute>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="challenges/new"
                  element={
                    <ProtectedRoute minRole="creator">
                      <LazyRoute><ChallengeCreate /></LazyRoute>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="challenges/:challengeId"
                  element={
                    <ProtectedRoute minRole="creator">
                      <LazyRoute><ChallengeDetail /></LazyRoute>
                    </ProtectedRoute>
                  }
                />
                <Route path="runs" element={<ProtectedRoute minRole="coordinator"><LazyRoute><RunList /></LazyRoute></ProtectedRoute>} />
                <Route path="runs/new" element={<ProtectedRoute minRole="coordinator"><LazyRoute><RunCreate /></LazyRoute></ProtectedRoute>} />
                <Route path="runs/:runId" element={<ProtectedRoute minRole="coordinator"><LazyRoute><RunDashboard /></LazyRoute></ProtectedRoute>} />
                <Route path="runs/:runId/teams/:teamId/chat" element={<ProtectedRoute minRole="coordinator"><LazyRoute><CoordinatorTeamChat /></LazyRoute></ProtectedRoute>} />
                <Route path="runs/:runId/submissions" element={<ProtectedRoute minRole="coordinator"><LazyRoute><SubmissionReview /></LazyRoute></ProtectedRoute>} />
                <Route path="runs/:runId/scoreboard" element={<ProtectedRoute minRole="coordinator"><LazyRoute><Scoreboard /></LazyRoute></ProtectedRoute>} />
                {/* Presentable closing view for the event. */}
                <Route path="runs/:runId/results" element={<ProtectedRoute minRole="coordinator"><LazyRoute><RunResults /></LazyRoute></ProtectedRoute>} />
                <Route path="analytics" element={<ProtectedRoute minRole="coordinator"><LazyRoute><RunAnalytics /></LazyRoute></ProtectedRoute>} />
                <Route
                  path="admin/settings"
                  element={
                    <ProtectedRoute minRole="org_admin">
                      <LazyRoute><OrgSettings /></LazyRoute>
                    </ProtectedRoute>
                  }
                />
                {/* Backwards-compat redirect for the old /settings path. */}
                <Route
                  path="settings"
                  element={<Navigate to="/admin/settings" replace />}
                />
                <Route
                  path="admin/users"
                  element={
                    <ProtectedRoute minRole="org_admin">
                      <LazyRoute><UserList /></LazyRoute>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path="admin/logs"
                  element={
                    <ProtectedRoute minRole="org_admin">
                      <LazyRoute><LogViewer /></LazyRoute>
                    </ProtectedRoute>
                  }
                />
              </Route>

              {/* Catch-all redirect */}
              <Route path="*" element={<Navigate to="/dashboard" replace />} />
            </Routes>
            </WizardStateProvider>
          </Router>
        </AuthProvider>
      </ErrorBoundary>
  );
}

export default App;
