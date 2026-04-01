import React, { useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  Box,
  Button,
  Card,
  CardContent,
  Rating,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import { createFeedback } from '../../api/runs';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import ErrorAlert from '../../components/ErrorAlert';

/**
 * Post-run feedback form.
 *
 * Route: `/runs/:runId/participant/feedback` (and `/runs/:runId/feedback`).
 * Feedback is anonymous on the backend. The server must not link it to the
 * user id, so the form sends only `{ rating, comment? }`.
 */
const FeedbackForm: React.FC = () => {
  const { runId } = useParams<{ runId: string }>();
  const { user } = useAuth();
  const { t } = useTranslation('runs');
  const navigate = useNavigate();

  const [rating, setRating] = useState<number | null>(null);
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One feedback per device and run. Feedback is anonymous, so the server
  // cannot tell people apart. This device remembers that it sent one and shows
  // the thank-you instead.
  const sentKey = `clavis_feedback_sent_${runId}`;
  const [done, setDone] = useState(() => {
    try {
      return localStorage.getItem(sentKey) === '1';
    } catch {
      return false;
    }
  });

  const orgSlug = user?.organization_slug;
  const canSubmit = !!rating && rating > 0 && !submitting && !!orgSlug && !!runId;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      await createFeedback(orgSlug!, runId!, {
        rating: rating!,
        comment: comment.trim() || undefined,
      });
      try {
        localStorage.setItem(sentKey, '1');
      } catch {
        // storage blocked: the form simply stays available on this device
      }
      setDone(true);
    } catch (err) {
      // The rate limit answers 429 without a "detail". Without its own text the
      // client would show the English message of axios.
      if (err instanceof ApiError && err.status === 429) {
        setError(t('participant.feedback.rateLimited'));
      } else {
        setError(err instanceof ApiError ? err.detail : String(err));
      }
      setSubmitting(false);
    }
  };

  return (
    <Box sx={{ py: 2 }}>
      <Card>
        <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
          <Typography variant="h2" component="h1" gutterBottom>
            {t('participant.feedback.title')}
          </Typography>

          {done ? (
            <Stack spacing={2} sx={{ mt: 2, textAlign: 'center' }}>
              <Typography variant="h5" component="p">
                {t('participant.feedback.thankYou')}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {t('participant.feedback.thankYouSubtitle')}
              </Typography>
              <Box>
                {/* The standing, not the stations. Feedback is asked once the run
                    has ended, so no station is left to do. A team then wants to
                    see where it came out. Scoring usually finishes later, so this
                    is also the view the team returns to. */}
                <Button
                  variant="contained"
                  onClick={() => navigate(`/runs/${runId}/participant/team/leaderboard`)}
                  sx={{ minWidth: 160 }}
                >
                  {t('participant.nav.leaderboard')}
                </Button>
              </Box>
            </Stack>
          ) : (
            <Box component="form" onSubmit={handleSubmit} noValidate>
              <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
                {t('participant.feedback.intro')}
              </Typography>
              {error && (
                <Box sx={{ mb: 2 }}>
                  <ErrorAlert message={error} />
                </Box>
              )}
              <Stack spacing={3}>
                <Box>
                  <Typography
                    variant="subtitle2"
                    component="div"
                    id="feedback-rating-label"
                    sx={{ display: 'block', mb: 1 }}
                  >
                    {t('participant.feedback.rating')}
                  </Typography>
                  <Box role="radiogroup" aria-labelledby="feedback-rating-label">
                    <Rating
                      value={rating}
                      onChange={(_, next) => setRating(next)}
                      // The defaults of MUI are English: "1 Star", "Empty".
                      getLabelText={(value: number) => t('participant.feedback.stars', { count: value })}
                      emptyLabelText={t('participant.feedback.noRating')}
                      size="large"
                      sx={{
                        fontSize: '2.5rem',
                        // 3.03:1 on white. The library default is black at 26 percent, 1.88:1.
                        '& .MuiRating-iconEmpty': { color: '#949494' },
                      }}
                    />
                  </Box>
                </Box>

                <Box>
                  <Typography
                    variant="subtitle2"
                    component="label"
                    htmlFor="feedback-comment"
                    sx={{ display: 'block', mb: 0.5 }}
                  >
                    {t('participant.feedback.commentLabel')}
                  </Typography>
                  <TextField
                    id="feedback-comment"
                    value={comment}
                    onChange={(e) => setComment(e.target.value)}
                    multiline
                    minRows={3}
                    fullWidth
                    placeholder={t('participant.feedback.comment')}
                    slotProps={{ htmlInput: { maxLength: 2000 } }}
                  />
                </Box>

                <Box>
                  <Button
                    type="submit"
                    variant="contained"
                    size="large"
                    disabled={!canSubmit}
                    sx={{ minWidth: 200 }}
                  >
                    {t('participant.feedback.submit')}
                  </Button>
                </Box>
              </Stack>
            </Box>
          )}
        </CardContent>
      </Card>
    </Box>
  );
};

export default FeedbackForm;
