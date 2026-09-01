import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Button,
  Card,
  CardContent,
  IconButton,
  Snackbar,
  Stack,
  Typography,
} from '@mui/material';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import EmojiEventsIcon from '@mui/icons-material/EmojiEvents';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import { getResultLink, publishResults, revokeResults } from '../../api/runs';
import { ApiError } from '../../api/client';
import type { ResultLinkStatus } from '../../api/types';
import { AnswerStateChip } from '../../components/AnswerState';
import ConfirmDialog from '../../components/ConfirmDialog';
import ErrorAlert from '../../components/ErrorAlert';
import LoadingSpinner from '../../components/LoadingSpinner';
import { formatDate } from '../../utils/formatDate';

interface ResultLinkCardProps {
  orgSlug: string;
  runId: string;
  /** Scored submissions across the run, from the scoreboard totals. */
  scored: number;
  total: number;
  /** Ended run with every submission scored — gates the publish action. */
  gradingComplete: boolean;
}

/**
 * Grading status of an ended run plus the public result link:
 * publish (with a confirm that names what becomes public and for how long),
 * copy, expiry, revoke (with confirm) and the link to the closing view.
 */
const ResultLinkCard: React.FC<ResultLinkCardProps> = ({
  orgSlug,
  runId,
  scored,
  total,
  gradingComplete,
}) => {
  const { t, i18n } = useTranslation('runs');
  const navigate = useNavigate();

  const [link, setLink] = useState<ResultLinkStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [publishOpen, setPublishOpen] = useState(false);
  const [revokeOpen, setRevokeOpen] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [copyToast, setCopyToast] = useState<string | null>(null);
  const publicLinkRef = useRef<HTMLAnchorElement>(null);
  // Set by a first publication: the button that opened the dialog is gone
  // with the unpublished state, so the dialog has nothing to give the focus
  // back to.
  const focusPublicLink = useRef(false);

  const load = useCallback(async () => {
    if (!orgSlug || !runId) return;
    try {
      setLink(await getResultLink(orgSlug, runId));
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.detail : String(err));
    }
  }, [orgSlug, runId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handlePublish = async () => {
    const firstPublication = link?.status !== 'published';
    setActionLoading(true);
    setActionError(null);
    try {
      const published = await publishResults(orgSlug, runId);
      setLink({
        status: 'published',
        url: published.url,
        published_at: published.published_at,
        expires_at: published.expires_at,
      });
      // A second publication keeps "Erneut veröffentlichen", and the dialog
      // returns the focus to it by itself.
      if (firstPublication) focusPublicLink.current = true;
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setActionLoading(false);
      setPublishOpen(false);
    }
  };

  const handleRevoke = async () => {
    setActionLoading(true);
    setActionError(null);
    try {
      await revokeResults(orgSlug, runId);
      setLink({ status: 'revoked', url: null, published_at: null, expires_at: null });
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setActionLoading(false);
      setRevokeOpen(false);
    }
  };

  const handleCopy = () => {
    if (!link?.url) return;
    void navigator.clipboard
      .writeText(link.url)
      .then(() => setCopyToast(t('results.copySuccess')))
      .catch(() => setCopyToast(t('results.copyFailure')));
  };

  const isPublished = link?.status === 'published';

  // Runs after the closing dialog has tried to restore the focus (its cleanup
  // comes first in the same commit), so the link has the last word.
  useEffect(() => {
    if (!focusPublicLink.current || publishOpen || !isPublished) return;
    focusPublicLink.current = false;
    publicLinkRef.current?.focus();
  }, [isPublished, publishOpen]);

  return (
    <Card>
      <CardContent>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 1.5 }}>
          <EmojiEventsIcon color="warning" />
          <Typography variant="h6" component="h2">
            {t('results.title')}
          </Typography>
        </Stack>

        <Typography variant="body1" component="p" sx={{ fontWeight: 700, mb: 0.5 }}>
          {t('results.scoredOfTotal', { scored, total })}
        </Typography>
        <AnswerStateChip
          state={gradingComplete ? 'scored' : 'submitted'}
          label={gradingComplete ? t('results.gradingComplete') : t('results.gradingOpen')}
          sx={{ mb: 2 }}
        />

        {loadError && <ErrorAlert message={loadError} onRetry={load} />}
        {actionError && <ErrorAlert message={actionError} />}

        {!link && !loadError && <LoadingSpinner minHeight={60} />}

        {link && isPublished && link.url && (
          <Stack spacing={1.5}>
            <Typography variant="body2" color="text.secondary">
              {t('results.linkLabel')}
            </Typography>
            <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
              <Typography
                variant="body2"
                component="a"
                ref={publicLinkRef}
                href={link.url}
                target="_blank"
                rel="noopener noreferrer"
                sx={{ color: 'primary.main', wordBreak: 'break-all' }}
              >
                {link.url}
              </Typography>
              <IconButton size="small" onClick={handleCopy} aria-label={t('results.copyLink')}>
                <ContentCopyIcon fontSize="small" />
              </IconButton>
            </Stack>
            {link.expires_at && (
              <Typography variant="body2" color="text.secondary">
                {t('results.expires', {
                  date: formatDate(link.expires_at, i18n.language),
                })}
              </Typography>
            )}
            {!gradingComplete && (
              <Typography variant="body2" color="text.secondary">
                {t('results.publishPrerequisiteHint')}
              </Typography>
            )}
            <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap' }}>
              {/* Re-publishing replaces the snapshot under the same slug and
                  restarts the expiry. A reopened graded
                  answer makes grading open again, and the backend rejects a
                  republish until it is complete once more. */}
              <Button
                variant="outlined"
                onClick={() => setPublishOpen(true)}
                disabled={!gradingComplete || actionLoading}
              >
                {t('results.republish')}
              </Button>
              <Button
                variant="outlined"
                color="error"
                onClick={() => setRevokeOpen(true)}
                disabled={actionLoading}
              >
                {t('results.revoke')}
              </Button>
            </Stack>
          </Stack>
        )}

        {link && !isPublished && (
          <Stack spacing={1.5}>
            {link.status === 'revoked' && <Alert severity="info">{t('results.revokedNotice')}</Alert>}
            {!gradingComplete && (
              <Typography variant="body2" color="text.secondary">
                {t('results.publishPrerequisiteHint')}
              </Typography>
            )}
            <Button
              variant="contained"
              onClick={() => setPublishOpen(true)}
              disabled={!gradingComplete || actionLoading}
            >
              {t('results.publish')}
            </Button>
          </Stack>
        )}

        <Button
          variant="text"
          startIcon={<OpenInNewIcon />}
          onClick={() => navigate(`/runs/${runId}/results`)}
          sx={{ mt: 2 }}
        >
          {t('results.openClosingView')}
        </Button>
      </CardContent>

      <ConfirmDialog
        open={publishOpen}
        // A second publication keeps the link and replaces the published
        // standings. The dialog says so.
        title={t(isPublished ? 'results.republishConfirmTitle' : 'results.publishConfirmTitle')}
        message={t(isPublished ? 'results.republishConfirmMessage' : 'results.publishConfirmMessage')}
        confirmLabel={t(isPublished ? 'results.republish' : 'results.publish')}
        loading={actionLoading}
        onConfirm={() => void handlePublish()}
        onCancel={() => setPublishOpen(false)}
      />
      <ConfirmDialog
        open={revokeOpen}
        title={t('results.revokeConfirmTitle')}
        message={t('results.revokeConfirmMessage')}
        confirmLabel={t('results.revoke')}
        destructive
        loading={actionLoading}
        onConfirm={() => void handleRevoke()}
        onCancel={() => setRevokeOpen(false)}
      />

      <Snackbar
        open={!!copyToast}
        autoHideDuration={3000}
        onClose={() => setCopyToast(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert severity="success" onClose={() => setCopyToast(null)}>
          {copyToast}
        </Alert>
      </Snackbar>
    </Card>
  );
};

export default ResultLinkCard;
