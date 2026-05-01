import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import IosShareIcon from '@mui/icons-material/IosShare';
import { Box, Button, Snackbar, Stack, Tooltip, Typography } from '@mui/material';
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';

interface TeamShareCodeChipProps {
  code: string;
  /**
   * `inline` — compact rendering for headers/lists.
   * `full`   — large, prominent rendering for the team-info page and join-success.
   */
  variant: 'inline' | 'full';
}

const TeamShareCodeChip: React.FC<TeamShareCodeChipProps> = ({ code, variant }) => {
  const { t } = useTranslation('runs');
  const [snackOpen, setSnackOpen] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setSnackOpen(true);
    } catch {
      /* ignore — older browsers w/o clipboard API; the inline display still serves */
    }
  };

  const share = async () => {
    if (navigator.share) {
      try {
        await navigator.share({ title: t('team.shareCode.label'), text: code });
        return;
      } catch {
        /* user-cancelled or unsupported — fall through to copy */
      }
    }
    await copy();
  };

  if (variant === 'inline') {
    return (
      <>
        <Tooltip title={t('team.shareCode.copy')}>
          <Box
            component="button"
            type="button"
            onClick={() => void copy()}
            aria-label={t('team.shareCode.copy')}
            sx={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 0.5,
              px: 1,
              py: 0.25,
              border: 'none',
              borderRadius: 1,
              bgcolor: 'rgba(255,255,255,0.18)',
              color: 'inherit',
              cursor: 'pointer',
              fontFamily: 'monospace',
              fontSize: '0.95rem',
              fontWeight: 600,
              letterSpacing: 1,
              '&:hover': { bgcolor: 'rgba(255,255,255,0.3)' },
              '&:focus-visible': { outline: '2px solid', outlineColor: 'primary.contrastText', outlineOffset: 2 },
            }}
          >
            {code}
            <ContentCopyIcon sx={{ fontSize: 14, opacity: 0.8 }} />
          </Box>
        </Tooltip>
        <Snackbar
          open={snackOpen}
          autoHideDuration={2000}
          onClose={() => setSnackOpen(false)}
          message={t('team.shareCode.copied')}
        />
      </>
    );
  }

  // full variant
  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 1.5,
        py: 2,
        px: 3,
        bgcolor: 'background.paper',
        border: '2px dashed',
        borderColor: 'primary.main',
        borderRadius: 2,
      }}
    >
      <Typography variant="overline" color="text.secondary" sx={{ letterSpacing: 1.5 }}>
        {t('team.shareCode.label')}
      </Typography>
      <Typography
        component="div"
        sx={{
          fontFamily: 'monospace',
          fontSize: { xs: '2.4rem', sm: '3rem' },
          fontWeight: 700,
          letterSpacing: { xs: 4, sm: 6 },
          color: 'primary.main',
          userSelect: 'all',
          lineHeight: 1.1,
        }}
        data-variant="full"
        aria-label={t('team.shareCode.label')}
      >
        {code}
      </Typography>
      {/* Copy and share are the controls a participant hits while standing, so
          both carry the 44 px touch target. size="medium" plus minHeight keeps
          the outlined/contained look and the icon sizes unchanged. */}
      <Stack direction="row" spacing={1}>
        <Button
          variant="outlined"
          size="medium"
          sx={{ minHeight: 44 }}
          startIcon={<ContentCopyIcon />}
          onClick={() => void copy()}
        >
          {t('team.shareCode.copy')}
        </Button>
        {typeof navigator !== 'undefined' && 'share' in navigator && (
          <Button
            variant="contained"
            size="medium"
            sx={{ minHeight: 44 }}
            startIcon={<IosShareIcon />}
            onClick={() => void share()}
          >
            {t('team.shareCode.share')}
          </Button>
        )}
      </Stack>
      <Snackbar
        open={snackOpen}
        autoHideDuration={2000}
        onClose={() => setSnackOpen(false)}
        message={t('team.shareCode.copied')}
      />
    </Box>
  );
};

export default TeamShareCodeChip;
