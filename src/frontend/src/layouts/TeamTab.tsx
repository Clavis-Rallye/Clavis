import React, { useCallback, useEffect, useState } from 'react';
import {
  Outlet,
  useLocation,
  useNavigate,
  useOutletContext,
  useParams,
} from 'react-router';
import { useTranslation } from 'react-i18next';
import { Box, Button, IconButton, Snackbar, Stack, Tab, Tabs, Tooltip, Typography } from '@mui/material';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';

import TeamShareCodeChip from '../components/TeamShareCodeChip';
import type { Team } from '../api/types';
import type { PublicRunState } from '../services/publicRuns';

type TeamSubTab = 'leaderboard' | 'chat' | 'info';

interface RunShellOutletContext {
  team: Team | null;
  run: PublicRunState | null;
  /** The shell's answer on the caller's membership, passed on to the pages. */
  membership?: 'unknown' | 'present' | 'none' | 'error';
  /** Asks the server for the membership again after a failed lookup. */
  retryMembership?: () => void;
}

/**
 * The share code is the first thing a founder needs and dead weight afterwards.
 * Once the code has done its job the panel shrinks to a single line, and the
 * marker that it did survives a remount within the session.
 */
const copiedStorageKey = (teamId: string) => `clavis_team_code_copied_${teamId}`;

const TeamTab: React.FC = () => {
  const { runId } = useParams<{ runId: string }>();
  const { t } = useTranslation('runs');
  const location = useLocation();
  const navigate = useNavigate();
  // `run` is forwarded, not just consumed here: the chat below needs the run
  // state to know whether sending is still possible.
  const { team, run, membership, retryMembership } = useOutletContext<RunShellOutletContext>();

  const last = location.pathname.split('/').filter(Boolean).pop() ?? '';
  const active: TeamSubTab =
    last === 'chat' ? 'chat' : last === 'info' ? 'info' : 'leaderboard';

  const teamId = team?.id ?? null;
  const [codeCopied, setCodeCopied] = useState(false);
  const [snackOpen, setSnackOpen] = useState(false);

  useEffect(() => {
    if (!teamId) {
      setCodeCopied(false);
      return;
    }
    try {
      setCodeCopied(sessionStorage.getItem(copiedStorageKey(teamId)) === '1');
    } catch {
      /* storage blocked — the panel then simply stays expanded */
    }
  }, [teamId]);

  const markCopied = useCallback(() => {
    if (teamId) {
      try {
        sessionStorage.setItem(copiedStorageKey(teamId), '1');
      } catch {
        /* storage blocked — collapse still holds for this mount */
      }
    }
    setCodeCopied(true);
  }, [teamId]);

  const handleChange = (_: React.SyntheticEvent, next: TeamSubTab) => {
    navigate(`/runs/${runId}/participant/team/${next}`);
  };

  const copyFromLine = async () => {
    try {
      await navigator.clipboard.writeText(team?.share_code ?? '');
      setSnackOpen(true);
    } catch {
      /* older browsers without the clipboard API — the code stays readable */
    }
    markCopied();
  };

  // TeamShareCodeChip owns the clipboard call of the expanded panel. The panel
  // only observes that one of its buttons was used, which is what collapses it.
  const handleExpandedClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if ((event.target as HTMLElement).closest('button')) markCopied();
  };

  // The chat needs every line of a phone screen for its messages and its
  // input, so there the code is always the one line, and that line never
  // wraps: an icon button copies the code in one tap.
  const onChat = active === 'chat';
  const collapsed = onChat || (team?.member_count ?? 0) > 1 || codeCopied;

  // On the chat tab this layout is one link of the flex column
  // that runs from the shell's header to the bottom navigation: the code line
  // and the tabs take the height they have, the panel takes the rest and may
  // shrink, so the message list scrolls inside it. The other tabs keep the
  // plain block flow of a scrolling page.
  const fillSx = onChat
    ? ({ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' } as const)
    : undefined;

  return (
    <Box sx={fillSx}>
      {/* After the end nobody can join, so there is no code to share. */}
      {team?.share_code &&
        run?.state !== 'ended' &&
        (collapsed ? (
          <Stack
            direction="row"
            spacing={1}
            data-testid="team-code-line"
            sx={{
              mb: onChat ? 0.5 : 2,
              alignItems: 'center',
              flexWrap: onChat ? 'nowrap' : 'wrap',
              flexShrink: 0,
            }}
          >
            <Typography variant="body2" color="text.secondary">
              {t('team.shareCode.label')}
            </Typography>
            <Typography
              component="span"
              sx={{
                fontFamily: 'monospace',
                fontWeight: 700,
                letterSpacing: 2,
                color: 'primary.main',
              }}
            >
              {team.share_code}
            </Typography>
            {/* Same 44 px touch target as the expanded panel's copy button. On
                the chat tab the words would wrap the line at 320 px, so the
                button is its icon and keeps the words as its name. */}
            {onChat ? (
              <Tooltip title={t('team.shareCode.copy')}>
                <IconButton
                  color="primary"
                  aria-label={t('team.shareCode.copy')}
                  sx={{ minWidth: 44, minHeight: 44, ml: 'auto' }}
                  onClick={() => void copyFromLine()}
                >
                  <ContentCopyIcon />
                </IconButton>
              </Tooltip>
            ) : (
              <Button
                variant="text"
                size="medium"
                sx={{ minHeight: 44, ml: 'auto' }}
                startIcon={<ContentCopyIcon />}
                onClick={() => void copyFromLine()}
              >
                {t('team.shareCode.copy')}
              </Button>
            )}
            <Snackbar
              open={snackOpen}
              autoHideDuration={2000}
              onClose={() => setSnackOpen(false)}
              message={t('team.shareCode.copied')}
            />
          </Stack>
        ) : (
          <Stack spacing={1} sx={{ mb: 2 }} onClick={handleExpandedClick}>
            <Typography variant="body2" color="text.secondary">
              {t('team.shareCode.intro')}
            </Typography>
            <TeamShareCodeChip code={team.share_code} variant="full" />
          </Stack>
        ))}
      <Tabs
        value={active}
        onChange={handleChange}
        variant="fullWidth"
        sx={{
          mb: onChat ? 1 : 2,
          borderBottom: 1,
          borderColor: 'divider',
          flexShrink: 0,
        }}
      >
        <Tab
          value="leaderboard"
          id="team-subtab-leaderboard"
          aria-controls="team-subtab-panel"
          label={t('participant.nav.leaderboard')}
        />
        <Tab
          value="chat"
          id="team-subtab-chat"
          aria-controls="team-subtab-panel"
          label={t('participant.nav.chat')}
        />
        <Tab
          value="info"
          id="team-subtab-info"
          aria-controls="team-subtab-panel"
          label={t('team.info.title')}
        />
      </Tabs>
      {/* The active tab is the name of what follows, so the views below carry no
          heading that repeats it. */}
      <Box
        role="tabpanel"
        id="team-subtab-panel"
        aria-labelledby={`team-subtab-${active}`}
        sx={fillSx}
      >
        <Outlet context={{ team, run, membership, retryMembership }} />
      </Box>
    </Box>
  );
};

export default TeamTab;
