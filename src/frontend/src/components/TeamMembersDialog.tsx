import React, { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { memberDisplayName } from '../utils/memberName';
import {
  Alert,
  Avatar,
  Box,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  IconButton,
  List,
  ListItem,
  ListItemAvatar,
  ListItemButton,
  ListItemText,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import PersonIcon from '@mui/icons-material/Person';
import PersonRemoveIcon from '@mui/icons-material/PersonRemove';
import SwapHorizIcon from '@mui/icons-material/SwapHoriz';
import {
  getRun,
  getTeam,
  listTeams,
  moveTeamMember,
  removeTeam,
  removeTeamMember,
  teamWouldDissolve,
} from '../api/runs';
import { ApiError } from '../api/client';
import type { Team, TeamWouldDissolve } from '../api/types';
import ErrorAlert from './ErrorAlert';
import PersonAddAlt1Icon from '@mui/icons-material/PersonAddAlt1';
import TeamShareCodeChip from './TeamShareCodeChip';
import QrCodeDisplay from './qr/QrCodeDisplay';

interface TeamMemberDTO {
  id: string;
  display_name: string;
  is_guest: boolean;
}

interface TeamWithMembers extends Team {
  members?: TeamMemberDTO[];
}

interface TeamMembersDialogProps {
  open: boolean;
  onClose: () => void;
  orgSlug: string;
  runId: string;
  teamId: string | null;
  teamName: string;
  /** The run's join code, the first half of a team's invite link. */
  rallyeCode: string;
  /** Called after a successful removal or move so the caller can refresh team counts. */
  onMemberRemoved: () => void;
  /**
   * Changes whenever the run reports a change of team members. The open dialog
   * then loads its list again, as the team rows behind it do.
   */
  reloadSignal?: number;
  /**
   * Called when the team itself is gone: removed as a whole, or dissolved
   * with its last member. The caller closes the dialog and loads its teams.
   */
  onTeamRemoved?: () => void;
}

/**
 * The busy look of a confirm button that keeps the focus while its request runs
 * (aria-disabled, never the disabled attribute: a disabled button loses the focus).
 */
const busySx = { '&[aria-disabled="true"]': { opacity: 0.6, cursor: 'default' } };

/**
 * The words for a refused call. TEAM_BUSY means the team changed under the
 * call until the server gave up, so the person is asked to try again.
 */
const errorText = (err: unknown, busy: string): string => {
  if (!(err instanceof ApiError)) return String(err);
  return err.errorCode === 'TEAM_BUSY' ? busy : err.detail;
};

/**
 * A step of the dialog. Each one can dissolve the team, and only the server
 * says whether it does: the page decides nothing from what it has read.
 */
type Act =
  | { kind: 'remove'; member: TeamMemberDTO }
  | { kind: 'move'; member: TeamMemberDTO; targetTeamId: string }
  | { kind: 'team' };

/** A step the server refused with TEAM_WOULD_DISSOLVE, and the facts it sent. */
interface RefusedStep {
  act: Act;
  refusal: TeamWouldDissolve;
}

/** One selectable row of the move picker: a team of this run minus the source. */
interface MoveTarget {
  id: string;
  name: string;
  free: number;
  max: number;
}

interface MoveMemberPickerProps {
  orgSlug: string;
  runId: string;
  teamId: string;
  member: TeamMemberDTO;
  onMoved: () => void;
  /** The server refused the move to this team because it would dissolve the source. */
  onRefused: (targetTeamId: string, refusal: TeamWouldDissolve) => void;
}

/**
 * The run's other teams with their free places. Picking a row sends the move.
 * A full team is shown but disabled, so the server's TEAM_FULL refusal is a
 * guard and not the only signal. The limit comes from the run settings, with
 * the backend's default of 10.
 */
const MoveMemberPicker: React.FC<MoveMemberPickerProps> = ({
  orgSlug,
  runId,
  teamId,
  member,
  onMoved,
  onRefused,
}) => {
  const { t } = useTranslation('runs');
  const [targets, setTargets] = useState<MoveTarget[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [movingTo, setMovingTo] = useState<string | null>(null);
  // The button that opened the picker is gone, so the focus moves to its text.
  const titleRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    titleRef.current?.focus();
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listTeams(orgSlug, runId), getRun(orgSlug, runId)])
      .then(([teamsPage, run]) => {
        if (cancelled) return;
        const raw = run.settings.max_members_per_team;
        const max = typeof raw === 'number' ? raw : 10;
        setTargets(
          teamsPage.items
            .filter((t) => t.id !== teamId)
            .map((t) => ({ id: t.id, name: t.name, free: Math.max(max - t.member_count, 0), max })),
        );
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.detail : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [orgSlug, runId, teamId]);

  const move = async (targetTeamId: string) => {
    setMovingTo(targetTeamId);
    setError(null);
    try {
      // Plain, without the confirmation of a dissolution: the server refuses
      // the move if it would dissolve the team, and the dialog asks then.
      await moveTeamMember(orgSlug, runId, teamId, member.id, targetTeamId, false);
      onMoved();
    } catch (err) {
      const refusal = teamWouldDissolve(err);
      if (refusal) {
        onRefused(targetTeamId, refusal);
        return;
      }
      setError(errorText(err, t('teamMembers.removeTeam.busy')));
      setMovingTo(null);
    }
  };

  return (
    <>
      <DialogContentText ref={titleRef} tabIndex={-1} sx={{ mb: 1, outline: 'none' }}>
        {t('teamMembers.move.pickerTitle', { name: member.display_name })}
      </DialogContentText>
      {error && <ErrorAlert message={error} />}
      {targets === null && !error ? (
        <CircularProgress size={24} />
      ) : targets && targets.length === 0 ? (
        <Typography color="text.secondary">{t('teamMembers.move.noTeams')}</Typography>
      ) : (
        <List dense disablePadding>
          {targets?.map((target) => {
            const full = target.free <= 0;
            return (
              <ListItem key={target.id} disablePadding>
                <ListItemButton
                  disabled={full || movingTo !== null}
                  onClick={() => void move(target.id)}
                >
                  <ListItemText
                    primary={target.name}
                    secondary={
                      full
                        ? t('teamMembers.move.full')
                        : t('teamMembers.move.freePlaces', { free: target.free, max: target.max })
                    }
                  />
                  {movingTo === target.id && <CircularProgress size={18} />}
                </ListItemButton>
              </ListItem>
            );
          })}
        </List>
      )}
    </>
  );
};

/**
 * Staff view of a team's member list. A member can be removed, after a
 * question in place, or moved to another team of the run. The backend keeps a
 * removed guest from joining the run again with a departure record. A move
 * writes no departure.
 */
const TeamMembersDialog: React.FC<TeamMembersDialogProps> = ({
  open,
  onClose,
  orgSlug,
  runId,
  teamId,
  teamName,
  rallyeCode,
  onMemberRemoved,
  reloadSignal = 0,
  onTeamRemoved,
}) => {
  const { t } = useTranslation('runs');
  const [members, setMembers] = useState<TeamMemberDTO[] | null>(null);
  // Every read of the member list is an ask of its own: the opening, each
  // `reloadSignal` and each "load again". `rosterRead` says which ask the
  // list on screen answers. The list is a display: no confirmation counts on
  // it, because the server says whether a step dissolves the team.
  const [rosterRetry, setRosterRetry] = useState(0);
  const [rosterRead, setRosterRead] = useState<{ ask: string; ok: boolean } | null>(null);
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    // A dialog that opens again asks anew, also when its parent keeps it mounted.
    setWasOpen(open);
    if (open) setRosterRetry((n) => n + 1);
  }
  const rosterAsk = `${reloadSignal}:${rosterRetry}`;
  const [error, setError] = useState<string | null>(null);
  const [confirmingPick, setConfirming] = useState<TeamMemberDTO | null>(null);
  const [removing, setRemoving] = useState(false);
  // Set before a removal request starts and cleared when it ends. The pressed button
  // keeps the focus, so it is not natively disabled (Chrome would drop the focus to BODY);
  // this ref is what refuses a second press or Enter. State alone is not enough: two
  // presses in one tick both read the old state.
  const requestRunning = useRef(false);
  // The plain question of "Team entfernen" replaces the member list while set.
  const [confirmingTeam, setConfirmingTeam] = useState(false);
  // The member about to be moved. While it is set, the picker of the target
  // team replaces the member list.
  const [movingPick, setMoving] = useState<TeamMemberDTO | null>(null);
  // The server refused a step because it would dissolve the team. This is the
  // only source of the dissolution confirmation, and its text comes from the
  // refusal alone. Only its confirm button sends `confirm_dissolution`.
  const [refused, setRefused] = useState<RefusedStep | null>(null);
  // The team's own invite details, revealed on request rather than sitting in
  // the dialog: handing the code on is a deliberate act, not a default view.
  const [shareCode, setShareCode] = useState<string | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);

  const joinUrl =
    typeof window !== 'undefined' && rallyeCode && shareCode
      ? `${window.location.origin}/join/${rallyeCode}?team=${shareCode}`
      : null;

  // No synchronous state resets here (react-hooks/set-state-in-effect). The
  // parent keys this component by team id, which gives each team fresh state,
  // and handleClose below cleans up on close.
  //
  // `reloadSignal` is a dependency on purpose: a person who joins or leaves
  // while the dialog is open must appear in it, not only in the rows behind.
  useEffect(() => {
    if (!open || !teamId) return;
    let cancelled = false;
    getTeam(orgSlug, runId, teamId)
      .then((data) => {
        if (!cancelled) {
          setMembers((data as TeamWithMembers).members ?? []);
          setShareCode(data.share_code ?? null);
          setRosterRead({ ask: rosterAsk, ok: true });
        }
      })
      .catch(() => {
        // The dialog says that the members could not be read and offers to
        // read them again. The answer of a cancelled, older ask never lands.
        if (!cancelled) setRosterRead({ ask: rosterAsk, ok: false });
      });
    return () => {
      cancelled = true;
    };
  }, [open, orgSlug, runId, teamId, rosterAsk]);

  const rosterFresh = rosterRead?.ask === rosterAsk;
  const rosterFailed = rosterFresh && !rosterRead.ok;
  const reloadRoster = () => setRosterRetry((n) => n + 1);
  // Says that the list could not be read, and offers to read it again.
  const rosterNote = rosterFailed && (
    <Alert
      severity="warning"
      sx={{ mb: 1 }}
      action={
        <Button color="inherit" size="small" onClick={reloadRoster}>
          {t('teamMembers.removeTeam.reload')}
        </Button>
      }
    >
      {t('teamMembers.removeTeam.membersFailed')}
    </Alert>
  );

  // A member whom a fresh list no longer names is no longer asked about: the
  // question falls back to the list.
  const stillThere = (m: TeamMemberDTO | null) =>
    m !== null && (!rosterFresh || !rosterRead.ok || members?.some((k) => k.id === m.id)) ? m : null;

  const confirming = stillThere(confirmingPick);
  const moving = stillThere(movingPick);

  // Each inner step replaces the content and unmounts the button that was
  // pressed, which would leave the focus on the dialog container. The focus
  // goes into the new content instead, inside role=dialog:
  //  - the plain questions take it on their text;
  //  - the refusal takes it on its first control, "Abbrechen": its sentence is
  //    spoken once, by the polite live region, and not again by the focus;
  //  - a step back to the list takes it on the dialog title.
  const stepRef = useRef<HTMLDivElement>(null);
  const refusedCancelRef = useRef<HTMLButtonElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const refusedTextId = useId();
  const view = refused ? 'refused' : confirmingTeam ? 'team' : confirming ? 'member' : moving ? 'move' : 'list';
  const previousView = useRef(view);
  // A layout effect: the pressed button is already gone when the result changes the view,
  // so the focus must be on its new target before the browser paints.
  useLayoutEffect(() => {
    const before = previousView.current;
    previousView.current = view;
    if (!open) return;
    if (view === 'refused') refusedCancelRef.current?.focus();
    else if (view === 'team' || view === 'member') stepRef.current?.focus();
    else if (view === 'list' && before !== 'list') titleRef.current?.focus();
  }, [view, open, refused]);

  const handleClose = () => {
    setRefused(null);
    setConfirming(null);
    setConfirmingTeam(false);
    setMoving(null);
    setError(null);
    onClose();
  };

  // Every handler below refuses when its ref is set, or when `removing` of the render it
  // belongs to is true. The ref is cleared when the request ends, before React renders the
  // result; until then the DOM still shows the busy button with the handler of the busy
  // render, and that render's `removing` refuses a press in the gap. The handler of the
  // next render is free again.

  // The plain removal of a member, without `confirm_dissolution`. When other
  // members stay, the server does it. When it would dissolve the team, the
  // server refuses, and the refusal is shown.
  const handleRemove = async () => {
    if (!teamId || !confirming || requestRunning.current || removing) return;
    requestRunning.current = true;
    setRemoving(true);
    setError(null);
    try {
      await removeTeamMember(orgSlug, runId, teamId, confirming.id, false);
      setMembers((prev) => prev?.filter((m) => m.id !== confirming.id) ?? null);
      setConfirming(null);
      onMemberRemoved();
    } catch (err) {
      const refusal = teamWouldDissolve(err);
      if (refusal) {
        setConfirming(null);
        setRefused({ act: { kind: 'remove', member: confirming }, refusal });
        return;
      }
      setError(errorText(err, t('teamMembers.removeTeam.busy')));
    } finally {
      requestRunning.current = false;
      setRemoving(false);
    }
  };

  // "Team entfernen" after its plain question, without `confirm_dissolution`.
  // The step always dissolves the team, so the server refuses this call and
  // the refusal carries what the confirmation names.
  const handleRemoveTeam = async () => {
    if (!teamId || requestRunning.current || removing) return;
    requestRunning.current = true;
    setRemoving(true);
    setError(null);
    try {
      await removeTeam(orgSlug, runId, teamId, false);
      onTeamRemoved?.();
    } catch (err) {
      const refusal = teamWouldDissolve(err);
      if (refusal) {
        setConfirmingTeam(false);
        setRefused({ act: { kind: 'team' }, refusal });
        return;
      }
      // 404: somebody else removed the team first. It is gone either way.
      if (err instanceof ApiError && err.status === 404) {
        onTeamRemoved?.();
        return;
      }
      setError(errorText(err, t('teamMembers.removeTeam.busy')));
    } finally {
      requestRunning.current = false;
      setRemoving(false);
    }
  };

  // The person confirmed the refusal on screen: the same step again, with
  // `confirm_dissolution`. This is the only place that sends it. A new
  // refusal, because the facts changed in between, takes the place of the old
  // one, and the person decides again.
  const handleConfirmRefused = async () => {
    if (!teamId || !refused || requestRunning.current || removing) return;
    requestRunning.current = true;
    const { act } = refused;
    setRemoving(true);
    setError(null);
    try {
      if (act.kind === 'team') {
        await removeTeam(orgSlug, runId, teamId, true);
      } else if (act.kind === 'remove') {
        await removeTeamMember(orgSlug, runId, teamId, act.member.id, true);
      } else {
        await moveTeamMember(orgSlug, runId, teamId, act.member.id, act.targetTeamId, true);
      }
      setRefused(null);
      if (act.kind !== 'team') onMemberRemoved();
      onTeamRemoved?.();
    } catch (err) {
      const refusal = teamWouldDissolve(err);
      if (refusal) {
        setRefused({ act, refusal });
        return;
      }
      if (act.kind === 'team' && err instanceof ApiError && err.status === 404) {
        onTeamRemoved?.();
        return;
      }
      setError(errorText(err, t('teamMembers.removeTeam.busy')));
    } finally {
      requestRunning.current = false;
      setRemoving(false);
    }
  };

  // The server did the move, so other members stay and the team with them.
  const handleMoved = (memberId: string) => {
    setMembers((prev) => prev?.filter((m) => m.id !== memberId) ?? null);
    setMoving(null);
    // Reuses the removal callback on purpose: both change member counts, and
    // the caller's refresh is the same either way.
    onMemberRemoved();
  };

  return (
    <Dialog open={open} onClose={handleClose} fullWidth maxWidth="xs">
      <DialogTitle ref={titleRef} tabIndex={-1} sx={{ outline: 'none' }}>
        {t('teamMembers.dialogTitle', { teamName })}
      </DialogTitle>
      <DialogContent>
        {error && <ErrorAlert message={error} />}
        {/* One polite live region, mounted before any refusal so that it
            exists empty. The server's refusal lands in it and is spoken once;
            the focus goes to "Abbrechen", not to this text. */}
        <Box role="status" aria-atomic="true">
          {refused && (
            <>
              <DialogContentText id={refusedTextId}>
                {t('dissolveRefused.staff', {
                  teamName,
                  members: refused.refusal.member_count,
                  answers: refused.refusal.answer_count,
                })}
              </DialogContentText>
              {/* Every sentence here comes from the server's refusal. */}
              {refused.refusal.run_ended && (
                <DialogContentText sx={{ mt: 1 }}>{t('dissolveRefused.staffRunEnded')}</DialogContentText>
              )}
              {refused.refusal.result_published && (
                <DialogContentText sx={{ mt: 1 }}>
                  {t('dissolveRefused.staffResultPublished')}
                </DialogContentText>
              )}
              {refused.act.kind === 'team' && (
                <DialogContentText sx={{ mt: 1 }}>{t('teamMembers.removeTeam.guests')}</DialogContentText>
              )}
            </>
          )}
        </Box>
        {refused ? null : confirmingTeam ? (
          <DialogContentText ref={stepRef} tabIndex={-1} sx={{ outline: 'none' }}>
            {t('teamMembers.removeTeam.confirm', { teamName })}
          </DialogContentText>
        ) : confirming ? (
          <DialogContentText ref={stepRef} tabIndex={-1} sx={{ outline: 'none' }}>
            {/* Only a guest is blocked from re-joining (teams.py records a
                departure for guests only), so a registered member gets the
                text without the block. */}
            {t(confirming.is_guest ? 'teamMembers.removeConfirm' : 'teamMembers.removeConfirmMember', {
              name: confirming.display_name,
            })}
          </DialogContentText>
        ) : moving && teamId ? (
          <MoveMemberPicker
            key={moving.id}
            orgSlug={orgSlug}
            runId={runId}
            teamId={teamId}
            member={moving}
            onMoved={() => handleMoved(moving.id)}
            onRefused={(targetTeamId, refusal) => {
              setMoving(null);
              setRefused({ act: { kind: 'move', member: moving, targetTeamId }, refusal });
            }}
          />
        ) : members === null ? (
          rosterFailed ? rosterNote : <CircularProgress size={24} />
        ) : members.length === 0 ? (
          <Typography color="text.secondary">{t('teamMembers.empty')}</Typography>
        ) : (
          <>
          {/* A failed refresh is said here too, with the way to read again. */}
          {rosterNote}
          <List dense>
            {members.map((m) => (
              <ListItem
                key={m.id}
                disablePadding
                // Room for both actions: MUI keeps 48 px for one, and a long
                // name (up to 50 characters) would run under the move icon.
                sx={{ pr: 10 }}
                secondaryAction={
                  <Stack direction="row" spacing={0.5}>
                    <Tooltip title={t('teamMembers.move.action', { name: m.display_name })}>
                      <IconButton
                        edge="end"
                        size="small"
                        aria-label={t('teamMembers.move.action', { name: m.display_name })}
                        onClick={() => setMoving(m)}
                      >
                        <SwapHorizIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title={t('teamMembers.remove', { name: m.display_name })}>
                      <IconButton
                        edge="end"
                        size="small"
                        aria-label={t('teamMembers.remove', { name: m.display_name })}
                        onClick={() => setConfirming(m)}
                      >
                        <PersonRemoveIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </Stack>
                }
              >
                <ListItemAvatar>
                  <Avatar sx={{ width: 32, height: 32 }}>
                    <PersonIcon fontSize="small" />
                  </Avatar>
                </ListItemAvatar>
                <ListItemText
                  primary={memberDisplayName(m.display_name)}
                  secondary={m.is_guest ? t('teamMembers.guest') : undefined}
                />
              </ListItem>
            ))}
          </List>
          </>
        )}

        {/* Adding someone means handing over the team's code: the person joins
            on their own, as from a teammate's phone. Folded away until asked
            for, so the dialog stays a member list. */}
        {!refused && !confirming && !confirmingTeam && !moving && shareCode && (
          <Box sx={{ mt: 2 }}>
            {inviteOpen ? (
              <Stack spacing={1.5} sx={{ alignItems: 'center' }}>
                <Typography variant="body2" color="text.secondary">
                  {t('teamMembers.invite.hint')}
                </Typography>
                <TeamShareCodeChip code={shareCode} variant="full" />
                {joinUrl && <QrCodeDisplay value={joinUrl} size={200} />}
              </Stack>
            ) : (
              <Button startIcon={<PersonAddAlt1Icon />} onClick={() => setInviteOpen(true)}>
                {t('teamMembers.invite.open')}
              </Button>
            )}
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        {refused ? (
          <>
            <Button
              ref={refusedCancelRef}
              onClick={() => { setError(null); setRefused(null); }}
              disabled={removing}
            >
              {t('teamMembers.cancel')}
            </Button>
            <Button color="error" onClick={handleConfirmRefused} aria-disabled={removing} sx={busySx}>
              {refused.act.kind === 'team'
                ? t('teamMembers.removeTeam.action')
                : refused.act.kind === 'remove'
                  ? t('teamMembers.confirmRemove')
                  : t('teamMembers.move.action', { name: refused.act.member.display_name })}
            </Button>
          </>
        ) : confirmingTeam ? (
          <>
            <Button onClick={() => setConfirmingTeam(false)} disabled={removing}>
              {t('teamMembers.cancel')}
            </Button>
            <Button color="error" onClick={handleRemoveTeam} aria-disabled={removing} sx={busySx}>
              {t('teamMembers.removeTeam.action')}
            </Button>
          </>
        ) : confirming ? (
          <>
            <Button onClick={() => setConfirming(null)} disabled={removing}>
              {t('teamMembers.cancel')}
            </Button>
            <Button color="error" onClick={handleRemove} aria-disabled={removing} sx={busySx}>
              {t('teamMembers.confirmRemove')}
            </Button>
          </>
        ) : moving ? (
          <Button onClick={() => setMoving(null)}>{t('teamMembers.cancel')}</Button>
        ) : (
          <>
            <Button
              color="error"
              sx={{ mr: 'auto' }}
              disabled={!teamId}
              onClick={() => {
                setError(null);
                setConfirmingTeam(true);
              }}
            >
              {t('teamMembers.removeTeam.action')}
            </Button>
            <Button onClick={handleClose}>{t('teamMembers.close')}</Button>
          </>
        )}
      </DialogActions>
    </Dialog>
  );
};

export default TeamMembersDialog;
