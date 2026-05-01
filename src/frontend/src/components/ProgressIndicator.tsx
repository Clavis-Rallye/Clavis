import React from 'react';
import Box from '@mui/material/Box';

/**
 * V2 linear progress for onboarding: 8 px track height, pill ends, light-blue
 * track (#C1C9E5), dark-blue active (#1D47A4), small dark-blue stop dot at the
 * far right.
 */
interface ProgressIndicatorProps {
  current: number;
  total: number;
  ariaLabel?: string;
}

const ProgressIndicator: React.FC<ProgressIndicatorProps> = ({
  current,
  total,
  ariaLabel = 'Onboarding-Fortschritt',
}) => {
  const safeTotal = Math.max(1, total);
  const safeCurrent = Math.min(Math.max(0, current), safeTotal);
  const pct = (safeCurrent / safeTotal) * 100;
  return (
    <Box
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={safeTotal}
      aria-valuenow={safeCurrent}
      aria-label={ariaLabel}
      sx={{
        position: 'relative',
        height: '8px',
        width: '100%',
        // The outer linear progress has padding-left 4 inside the 314 px container.
        paddingLeft: '4px',
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
      }}
    >
      {pct > 0 && (
        <Box
          sx={{
            flexBasis: `${pct}%`,
            height: '100%',
            backgroundColor: '#1D47A4',
            borderRadius: '999px',
            transition: 'flex-basis 200ms ease-out',
          }}
        />
      )}
      <Box
        sx={{
          // Track-and-stop wrapper. The layout is vertical, with the stop dot
          // absolutely positioned at the right edge of the track, overlapping
          // it (not floating after a gap).
          position: 'relative',
          flex: 1,
          height: '100%',
        }}
      >
        <Box
          sx={{
            width: '100%',
            height: '100%',
            backgroundColor: '#C1C9E5',
            borderRadius: '4px',
          }}
        />
        <Box
          aria-hidden
          sx={{
            // 8 px stop container at right end, 4×4 dot centered in it →
            // dot sits on top of the grey track, 2 px from track-right edge.
            position: 'absolute',
            right: '2px',
            top: '50%',
            transform: 'translateY(-50%)',
            width: '4px',
            height: '4px',
            borderRadius: '50%',
            backgroundColor: '#1D47A4',
          }}
        />
      </Box>
    </Box>
  );
};

export default ProgressIndicator;
