import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, IconButton, Link, Stack, Tooltip, Typography } from '@mui/material';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import CheckIcon from '@mui/icons-material/Check';
import PlaceIcon from '@mui/icons-material/Place';
import { navigationHref } from '../utils/navigationHref';

/**
 * A station's address, ready to act on: tappable to hand off to the phone's
 * navigation app, and copyable for everyone else.
 *
 * Why both: participants are standing in a street trying
 * to reach the next station. Reading an address off a screen and retyping it is
 * the failure mode. The link covers the common case; the copy button is the
 * fallback that works everywhere, including desktop and any browser that does
 * not hand off the URL scheme.
 */

interface StationAddressProps {
  address: string;
  lat?: number | null;
  lng?: number | null;
  /** Station title — shown as the pin label in the navigation app. */
  label?: string;
  /** Compact rendering for the map popup, where space is tight. */
  dense?: boolean;
  /**
   * The place as a block of its own, for the station page and the task page.
   * Teams overlooked a grey line, so the place gets a frame, the full text
   * size and a line that says what a tap does.
   */
  prominent?: boolean;
}

const StationAddress: React.FC<StationAddressProps> = ({
  address,
  lat,
  lng,
  label,
  dense = false,
  prominent = false,
}) => {
  const { t } = useTranslation(['runs']);
  const [copied, setCopied] = useState(false);

  const hasCoords = typeof lat === 'number' && typeof lng === 'number';

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(address);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access can be denied (insecure context, permissions). The
      // address stays selectable on screen, so this is a silent no-op rather
      // than an error worth interrupting someone mid-rally with.
    }
  };

  const variant = dense ? 'caption' : prominent ? 'body1' : 'body2';
  const text = hasCoords ? (
    <Link
      href={navigationHref(lat as number, lng as number, label)}
      underline={prominent ? 'always' : 'hover'}
      color={prominent ? 'primary' : 'inherit'}
      variant={variant}
      sx={prominent ? { fontWeight: 500 } : undefined}
      aria-label={t('participant.stationChallenges.address.openInMaps', { address })}
    >
      {address}
    </Link>
  ) : (
    <Typography
      variant={variant}
      color={prominent ? 'text.primary' : 'text.secondary'}
      sx={prominent ? { fontWeight: 500 } : undefined}
    >
      {address}
    </Typography>
  );

  return (
    <Stack
      direction="row"
      spacing={prominent ? 1 : 0.5}
      sx={{
        alignItems: 'center',
        color: 'text.secondary',
        ...(prominent && {
          border: 1,
          borderColor: 'divider',
          borderRadius: 2,
          bgcolor: 'background.paper',
          pl: 1.5,
          pr: 0.5,
          py: 0.5,
        }),
      }}
    >
      <PlaceIcon fontSize={prominent ? 'medium' : 'small'} color={prominent ? 'primary' : 'action'} />
      {prominent ? (
        <Box sx={{ flex: 1, minWidth: 0 }}>
          {text}
          {hasCoords && (
            <Typography variant="caption" color="text.secondary" component="p">
              {t('participant.stationChallenges.address.mapsHint')}
            </Typography>
          )}
        </Box>
      ) : (
        text
      )}
      <Tooltip
        title={t(
          copied
            ? 'participant.stationChallenges.address.copied'
            : 'participant.stationChallenges.address.copy',
        )}
      >
        <IconButton
          size="small"
          // 28 px is under the 44 px a finger needs.
          sx={{ minWidth: 44, minHeight: 44 }}
          onClick={handleCopy}
          aria-label={t('participant.stationChallenges.address.copy')}
        >
          {copied ? (
            <CheckIcon fontSize="inherit" color="success" />
          ) : (
            <ContentCopyIcon fontSize="inherit" />
          )}
        </IconButton>
      </Tooltip>
    </Stack>
  );
};

export default StationAddress;
