import { matchPath } from 'react-router';

/**
 * The name of each page in the browser tab and the window list, from route pattern to a key under `pageTitle` in the common
 * namespace. `DocumentTitle` turns it into "<page name> · <brand>".
 *
 * The patterns are the absolute paths of the `<Route>`s in `App.tsx`. A
 * route that only redirects has no entry; `pageTitles.test.ts` fails for a
 * route that is neither here nor marked as a redirect there.
 *
 * The words are the ones the page shows as its main heading. A detail page
 * whose heading is the name of a record is named after the kind of the
 * record (run, rallye, station, challenge).
 */
export const PAGE_TITLE_KEYS: Readonly<Record<string, string>> = {
  // Public and onboarding
  '/': 'pageTitle.welcome',
  '/welcome': 'pageTitle.welcome',
  '/join/:code': 'pageTitle.welcome',
  '/login': 'pageTitle.login',
  '/register': 'pageTitle.profileSetup',
  '/register/organization': 'pageTitle.createAccount',
  '/2fa-setup': 'pageTitle.twoFactorSetup',
  '/2fa-verify': 'pageTitle.twoFactorVerify',
  '/forgot-password': 'pageTitle.forgotPassword',
  '/reset-password': 'pageTitle.resetPassword',
  '/accept-invite': 'pageTitle.acceptInvite',
  '/auth-choice': 'pageTitle.entryChoice',
  '/join/:code/auth-choice': 'pageTitle.entryChoice',
  '/join/:code/profile': 'pageTitle.profileSetup',
  '/join/:code/team/find': 'pageTitle.teamFind',
  '/join/:code/team/join': 'pageTitle.teamJoin',
  '/join/:code/team/name': 'pageTitle.teamName',
  '/join/:code/team/pending': 'pageTitle.teamName',
  '/join/:code/team/waiting': 'pageTitle.teamWaiting',
  '/join/:code/team/invite': 'pageTitle.teamInvite',
  '/legal/impressum': 'pageTitle.impressum',
  '/legal/privacy': 'pageTitle.privacy',
  '/legal/accessibility': 'pageTitle.accessibility',
  '/results': 'pageTitle.results',
  '/no-runs': 'pageTitle.noRuns',
  '/run-history': 'pageTitle.runHistory',

  // Operator console
  '/instance/login': 'pageTitle.instanceLogin',
  '/instance': 'pageTitle.instanceOrganizations',
  '/instance/legal': 'pageTitle.instanceLegal',
  '/instance/audit': 'pageTitle.instanceAudit',
  '/instance/account': 'pageTitle.instanceAccount',

  // Participant run shell
  '/runs/:runId/participant/stations': 'pageTitle.stations',
  '/runs/:runId/participant/feed': 'pageTitle.feed',
  '/runs/:runId/participant/team/leaderboard': 'pageTitle.leaderboard',
  '/runs/:runId/participant/team/chat': 'pageTitle.teamChat',
  '/runs/:runId/participant/team/info': 'pageTitle.teamInfo',
  '/runs/:runId/participant/stations/:stationId': 'pageTitle.station',
  '/runs/:runId/participant/stations/:stationId/challenges/:challengeId': 'pageTitle.challenge',
  '/runs/:runId/participant/feedback': 'pageTitle.feedback',

  // Staff
  '/dashboard': 'pageTitle.dashboard',
  '/profile': 'pageTitle.profile',
  '/rallyes': 'pageTitle.rallyes',
  '/rallyes/new': 'pageTitle.rallyeNew',
  '/rallyes/:rallyeId': 'pageTitle.rallye',
  '/stations': 'pageTitle.stations',
  '/stations/new': 'pageTitle.stationNew',
  '/stations/:stationId': 'pageTitle.station',
  '/challenges': 'pageTitle.challenges',
  '/challenges/new': 'pageTitle.challengeNew',
  '/challenges/:challengeId': 'pageTitle.challenge',
  '/runs': 'pageTitle.runs',
  '/runs/new': 'pageTitle.runNew',
  '/runs/:runId': 'pageTitle.run',
  '/runs/:runId/teams/:teamId/chat': 'pageTitle.coordinatorTeamChat',
  '/runs/:runId/submissions': 'pageTitle.submissions',
  '/runs/:runId/scoreboard': 'pageTitle.scoreboard',
  '/runs/:runId/results': 'pageTitle.results',
  '/analytics': 'pageTitle.analytics',
  '/admin/settings': 'pageTitle.orgSettings',
  '/admin/users': 'pageTitle.users',
  '/admin/logs': 'pageTitle.logs',
};

/** A fixed segment outranks a parameter, so `/runs/new` wins over `/runs/:runId`. */
function specificity(pattern: string): number {
  return pattern
    .split('/')
    .filter(Boolean)
    .reduce((score, segment) => score + (segment.startsWith(':') ? 2 : 3), 0);
}

const PATTERNS_BY_SPECIFICITY = Object.keys(PAGE_TITLE_KEYS).sort(
  (a, b) => specificity(b) - specificity(a),
);

/** The `pageTitle` key of the most specific pattern that matches, or `null`. */
export function findPageTitleKey(pathname: string): string | null {
  for (const pattern of PATTERNS_BY_SPECIFICITY) {
    if (matchPath({ path: pattern, end: true }, pathname)) {
      return PAGE_TITLE_KEYS[pattern];
    }
  }
  return null;
}
