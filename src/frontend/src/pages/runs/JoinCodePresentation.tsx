import React, { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Dialog, Typography } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { QRCodeSVG } from 'qrcode.react';
import { CODE_LETTER_SPACING_EM, presentationLayout } from './joinCodeLayout';

/**
 * The large join code: a mode of the run page for a projector or a laptop
 * held up to a group. The view shows the run's name, the QR code, the code in
 * letters and the join link, and nothing else: no navigation, no team data,
 * no personal data.
 *
 * It is a full-screen dialog, so the app shell stays underneath and the page
 * keeps its live connection. Escape and the visible button close it, and
 * the dialog gives the focus back to the button that opened it.
 *
 * The colours are fixed black on white, whatever the theme: the highest
 * contrast for a projector in a light room, and the form a QR scanner reads
 * best. The view does not ask the browser for full screen, because Escape
 * would then leave the full screen first and the view only on a second press.
 */

/**
 * The area a fixed dialog covers. The app keeps the page scrollbar while a
 * dialog is open, and innerWidth counts it, so the view measured 15 px too
 * wide in Chrome. jsdom has no layout and reports 0, then innerWidth counts.
 */
function viewport(): { width: number; height: number } {
  const root = document.documentElement;
  return {
    width: root.clientWidth || window.innerWidth,
    height: root.clientHeight || window.innerHeight,
  };
}

function useWindowSize(): { width: number; height: number } {
  const [size, setSize] = useState(viewport);
  useEffect(() => {
    const onResize = () => setSize(viewport());
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return size;
}

interface JoinCodePresentationProps {
  open: boolean;
  onClose: () => void;
  runName: string;
  joinCode: string;
  /** The same link the card's QR code holds. */
  joinUrl: string;
}

const INK = '#000000';
const PAPER = '#FFFFFF';

const JoinCodePresentation: React.FC<JoinCodePresentationProps> = ({
  open,
  onClose,
  runName,
  joinCode,
  joinUrl,
}) => {
  const { t } = useTranslation('runs');
  const headingId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  const { width, height } = useWindowSize();
  const layout = presentationLayout(width, height, joinCode.length);
  // The link without its scheme reads shorter and is typed the same way.
  const linkText = joinUrl.replace(/^https?:\/\//, '');

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullScreen
      aria-labelledby={headingId}
      slotProps={{
        paper: { sx: { bgcolor: PAPER, color: INK, backgroundImage: 'none', borderRadius: 0 } },
        // The view opens on its own exit, so Enter or Space also leaves it.
        // Focused after the transition: the dialog's focus trap takes the
        // focus to its container on open, after any autoFocus.
        transition: { onEntered: () => closeRef.current?.focus() },
      }}
    >
      <Box
        data-testid="join-presentation"
        sx={{
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          p: `${layout.padPx}px`,
          gap: `${layout.padPx / 2}px`,
          boxSizing: 'border-box',
          overflow: 'auto',
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 2, minHeight: 48 }}>
          <Typography
            id={headingId}
            component="h2"
            sx={{
              flex: 1,
              minWidth: 0,
              fontSize: `${layout.namePx}px`,
              fontWeight: 700,
              lineHeight: 1.2,
              color: INK,
              overflowWrap: 'anywhere',
            }}
          >
            {runName}
          </Typography>
          <Button
            ref={closeRef}
            onClick={onClose}
            startIcon={<CloseIcon />}
            variant="outlined"
            size="large"
            sx={{
              flex: 'none',
              minHeight: 48,
              color: INK,
              borderColor: INK,
              borderWidth: 2,
              fontWeight: 700,
              '&:hover': { borderColor: INK, borderWidth: 2, bgcolor: '#EEEEEE' },
              '&.Mui-focusVisible': { outline: `3px solid ${INK}`, outlineOffset: 3 },
            }}
          >
            {t('dashboard.presentation.close')}
          </Button>
        </Box>

        <Box
          sx={{
            flex: 1,
            minHeight: 0,
            display: 'flex',
            flexDirection: layout.direction,
            alignItems: 'center',
            justifyContent: 'center',
            gap: `${layout.padPx}px`,
          }}
        >
          <Box
            data-testid="join-presentation-qr"
            sx={{ flex: 'none', width: layout.qrPx, height: layout.qrPx, lineHeight: 0 }}
          >
            {/* Black on white with the standard quiet zone of four modules,
                the form every scanner reads. */}
            <QRCodeSVG
              value={joinUrl}
              size={layout.qrPx}
              level="M"
              marginSize={4}
              fgColor={INK}
              bgColor={PAPER}
              title={t('dashboard.qrTitle', { code: joinCode })}
            />
          </Box>
          <Box
            sx={{
              minWidth: 0,
              display: 'flex',
              flexDirection: 'column',
              alignItems: layout.direction === 'row' ? 'flex-start' : 'center',
              gap: 1,
            }}
          >
            <Typography component="p" sx={{ fontSize: `${layout.linkPx}px`, color: INK, lineHeight: 1.2 }}>
              {t('dashboard.joinCode')}
            </Typography>
            <Typography
              component="p"
              data-testid="join-presentation-code"
              sx={{
                fontFamily: 'monospace',
                fontWeight: 700,
                fontSize: `${layout.codePx}px`,
                letterSpacing: `${CODE_LETTER_SPACING_EM}em`,
                lineHeight: 1,
                color: INK,
                whiteSpace: 'nowrap',
              }}
            >
              {joinCode}
            </Typography>
            {/* For a phone that cannot scan: the link typed by hand leads to
                the same place as the QR code. */}
            <Typography
              component="p"
              sx={{
                fontSize: `${layout.linkPx}px`,
                color: INK,
                lineHeight: 1.3,
                overflowWrap: 'anywhere',
                textAlign: layout.direction === 'row' ? 'left' : 'center',
              }}
            >
              {linkText}
            </Typography>
          </Box>
        </Box>
      </Box>
    </Dialog>
  );
};

export default JoinCodePresentation;
