import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Outlet, useLocation, useNavigate, useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  AppBar,
  Box,
  CircularProgress,
  Divider,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Snackbar,
  Toolbar,
  Tooltip,
  Typography,
} from '@mui/material';
import MoreVertIcon from '@mui/icons-material/MoreVert';
import AccountCircleIcon from '@mui/icons-material/AccountCircle';
import HistoryIcon from '@mui/icons-material/History';
import LanguageIcon from '@mui/icons-material/Language';
import PolicyIcon from '@mui/icons-material/Policy';
import AccessibilityNewIcon from '@mui/icons-material/AccessibilityNew';
import GavelIcon from '@mui/icons-material/Gavel';
import LogoutIcon from '@mui/icons-material/Logout';
import CheckIcon from '@mui/icons-material/Check';
import { getParticipantOverview, getTeam, listFeed } from '../api/runs';
import { getMyRunHistory } from '../api/me';
import type { FeedEvent, FeedEventType, Team } from '../api/types';
import { useWebSocket } from '../hooks/useWebSocket';
import { isCoordinationNotice, peekLastSeen, useUnreadNotices } from '../hooks/useUnreadNotices';
import { useActiveCountdown } from '../hooks/useActiveCountdown';
import { runStateToastKey } from './runStateToast';
import { SUPPORTED_LANGUAGES } from '../i18n/resolveDefaultLanguage';
import { feedMessage } from '../utils/feedMessage';
import { TEAM_DISSOLVED_EVENT } from '../utils/windowEvents';
import { useParticipantRunState } from '../hooks/useParticipantRunState';
import { useInstanceConfig } from '../config/instanceConfig';
import { useAuth } from '../auth/useAuth';
import ParticipantTargets from '../components/ParticipantTargets';
import GuestLogoutDialog from '../components/GuestLogoutDialog';
import OfflineBanner from '../components/OfflineBanner';
import ParticipantProfileSheet from '../components/ParticipantProfileSheet';
import ParticipantBottomNav from '../components/ParticipantBottomNav';
import CountdownTimer from '../components/CountdownTimer';
import {
  BOTTOM_NAV_HEIGHT,
  rememberLastRun,
  type ParticipantTab,
} from '../components/participantNav';
import {
  REMOVED_FROM_RUN_PATH,
  isNotTeamMember,
  isPlayingRole,
} from '../pages/participant/removedFromRun';
import { isLeaveInProgress } from '../pages/participant/leaveInProgress';
import logoSmall from '../assets/logos/logo-small.svg';

const HEADER_HEIGHT = 56;

/**
 * The floor of the shell while a page fills the screen. Below it (a phone held
 * sideways) the shell stops shrinking and the document scrolls again, so the
 * message list keeps a few lines and the input stays reachable.
 */
const FILL_MIN_HEIGHT = 480;

/**
 * Pages that fill the space between the header and the bottom navigation and
 * scroll inside themselves: the team chat. Every other page keeps the
 * scrolling document.
 */
const fillsViewport = (pathname: string): boolean => /\/team\/chat\/?$/.test(pathname);

// The feed carries team activity as well as coordination notices, so the
// countdown or an unseen notice can sit further back than the newest page.
// The shell pages newest-first at the backend's per-call max until both are
// covered. The cap keeps a long, quiet run without a countdown from paging
// its whole history.
const FEED_HISTORY_PAGE_LIMIT = 1000;
const FEED_HISTORY_MAX_PAGES = 5;

/** Payload of the `team_member_moved` broadcast. The move endpoint emits it on
 *  both teams' chat channels and the run channel. */
interface TeamMemberMovedPayload {
  run_id: string;
  member_id: string;
  user_id: string | null;
  display_name: string;
  from_team_id: string;
  to_team_id: string;
}

/** Payload of the `team_dissolved` broadcast on the run channel. It names the
 *  team by id only. */
interface TeamDissolvedPayload {
  run_id: string;
  team_id: string;
}

/** What the shell knows of the caller's membership in this run, handed to the
 *  pages of the team tab: `present` when the stored key names a team,
 *  `unknown` while the server is asked for a run page opened without it,
 *  `none` when the server says there is no membership, `error` when the
 *  request failed (no answer is never "no membership"). */
type MembershipLookup = 'unknown' | 'present' | 'none' | 'error';

const teamKey = (runId: string) => `clavis_team_for_run_${runId}`;

const readStoredTeamId = (runId: string | undefined): string | null => {
  if (!runId) return null;
  try {
    return sessionStorage.getItem(teamKey(runId));
  } catch {
    return null;
  }
};

const RunShell: React.FC = () => {
  const { runId } = useParams<{ runId: string }>();
  const { user, logout } = useAuth();
  const { t, i18n } = useTranslation('runs');
  const { t: tCommon } = useTranslation('common');
  const { brand_name: brand } = useInstanceConfig();
  const location = useLocation();
  const navigate = useNavigate();

  const [toastMessage, setToastMessage] = useState<string | null>(null);
  // A notice pushed by the coordination. Kept apart from the run-state toast so
  // an alert can stay on screen while a state change comes and goes.
  const [notice, setNotice] = useState<{ text: string; type: FeedEventType } | null>(null);
  const [overflowAnchor, setOverflowAnchor] = useState<HTMLElement | null>(null);
  const [languageAnchor, setLanguageAnchor] = useState<HTMLElement | null>(null);
  const [comingSoonOpen, setComingSoonOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  // The text the live region holds. Only a pause that arrives while the frame
  // is open sets it, so a page opened during a pause shows the notice and does
  // not announce it. The frame stays mounted across page changes, so the text
  // stays put and nothing is said again.
  const [pauseAnnouncement, setPauseAnnouncement] = useState('');

  // Read-only run state for a participant. The lifecycle comes from the
  // membership-bound GET /api/public/runs/{id}/state and the guest-authorized
  // run WS, never from a coordinator run endpoint, which would answer 403.
  // It drives the header's run name, the state-change toast and the end-of-run
  // redirect.
  const { run, loading } = useParticipantRunState({
    orgSlug: user?.organization_slug,
    runId,
    onStateChange: (next, previous) => {
      // The lasting notice replaces the pause toast. Both would be two visible
      // messages and two announcements.
      if (next === 'paused') {
        setPauseAnnouncement(t('participant.pausedNotice'));
      } else {
        setPauseAnnouncement('');
        setToastMessage(t(`participant.runStateChange.${runStateToastKey(next, previous)}`));
      }
      if (next === 'ended' && runId) {
        // Give the participant about 2 s to read the toast, then go to feedback.
        window.setTimeout(() => {
          navigate(`/runs/${runId}/participant/feedback`);
        }, 2000);
      }
    },
  });

  // The user's team, handed to the outlet. It loads from the membership id
  // that GuestJoin leaves in sessionStorage. It is declared before the WS
  // subscription because the team_member_moved handler switches it live.
  const [team, setTeam] = useState<Team | null>(null);

  // A run page opened without the key GuestJoin leaves (a bookmark, a reload
  // after the storage was cleared, a second tab) asks the server for the
  // caller's team of this run, as the sign-in landing does. Until it answers,
  // the team tab neither shows a team nor says there is none.
  const [membership, setMembership] = useState<MembershipLookup>(() =>
    readStoredTeamId(runId) ? 'present' : 'unknown',
  );
  // Bumped by the retry of a failed lookup, so the effect below asks again
  // even though nothing else it depends on changed.
  const [lookupAttempt, setLookupAttempt] = useState(0);
  const retryMembership = useCallback(() => {
    setMembership('unknown');
    setLookupAttempt((count) => count + 1);
  }, []);

  // How often the handler below has applied a different team. The
  // outlet is keyed by this counter, not the team id. The key says why.
  const [teamSwitchCount, setTeamSwitchCount] = useState(0);

  // The shared notice registry behind the activity tab's unread badge. The WS
  // handler below records every coordination notice. It calls markSeen only
  // while the activity tab is open. FeedView marks seen in every other case,
  // once its own load lands.
  const { recordNotices, markSeen } = useUnreadNotices(runId);

  // The running countdown, shown in the app bar on every page of the run.
  const countdownTarget = useActiveCountdown(runId);

  // The participant's latest target team, kept apart from what is on screen.
  // A quick move back to the rendered team must win over a slower fetch for an
  // intermediate target. Every team_member_moved for this user sets it at
  // once, and a getTeam answer applies only while it still matches. So a fetch
  // for a superseded target is dropped, even when it resolves after a later
  // move already reverted the target.
  const latestTeamIdRef = useRef<string | null>(null);

  // The id of the team applied to state, set at once wherever setTeam is
  // called (the initial load included). The switch counter rises only when a
  // resolving fetch's team differs from it, so two pending fetches for the
  // same target remount once, not twice.
  const appliedTeamIdRef = useRef<string | null>(null);

  // An announcement, countdown, reminder or alert must reach every
  // participant, not only one on the activity tab. The notices ride the run WS
  // that already carries run_state_change and land on the same surface as
  // "the run was paused".
  useWebSocket({
    path: user?.organization_slug && runId ? `/api/orgs/${user.organization_slug}/runs/${runId}/ws` : '',
    enabled: !!(user?.organization_slug && runId),
    handlers: {
      feed_event: (data) => {
        const event = data as FeedEvent;
        // System entries narrate what the participant just did. As a notice
        // they would be noise on top of the action itself.
        if (!isCoordinationNotice(event)) return;
        // Recorded on every page, so the activity tab can badge it as unread.
        // Watching the tab while the notice arrives counts as seeing it.
        recordNotices([event]);
        if (active === 'feed') markSeen();
        const text = feedMessage(event.content, i18n.language);
        if (text) {
          setNotice({ text, type: event.event_type });
          // An alarm is felt, not only seen: a phone in a pocket shows nothing.
          // Browsers without the API, and iOS, ignore it.
          if (event.event_type === 'alert') {
            try {
              navigator.vibrate?.([300, 150, 300, 150, 300]);
            } catch {
              /* Vibration blocked. The popup still shows. */
            }
          }
        }
      },
      // When *this* participant is moved, the shell follows without a reload.
      // It re-points the stored membership and hands the new team down. Chat,
      // team info and leaderboard read the team from the outlet, not from
      // sessionStorage, so they switch with it.
      team_member_moved: (data) => {
        const event = data as TeamMemberMovedPayload;
        if (!runId || !user?.organization_slug || !user.id) return;
        if (event.user_id !== user.id) return;
        if (event.to_team_id === latestTeamIdRef.current) return;
        latestTeamIdRef.current = event.to_team_id;
        sessionStorage.setItem(`clavis_team_for_run_${runId}`, event.to_team_id);
        // The target is already rendered, as after a move that reverts a
        // pending one. Nothing to fetch.
        if (event.to_team_id === team?.id) return;
        getTeam(user.organization_slug, runId, event.to_team_id)
          .then((nextTeam) => {
            // A later move may have superseded this one. Compare with the
            // latest target, not the rendered team, because a pending fetch
            // can resolve after the target moved on and back again.
            if (latestTeamIdRef.current !== nextTeam.id) return;
            setTeam(nextTeam);
            // Two fetches for the same target must remount once. A second
            // remount would erase what the participant entered after the first.
            if (appliedTeamIdRef.current !== nextTeam.id) {
              appliedTeamIdRef.current = nextTeam.id;
              setTeamSwitchCount((count) => count + 1);
            }
            setToastMessage(t('participant.teamMovedToast', { teamName: nextTeam.name }));
          })
          .catch(() => { /* the current view stays if the fetch fails */ });
      },
      // A team of the run is dissolved. For every other team the standing
      // changed, so the open leaderboard loads again. A member of the dissolved
      // team would stay on a chat whose socket is closed, because the team tab
      // sends no request that names the membership. So the shell asks the
      // server at once. The answer decides, not the event, and it leads out
      // the way a removal does.
      team_dissolved: (data) => {
        const event = data as TeamDissolvedPayload;
        if (!runId || event.run_id !== runId) return;
        // The last member who leaves in this tab dissolves the team and may
        // hear of it before the leave answers. The leave sends them on. This
        // is not a removal.
        if (isLeaveInProgress(event.team_id)) return;
        // Read from the storage, which team_member_moved re-points at once: a
        // last member who was moved hears of the old team after the move.
        const storageKey = `clavis_team_for_run_${runId}`;
        if (event.team_id !== sessionStorage.getItem(storageKey)) {
          window.dispatchEvent(new CustomEvent(TEAM_DISSOLVED_EVENT, { detail: event }));
          return;
        }
        if (!user?.organization_slug || !isPlayingRole(user.role)) return;
        const leave = () => {
          // The answer came late: the leave of this tab may have ended in the
          // meantime and taken the key with it.
          if (isLeaveInProgress(event.team_id)) return;
          if (sessionStorage.getItem(storageKey) !== event.team_id) return;
          sessionStorage.removeItem(storageKey);
          navigate(REMOVED_FROM_RUN_PATH, { replace: true });
        };
        getParticipantOverview(user.organization_slug, runId)
          .then((overview) => {
            if (overview.is_team_member === false) leave();
          })
          .catch((err: unknown) => {
            // Any other failure leaves the page as it is. The next request
            // of a page that names the membership decides.
            if (isNotTeamMember(err)) leave();
          });
      },
    },
  });

  // The initial feed load lives in the shell, which mounts on every run tab.
  // So a reload on Rallye or Team still fills the shared registry, and the
  // badge and the header countdown do not stay empty until the activity tab
  // opens. FeedView loads its own list and seeds the same registry again.
  // recordRunNotices ignores ids it already knows, so nothing counts twice.
  useEffect(() => {
    if (!user?.organization_slug || !runId) return;
    let cancelled = false;
    const orgSlug = user.organization_slug;
    // Read once: this pass needs where the boundary stood when it began.
    const lastSeenBoundary = peekLastSeen(runId);

    (async () => {
      const collected: FeedEvent[] = [];
      let foundCountdown = false;
      let oldestLoadedAt: number | null = null;

      for (let page = 0; page < FEED_HISTORY_MAX_PAGES; page += 1) {
        const resp = await listFeed(orgSlug, runId, {
          skip: page * FEED_HISTORY_PAGE_LIMIT,
          limit: FEED_HISTORY_PAGE_LIMIT,
        });
        if (cancelled) return;
        collected.push(...resp.items);
        if (resp.items.some((ev) => ev.event_type === 'countdown')) foundCountdown = true;
        if (resp.items.length > 0) oldestLoadedAt = Date.parse(resp.items[0].created_at);

        // This page already reaches back past what the participant last saw,
        // so nothing older can still be unseen.
        const pastLastSeenBoundary =
          lastSeenBoundary !== null && oldestLoadedAt !== null && oldestLoadedAt < lastSeenBoundary;
        if (resp.items.length < FEED_HISTORY_PAGE_LIMIT) break; // reached the start of the run
        if (foundCountdown && pastLastSeenBoundary) break;
      }

      recordNotices(collected);
    })().catch(() => { /* the badge/countdown just wait for the next WS event */ });

    return () => { cancelled = true; };
  }, [user?.organization_slug, runId, recordNotices]);

  // The load of the user's team below hands it to the outlet, and the team tab
  // shows the share code. GuestJoin stores the team_id in sessionStorage on a
  // successful join.
  //
  // The team load fails silently. A load that ran only once would leave the
  // team tab without its team after one lost request: no code line, an
  // endless spinner on "Team". So opening the team tab without a team asks
  // again (`teamMissingOnTeamTab` flips), and so does leaving it.
  const teamMissingOnTeamTab = team === null && location.pathname.includes('/team');

  // The key is missing: ask the server which team of this run the caller is
  // in, store the answer under the key the join flow writes, and let the load
  // below fetch the team. A failed request never means "no membership". The
  // membership turns `error`, the pages offer a retry, and the team tab asks
  // again when it opens.
  useEffect(() => {
    if (!runId) return;
    // Nothing to ask when the key is there, or for staff, who have no team
    // (both read at render, below).
    if (readStoredTeamId(runId)) return;
    if (!user?.organization_slug || !isPlayingRole(user.role)) return;
    let cancelled = false;
    getMyRunHistory()
      .then(({ items }) => {
        if (cancelled) return;
        const mine = items.find((item) => item.run_id === runId);
        if (!mine) {
          setMembership('none');
          return;
        }
        try {
          sessionStorage.setItem(teamKey(runId), mine.team_id);
        } catch {
          // sessionStorage disabled: the pages still find the team below
        }
        setMembership('present');
      })
      .catch(() => {
        // No answer is not "no membership": the pages show a failure with a
        // retry, instead of waiting for a lookup that never runs again.
        if (!cancelled) setMembership('error');
      });
    return () => { cancelled = true; };
  }, [user?.organization_slug, user?.role, runId, teamMissingOnTeamTab, lookupAttempt]);

  useEffect(() => {
    if (!user?.organization_slug || !runId) return;
    const teamId = sessionStorage.getItem(`clavis_team_for_run_${runId}`);
    if (!teamId) return;
    // The flag above also flips when the team arrives. Nothing to fetch then.
    if (appliedTeamIdRef.current === teamId) return;
    let cancelled = false;
    getTeam(user.organization_slug, runId, teamId)
      .then((t) => {
        // Skip a fetch that raced a live move: the membership it read is gone.
        if (cancelled || sessionStorage.getItem(`clavis_team_for_run_${runId}`) !== teamId) return;
        appliedTeamIdRef.current = t.id;
        setTeam(t);
      })
      .catch(() => { /* The team tab asks again when it opens. */ });
    return () => { cancelled = true; };
  }, [user?.organization_slug, runId, teamMissingOnTeamTab, membership]);

  // What the pages are told: a key that appeared by another route counts, and
  // staff have no membership to wait for.
  const membershipForPages: MembershipLookup =
    membership !== 'unknown'
      ? membership
      : readStoredTeamId(runId)
        ? 'present'
        : isPlayingRole(user?.role)
          ? 'unknown'
          : 'none';

  // Remember the run so run-less pages (history, legal) can route the bottom
  // nav back into it.
  useEffect(() => {
    if (runId) rememberLastRun(runId);
  }, [runId]);

  const active: ParticipantTab = (() => {
    if (location.pathname.includes('/team')) return 'team';
    if (location.pathname.includes('/feed')) return 'feed';
    return 'rallye';
  })();

  // On a page that fills the screen the shell is a flex column exactly as high
  // as the screen. The header takes its height, the page takes the rest, and
  // the document does not scroll.
  const fill = fillsViewport(location.pathname);

  const openOverflow = (e: React.MouseEvent<HTMLElement>) => setOverflowAnchor(e.currentTarget);
  const closeOverflow = () => setOverflowAnchor(null);
  const closeLanguage = () => setLanguageAnchor(null);

  const handleProfile = () => {
    closeOverflow();
    setProfileOpen(true);
  };

  const handleLanguage = (e: React.MouseEvent<HTMLElement>) => {
    e.stopPropagation();
    setLanguageAnchor(e.currentTarget);
  };

  const handlePickLanguage = (lang: string) => {
    void i18n.changeLanguage(lang);
    closeLanguage();
    closeOverflow();
  };

  const goLegal = (path: string) => () => {
    closeOverflow();
    navigate(path);
  };

  const [confirmGuestLogout, setConfirmGuestLogout] = useState(false);

  const doLogout = async () => {
    setConfirmGuestLogout(false);
    await logout();
    navigate('/login');
  };

  // A guest cannot come back after a sign-out, so a guest is asked.
  const handleLogout = () => {
    closeOverflow();
    if (user?.role === 'guest') setConfirmGuestLogout(true);
    else void doLogout();
  };

  // The header names the run. While it loads or when it is missing, the brand
  // stands in, so the bar never reads as the bottom-nav tab label.
  const headerPrimary = run?.name ?? brand;

  return (
    <ParticipantTargets>
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        minHeight: '100dvh',
        bgcolor: 'background.default',
        ...(fill && { height: '100dvh', minHeight: FILL_MIN_HEIGHT }),
      }}
    >
      <a href="#main-content" className="skip-link">{tCommon('aria.skipToMain')}</a>
      {/* On a page that fills the screen the header is in the flow, so it takes
          its real height (offline banner included) out of the column. Sticky,
          so it stays on top where the height floor lets the document scroll. */}
      <AppBar
        position={fill ? 'sticky' : 'fixed'}
        elevation={1}
        sx={{ top: 0, flexShrink: 0 }}
      >
        <Toolbar
          variant="dense"
          disableGutters
          sx={{
            minHeight: HEADER_HEIGHT,
            px: 2,
            gap: 1.25,
            maxWidth: 720,
            width: '100%',
            mx: 'auto',
          }}
        >
          <Box
            component="img"
            src={logoSmall}
            alt={brand}
            sx={{ width: 28, height: 24, flexShrink: 0 }}
          />
          <Typography
            variant="subtitle1"
            component="div"
            noWrap
            sx={{ flex: 1, minWidth: 0, lineHeight: 1.2, fontWeight: 600 }}
          >
            {headerPrimary}
          </Typography>
          {/* The countdown shows on every page until its target time passes
              or the run ends. After the end, a countdown to a meeting point
              means nothing, and it would keep ticking on the feedback page. */}
          {countdownTarget && run?.state !== 'ended' && (
            <Box sx={{ flexShrink: 0 }}>
              {/* The header countdown is the one that speaks. */}
              <CountdownTimer targetTime={countdownTarget} announce />
            </Box>
          )}
          {/* The header leaves out the team's share code on purpose. The Team
              tab shows it. */}
          <Tooltip title={t('participant.overflow.menuLabel')}>
            {/* size="large" sizes the touch target, not the icon: its 12 px
                padding makes a 48 px box, and MoreVertIcon keeps 24 px. */}
            <IconButton
              size="large"
              edge="end"
              color="inherit"
              aria-label={t('participant.overflow.menuLabel')}
              aria-controls="run-overflow-menu"
              aria-haspopup="true"
              onClick={openOverflow}
            >
              <MoreVertIcon />
            </IconButton>
          </Tooltip>
        </Toolbar>
        <OfflineBanner />
      </AppBar>

      <Menu
        id="run-overflow-menu"
        anchorEl={overflowAnchor}
        open={Boolean(overflowAnchor)}
        onClose={closeOverflow}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <MenuItem onClick={handleProfile}>
          <ListItemIcon><AccountCircleIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('participant.overflow.profile')}</ListItemText>
        </MenuItem>
        <MenuItem onClick={() => { closeOverflow(); navigate('/run-history'); }}>
          <ListItemIcon><HistoryIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('participant.overflow.runHistory')}</ListItemText>
        </MenuItem>
        <MenuItem onClick={handleLanguage}>
          <ListItemIcon><LanguageIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('participant.overflow.language')}</ListItemText>
        </MenuItem>
        <Divider />
        <MenuItem onClick={goLegal('/legal/privacy')}>
          <ListItemIcon><PolicyIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('participant.overflow.dataPrivacy')}</ListItemText>
        </MenuItem>
        <MenuItem onClick={goLegal('/legal/accessibility')}>
          <ListItemIcon><AccessibilityNewIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('participant.overflow.accessibility')}</ListItemText>
        </MenuItem>
        <MenuItem onClick={goLegal('/legal/impressum')}>
          <ListItemIcon><GavelIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('participant.overflow.impressum')}</ListItemText>
        </MenuItem>
        <Divider />
        <MenuItem onClick={handleLogout}>
          <ListItemIcon><LogoutIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('participant.overflow.logout')}</ListItemText>
        </MenuItem>
      </Menu>
      <GuestLogoutDialog
        open={confirmGuestLogout}
        onConfirm={() => void doLogout()}
        onCancel={() => setConfirmGuestLogout(false)}
      />

      <Menu
        anchorEl={languageAnchor}
        open={Boolean(languageAnchor)}
        onClose={closeLanguage}
        anchorOrigin={{ vertical: 'top', horizontal: 'left' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        {SUPPORTED_LANGUAGES.map((lang) => {
          const active = i18n.language === lang || i18n.language?.startsWith(`${lang}-`);
          return (
            <MenuItem key={lang} onClick={() => handlePickLanguage(lang)} selected={active}>
              <ListItemIcon sx={{ minWidth: 32 }}>
                {active ? <CheckIcon fontSize="small" /> : null}
              </ListItemIcon>
              <ListItemText
                primary={tCommon(`language.names.${lang}`)}
                secondary={tCommon(`language.${lang}`)}
              />
            </MenuItem>
          );
        })}
      </Menu>

      {/* tabindex -1 lets the skip link hand the focus here. It draws no
          outline of its own (index.css). */}
      <Box
        component="main"
        id="main-content"
        tabIndex={-1}
        sx={{
          flex: 1,
          pt: fill ? '8px' : `${HEADER_HEIGHT + 8}px`,
          pb: `${BOTTOM_NAV_HEIGHT + 8}px`,
          px: 2,
          maxWidth: 720,
          mx: 'auto',
          width: '100%',
          // The page below becomes the flex child that takes what is left
          // and may shrink (min-height 0), so its own list scrolls.
          ...(fill && { display: 'flex', flexDirection: 'column', minHeight: 0 }),
        }}
      >
        {/* Read from the run state the frame already has, so a page opened
            during a pause shows it at once. Plain text: the live region below
            speaks, once, when the pause starts. In the flow and not shrinking,
            so a page that fills the screen gives up the height and its input
            line stays above the bottom navigation. */}
        {run?.state === 'paused' && (
          <Alert
            role="note"
            severity="warning"
            sx={{ flexShrink: 0, mb: 1, minWidth: 0, overflowWrap: 'anywhere' }}
          >
            {t('participant.pausedNotice')}
          </Alert>
        )}
        {loading && !run ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 200 }}>
            <CircularProgress aria-label={tCommon('loading')} />
          </Box>
        ) : (
          // Keyed by the switch counter, not the team id. The id is undefined
          // until getTeam resolves, so an id key would remount every page on
          // the initial load, closing an open dialog and wiping a half-typed
          // chat message. The counter rises only when a move applies a new
          // team. A real switch must remount every page below, because a page
          // would otherwise keep the old team until its own refresh
          // (StationList's progress, for one).
          <Outlet key={teamSwitchCount} context={{ run, team, membership: membershipForPages, retryMembership }} />
        )}
      </Box>

      {/* Always mounted and empty until a pause arrives live, so that
          the text is a change in a region the reader already knows. */}
      <Box
        aria-live="polite"
        aria-atomic="true"
        sx={{
          position: 'absolute',
          width: 1,
          height: 1,
          overflow: 'hidden',
          clipPath: 'inset(50%)',
          whiteSpace: 'nowrap',
        }}
      >
        {pauseAnnouncement}
      </Box>

      <ParticipantBottomNav runId={runId ?? null} active={active} />

      <ParticipantProfileSheet
        open={profileOpen}
        onClose={() => setProfileOpen(false)}
      />

      <Snackbar
        open={!!notice}
        // An alert waits to be acknowledged. The softer kinds fade on their own.
        autoHideDuration={notice?.type === 'alert' ? null : 8000}
        // A tap elsewhere must not dismiss it. MUI reports that as
        // "clickaway", and it would close an alarm the moment a participant
        // touched the page.
        onClose={(_, reason) => {
          if (reason !== 'clickaway') setNotice(null);
        }}
        anchorOrigin={{ vertical: 'top', horizontal: 'center' }}
        sx={{ top: { xs: `${HEADER_HEIGHT + 8}px`, sm: `${HEADER_HEIGHT + 8}px` } }}
      >
        <Alert
          onClose={() => setNotice(null)}
          severity={notice?.type === 'alert' ? 'error' : 'info'}
          variant="filled"
        >
          {notice?.text}
        </Alert>
      </Snackbar>

      <Snackbar
        open={!!toastMessage && !notice}
        // Eight seconds, and no dismissal by a tap elsewhere. Otherwise a toast
        // such as "Du wurdest in das Team … verschoben." would be gone before
        // a participant on a phone sees it.
        autoHideDuration={8000}
        onClose={(_, reason) => {
          if (reason !== 'clickaway') setToastMessage(null);
        }}
        anchorOrigin={{ vertical: 'top', horizontal: 'center' }}
        sx={{ top: { xs: `${HEADER_HEIGHT + 8}px`, sm: `${HEADER_HEIGHT + 8}px` } }}
      >
        <Alert
          onClose={() => setToastMessage(null)}
          severity={run?.state === 'ended' ? 'warning' : 'info'}
          variant="filled"
        >
          {toastMessage}
        </Alert>
      </Snackbar>

      <Snackbar
        open={comingSoonOpen}
        autoHideDuration={3000}
        onClose={() => setComingSoonOpen(false)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
        sx={{ bottom: `${BOTTOM_NAV_HEIGHT + 16}px` }}
      >
        <Alert severity="info" variant="filled" onClose={() => setComingSoonOpen(false)}>
          {t('participant.overflow.comingSoon')}
        </Alert>
      </Snackbar>
    </Box>
    </ParticipantTargets>
  );
};

export default RunShell;
