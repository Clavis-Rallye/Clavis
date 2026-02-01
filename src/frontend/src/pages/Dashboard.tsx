import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Grid,
  Link,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  Stack,
  Typography,
} from '@mui/material';
import { useAuth } from '../auth/useAuth';
import { listRallyes } from '../api/content';
import { listRuns, listSubmissions, listScores } from '../api/runs';
import { getTakeoverNotice, listUsers, listLogs } from '../api/admin';
import type { LogEntry, Run, RunState } from '../api/types';
import { formatDateTime } from '../utils/formatDate';

const STATE_COLORS: Record<
  RunState,
  'default' | 'primary' | 'success' | 'warning' | 'error'
> = {
  configured: 'default',
  scheduled: 'primary',
  active: 'success',
  paused: 'warning',
  ended: 'error',
};

interface OrgAdminData {
  totalUsers: number;
  totalRallyes: number;
  totalRuns: number;
  activeRuns: Run[];
  recentLogs: LogEntry[];
}

interface CoordinatorData {
  activeRuns: Run[];
  unscoredCount: number;
}

const OrgAdminPanels: React.FC<{ orgSlug: string }> = ({ orgSlug }) => {
  const { t, i18n } = useTranslation('auth');
  const { t: tAdmin } = useTranslation('admin');
  const navigate = useNavigate();
  const [data, setData] = useState<OrgAdminData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!orgSlug) return;
    let cancelled = false;
    Promise.allSettled([
      listUsers(orgSlug, { limit: 1 }),
      listRallyes(orgSlug),
      listRuns(orgSlug),
      listLogs(orgSlug, { category: ['audit'], limit: 5 }),
    ])
      .then(([usersRes, rallyesRes, runsRes, logsRes]) => {
        if (cancelled) return;
        const totalUsers = usersRes.status === 'fulfilled' ? usersRes.value.total : 0;
        const totalRallyes =
          rallyesRes.status === 'fulfilled' ? rallyesRes.value.total : 0;
        const allRuns = runsRes.status === 'fulfilled' ? runsRes.value.items : [];
        const totalRuns = runsRes.status === 'fulfilled' ? runsRes.value.total : 0;
        const recentLogs = logsRes.status === 'fulfilled' ? logsRes.value.items : [];
        setData({
          totalUsers,
          totalRallyes,
          totalRuns,
          activeRuns: allRuns.filter((r) => r.state === 'active' || r.state === 'paused'),
          recentLogs,
        });
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [orgSlug]);

  if (error) return <Alert severity="warning">{t('dashboard.coordinator.loadFailed')}</Alert>;
  if (!data) return null;

  return (
    <Stack spacing={3} sx={{ mt: 2 }}>
      <Grid container spacing={2}>
        <Grid size={{ xs: 12, sm: 4 }} sx={{ display: 'flex' }}>
          <Card sx={{ flex: 1 }}>
            <CardContent>
              <Typography variant="overline" sx={{
                color: "text.secondary"
              }}>
                {t('dashboard.stats.users')}
              </Typography>
              <Typography variant="h3" component="p">{data.totalUsers}</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid size={{ xs: 12, sm: 4 }} sx={{ display: 'flex' }}>
          <Card sx={{ flex: 1 }}>
            <CardContent>
              <Typography variant="overline" sx={{
                color: "text.secondary"
              }}>
                {t('dashboard.stats.rallyes')}
              </Typography>
              <Typography variant="h3" component="p">{data.totalRallyes}</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid size={{ xs: 12, sm: 4 }} sx={{ display: 'flex' }}>
          <Card sx={{ flex: 1 }}>
            <CardContent>
              <Typography variant="overline" sx={{
                color: "text.secondary"
              }}>
                {t('dashboard.stats.runs')}
              </Typography>
              <Typography variant="h3" component="p">{data.totalRuns}</Typography>
              <Typography variant="caption" sx={{
                color: "text.secondary"
              }}>
                {t('dashboard.stats.active', { count: data.activeRuns.length })}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>
      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Typography variant="h6" component="h2" gutterBottom>
                {t('dashboard.activeRuns.title')}
              </Typography>
              {data.activeRuns.length === 0 ? (
                <Typography variant="body2" sx={{
                  color: "text.secondary"
                }}>
                  {t('dashboard.activeRuns.empty')}
                </Typography>
              ) : (
                <List dense disablePadding>
                  {data.activeRuns.map((run) => (
                    <ListItem key={run.id} disablePadding>
                      <ListItemButton onClick={() => navigate(`/runs/${run.id}`)}>
                        <ListItemText
                          primary={
                            <Stack direction="row" spacing={1} sx={{
                              alignItems: "center"
                            }}>
                              <Typography variant="body2">{run.name}</Typography>
                              <Chip
                                label={t(`runs:dashboard.state.${run.state}`)}
                                size="small"
                                color={STATE_COLORS[run.state]}
                              />
                            </Stack>
                          }
                        />
                      </ListItemButton>
                    </ListItem>
                  ))}
                </List>
              )}
            </CardContent>
          </Card>
        </Grid>

        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Stack
                direction="row"
                sx={{
                  justifyContent: "space-between",
                  alignItems: "center"
                }}>
                <Typography variant="h6" component="h2">{t('dashboard.auditRecent.title')}</Typography>
                <Link
                  component="button"
                  variant="caption"
                  onClick={() => navigate('/admin/logs')}
                >
                  {t('dashboard.auditRecent.viewAll')}
                </Link>
              </Stack>
              {data.recentLogs.length === 0 ? (
                <Typography variant="body2" sx={{
                  color: "text.secondary"
                }}>
                  {t('dashboard.auditRecent.empty')}
                </Typography>
              ) : (
                <List dense disablePadding>
                  {data.recentLogs.map((entry, idx) => (
                    <ListItem key={`${entry.timestamp ?? idx}-${idx}`} disablePadding>
                      <ListItemText
                        primary={
                          entry.event ? (
                            /* The raw key ("user_login") is the
                               backend's name for the event, not a label an admin
                               can read. Show the German/English label and keep the
                               key in the tooltip, because the log filter and every
                               support question still speak in raw keys. An event we
                               have no label for falls back to its key. */
                            <Box component="span" title={entry.event}>
                              {tAdmin(`dashboard.auditEvents.${entry.event}`, {
                                defaultValue: entry.event,
                              })}
                            </Box>
                          ) : (
                            '—'
                          )
                        }
                        secondary={
                          entry.timestamp ? formatDateTime(entry.timestamp, i18n.language) : ''
                        }
                      />
                    </ListItem>
                  ))}
                </List>
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </Stack>
  );
};

const CoordinatorPanels: React.FC<{ orgSlug: string }> = ({ orgSlug }) => {
  const { t } = useTranslation('auth');
  const navigate = useNavigate();
  const [data, setData] = useState<CoordinatorData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!orgSlug) return;
    let cancelled = false;
    (async () => {
      try {
        const runsRes = await listRuns(orgSlug);
        const activeRuns = runsRes.items.filter(
          (r) => r.state === 'active' || r.state === 'paused',
        );
        const unscoredPerRun = await Promise.all(
          activeRuns.map(async (r) => {
            const [subs, scores] = await Promise.all([
              listSubmissions(orgSlug, r.id),
              listScores(orgSlug, r.id),
            ]);
            const scored = new Set(scores.items.map((s) => s.submission_id));
            return subs.items.filter((s) => !scored.has(s.id)).length;
          }),
        );
        if (cancelled) return;
        setData({
          activeRuns,
          unscoredCount: unscoredPerRun.reduce((a, b) => a + b, 0),
        });
      } catch (err) {
        if (!cancelled) setError(String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [orgSlug]);

  if (error) return <Alert severity="warning">{t('dashboard.coordinator.loadFailed')}</Alert>;
  if (!data) return null;

  return (
    <Grid container spacing={2} sx={{ mt: 2 }}>
      <Grid size={{ xs: 12, md: 8 }}>
        <Card>
          <CardContent>
            <Typography variant="h6" component="h2" gutterBottom>
              {t('dashboard.activeRuns.title')}
            </Typography>
            {data.activeRuns.length === 0 ? (
              <Typography variant="body2" sx={{
                color: "text.secondary"
              }}>
                {t('dashboard.activeRuns.empty')}
              </Typography>
            ) : (
              <List dense disablePadding>
                {data.activeRuns.map((run) => (
                  <ListItem key={run.id} disablePadding>
                    <ListItemButton onClick={() => navigate(`/runs/${run.id}`)}>
                      <ListItemText
                        primary={
                          <Stack direction="row" spacing={1} sx={{
                            alignItems: "center"
                          }}>
                            <Typography variant="body2">{run.name}</Typography>
                            <Chip
                              label={t(`runs:dashboard.state.${run.state}`)}
                              size="small"
                              color={STATE_COLORS[run.state]}
                            />
                          </Stack>
                        }
                      />
                    </ListItemButton>
                  </ListItem>
                ))}
              </List>
            )}
          </CardContent>
        </Card>
      </Grid>
      <Grid size={{ xs: 12, md: 4 }}>
        <Card>
          <CardContent>
            <Typography variant="overline" sx={{
              color: "text.secondary"
            }}>
              {t('dashboard.coordinator.needsAttention')}
            </Typography>
            <Typography variant="h3" component="p">{data.unscoredCount}</Typography>
            <Typography variant="caption" sx={{
              color: "text.secondary"
            }}>
              {data.unscoredCount === 0
                ? t('dashboard.coordinator.noUnscored')
                : t('dashboard.coordinator.unscoredCount', { count: data.unscoredCount })}
            </Typography>
          </CardContent>
        </Card>
      </Grid>
    </Grid>
  );
};

const DefaultPanels: React.FC = () => {
  const { t } = useTranslation('auth');
  return (
    <Grid container spacing={3} sx={{ mt: 2 }}>
      <Grid size={{ xs: 12, md: 6, lg: 4 }}>
        <Card>
          <CardContent>
            <Typography variant="h5" component="h2" gutterBottom>
              {t('dashboard.rallyes.title')}
            </Typography>
            <Typography variant="body2" sx={{
              color: "text.secondary"
            }}>
              {t('dashboard.rallyes.empty')}
            </Typography>
          </CardContent>
        </Card>
      </Grid>
      <Grid size={{ xs: 12, md: 6, lg: 4 }}>
        <Card>
          <CardContent>
            <Typography variant="h5" component="h2" gutterBottom>
              {t('dashboard.runs.title')}
            </Typography>
            <Typography variant="body2" sx={{
              color: "text.secondary"
            }}>
              {t('dashboard.runs.empty')}
            </Typography>
          </CardContent>
        </Card>
      </Grid>
      <Grid size={{ xs: 12, md: 6, lg: 4 }}>
        <Card>
          <CardContent>
            <Typography variant="h5" component="h2" gutterBottom>
              {t('dashboard.teams.title')}
            </Typography>
            <Typography variant="body2" sx={{
              color: "text.secondary"
            }}>
              {t('dashboard.teams.empty')}
            </Typography>
          </CardContent>
        </Card>
      </Grid>
    </Grid>
  );
};

const ROLE_ORDER: Record<string, number> = {
  guest: 0,
  participant: 1,
  coordinator: 2,
  creator: 3,
  org_admin: 4,
};

/**
 * Remembers, per browser, which takeovers the dashboard has already announced.
 * One entry per organization, account and moment of the acceptance: several
 * admins may share a browser, and what one has seen says nothing about another.
 */
const TAKEOVER_SEEN_PREFIX = 'clavis_takeover_notice_seen';

/**
 * The notice of a takeover, shown here once: the dashboard is the first page
 * after the sign-in, and the people page (where the notice stays for its 30
 * days) is one the new admin may not open for a while.
 *
 * Same source as the people page, `GET …/invitations/takeover`. "Once" is kept
 * in localStorage under the organization, the account and the moment of the
 * acceptance, so a later takeover by the same person is announced again and a
 * second admin in the same browser does not reset the first. Without storage the
 * notice simply shows on every visit, which is the safer failure.
 */
function useTakeoverNoticeOnce(orgSlug: string, userId: string, enabled: boolean) {
  const [names, setNames] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || !orgSlug) return;
    let cancelled = false;
    getTakeoverNotice(orgSlug)
      .then((notice) => {
        if (cancelled || !notice?.replaced?.length) return;
        const key = [TAKEOVER_SEEN_PREFIX, orgSlug, userId, notice.accepted_at ?? ''].join(':');
        try {
          if (window.localStorage.getItem(key) !== null) return;
          window.localStorage.setItem(key, '1');
        } catch {
          // Storage is blocked: show the notice rather than lose it.
        }
        setNames(notice.replaced.map((a) => `${a.display_name} (${a.email})`).join(', '));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [orgSlug, userId, enabled]);

  return [names, () => setNames(null)] as const;
}

const Dashboard: React.FC = () => {
  const { user } = useAuth();
  const { t } = useTranslation('auth');
  const navigate = useNavigate();
  const orgSlug = user?.organization_slug ?? '';
  const isCoordinatorOrAbove = !!user && ROLE_ORDER[user.role] >= ROLE_ORDER.coordinator;
  const { t: tAdmin } = useTranslation('admin');
  const [takeoverNames, dismissTakeover] = useTakeoverNoticeOnce(
    orgSlug,
    user?.id ?? '',
    user?.role === 'org_admin',
  );

  const panels = useMemo(() => {
    if (!user || !orgSlug) return <DefaultPanels />;
    if (user.role === 'org_admin') return <OrgAdminPanels orgSlug={orgSlug} />;
    if (user.role === 'coordinator') return <CoordinatorPanels orgSlug={orgSlug} />;
    return <DefaultPanels />;
  }, [user, orgSlug]);

  return (
    <Box>
      <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <Box>
          <Typography variant="h2" component="h1" gutterBottom>
            {t('dashboard.title')}
          </Typography>
          <Typography variant="body1" component="p" sx={{
            color: "text.secondary",
            mb: 2,
          }}>
            {t('dashboard.welcome', { name: user?.display_name })}
          </Typography>
        </Box>
        {isCoordinatorOrAbove && (
          <Button variant="contained" onClick={() => navigate('/runs')}>
            {t('dashboard.goToRuns')}
          </Button>
        )}
      </Stack>
      {takeoverNames && (
        <Alert severity="info" onClose={dismissTakeover} sx={{ mb: 2 }}>
          {tAdmin('users.takeover', { names: takeoverNames })}
        </Alert>
      )}
      {panels}
    </Box>
  );
};

export default Dashboard;
