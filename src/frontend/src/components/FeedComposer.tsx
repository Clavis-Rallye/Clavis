import React, { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material';
import { createFeedEvent } from '../api/runs';
import { ApiError } from '../api/client';
import type { FeedEvent, FeedEventType } from '../api/types';
import ErrorAlert from './ErrorAlert';

// The form no longer offers 'reminder'. Reminders of past runs stay valid
// events and still show with their label in every list of notices.
const COMPOSER_TYPES: FeedEventType[] = ['announcement', 'countdown', 'alert'];

/** Sentinel for "no single team" — a broadcast. MUI reads an empty value as
 *  "nothing selected": the label drops into the field as a placeholder, and
 *  picking the option renders a blank field, because the displayed value *is*
 *  the empty string. A real value keeps both the label and the choice visible. */
const ALL_TEAMS = 'all';

/** Current local time as "HH:mm" — the countdown's date is always today, so the
 *  operator only picks a time of day, prefilled to now on load. */
function nowHHMM(): string {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

interface FeedComposerProps {
  orgSlug: string;
  runId: string;
  /** Teams in the run; when present, the operator can target a message at a
   *  single team instead of broadcasting to all. */
  teams?: Array<{ id: string; name: string }>;
  onPosted?: (event: FeedEvent) => void;
}

const FeedComposer: React.FC<FeedComposerProps> = ({ orgSlug, runId, teams, onPosted }) => {
  const { t } = useTranslation('runs');
  const hintIdPrefix = useId();

  const [eventType, setEventType] = useState<FeedEventType>('announcement');
  const [content, setContent] = useState('');
  const [targetTime, setTargetTime] = useState<string>(nowHHMM);
  const [targetTeamId, setTargetTeamId] = useState<string>(ALL_TEAMS);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!content.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const eventContent: Record<string, unknown> = { text: content.trim() };
      if (eventType === 'countdown' && targetTime) {
        // Date is always today; the operator only sets the time of day.
        const [hh, mm] = targetTime.split(':').map(Number);
        const target = new Date();
        target.setHours(hh, mm, 0, 0);
        eventContent.target_time = target.toISOString();
      }
      const posted = await createFeedEvent(orgSlug, runId, {
        event_type: eventType,
        content: eventContent,
        target_team_id: targetTeamId === ALL_TEAMS ? null : targetTeamId,
      });
      setContent('');
      setTargetTime(nowHHMM());
      // Back to the broadcast. A sticky recipient is the dangerous default here:
      // the next message looks like it goes to everyone and quietly goes to
      // whichever team was picked last.
      setTargetTeamId(ALL_TEAMS);
      onPosted?.(posted);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Box component="form" onSubmit={(e) => void handleSubmit(e)} sx={{ mb: 2 }}>
      {error && <ErrorAlert message={error} />}
      <Stack spacing={2}>
        <Box>
          <Typography
            variant="caption"
            sx={{
              color: "text.secondary",
              mb: 0.5,
              display: 'block'
            }}>
            {t('feedComposer.typeLabel')}
          </Typography>
          <ToggleButtonGroup
            value={eventType}
            exclusive
            onChange={(_, next: FeedEventType | null) => next && setEventType(next)}
            size="small"
          >
            {COMPOSER_TYPES.map((type) => (
              // The type names alone do not say what they do differently.
              // "Countdown" or "Mitteilung" is a guess until you have sent
              // one, so each has a hint on hover and focus.
              //
              // The hint is the button's description, never its name: a
              // string title would become the aria-label and hide the visible
              // word from a screen reader. So the title is an element, and the
              // button points at the always-present hint text below, open
              // tooltip or not.
              <Tooltip
                key={type}
                describeChild
                title={<span>{t(`feedComposer.typeHints.${type}`)}</span>}
              >
                <ToggleButton value={type} aria-describedby={`${hintIdPrefix}-${type}`}>
                  {t(`feedComposer.types.${type}`)}
                </ToggleButton>
              </Tooltip>
            ))}
          </ToggleButtonGroup>
          {COMPOSER_TYPES.map((type) => (
            <span key={type} id={`${hintIdPrefix}-${type}`} hidden>
              {t(`feedComposer.typeHints.${type}`)}
            </span>
          ))}
          {eventType === 'alert' && (
            // The alert keeps its urgent styling, so the composer has to say
            // what "urgent" costs before the message goes to every team in red.
            <Alert severity="warning" variant="outlined" sx={{ mt: 1 }}>
              {t('feedComposer.alertHint')}
            </Alert>
          )}
        </Box>

        {teams && teams.length > 0 && (
          <TextField
            select
            label={t('feedComposer.targetTeam')}
            value={targetTeamId}
            onChange={(e) => setTargetTeamId(e.target.value)}
            size="small"
            fullWidth
          >
            <MenuItem value={ALL_TEAMS}>{t('feedComposer.allTeams')}</MenuItem>
            {teams.map((team) => (
              <MenuItem key={team.id} value={team.id}>
                {team.name}
              </MenuItem>
            ))}
          </TextField>
        )}

        <TextField
          label={t('feedComposer.content')}
          value={content}
          onChange={(e) => setContent(e.target.value)}
          multiline
          minRows={2}
          required
          fullWidth
        />

        {eventType === 'countdown' && (
          <TextField
            label={t('feedComposer.targetTime')}
            value={targetTime}
            onChange={(e) => setTargetTime(e.target.value)}
            type="time"
            fullWidth
            slotProps={{ inputLabel: { shrink: true } }}
          />
        )}

        <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
          <Button
            type="submit"
            variant="contained"
            disabled={submitting || !content.trim()}
          >
            {t('feedComposer.submit')}
          </Button>
        </Box>
      </Stack>
    </Box>
  );
};

export default FeedComposer;
