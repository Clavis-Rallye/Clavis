import React from 'react';
import Box from '@mui/material/Box';
import LegalLinks from './LegalLinks';
import OperatorNotice from './OperatorNotice';
import { useOperatorNotice } from '../config/operatorNotice';

/**
 * The onboarding page frame, designed for a 393 px phone viewport.
 *
 * A white full-height column with gutters, a content slot and a slot at the
 * bottom for the progress bar. It sets no spacing between hero, title and body,
 * because each onboarding screen of the design arranges them differently.
 */
interface OnboardingShellProps {
  children: React.ReactNode;
  progress?: React.ReactNode;
  contentSx?: object;
  /**
   * Pin the shell to the viewport height instead of letting the page grow and
   * scroll. The shell itself then never scrolls. A page gives ONE child
   * `flex: 1; minHeight: 0; overflowY: auto`, and only that region scrolls
   * while brand mark, heading, actions and progress bar stay fixed.
   * OrganizationPicker scrolls only its organization list this way.
   */
  fillViewport?: boolean;
}

const OnboardingShell: React.FC<OnboardingShellProps> = ({
  children,
  progress,
  contentSx,
  fillViewport,
}) => {
  const notice = useOperatorNotice();
  return (
    <Box
      sx={{
        // 100dvh (not 100vh) so the fixed layout tracks the mobile browser's
        // dynamic toolbar instead of hiding the progress bar behind it.
        minHeight: fillViewport ? undefined : '100vh',
        height: fillViewport ? '100dvh' : undefined,
        overflow: fillViewport ? 'hidden' : undefined,
        backgroundColor: '#FFFFFF',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
      }}
    >
      <Box
        sx={{
          width: '100%',
          maxWidth: '480px',
          flex: 1,
          minHeight: fillViewport ? 0 : undefined,
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <Box
          component="main"
          sx={{
            flex: 1,
            minHeight: fillViewport ? 0 : undefined,
            overflow: fillViewport ? 'hidden' : undefined,
            display: 'flex',
            flexDirection: 'column',
            // The design centers a 314 px content column in a 393 px viewport,
            // so each side has a 40 px gutter.
            paddingInline: '40px',
            ...contentSx,
          }}
        >
          {children}
        </Box>
        {/* What stands under main, the progress bar and the legal links, is a
            footer landmark, so no content of the shell sits outside every
            landmark. */}
        <Box
          component="footer"
          sx={
            fillViewport
              ? {
                  // The footer takes its natural height, so a notice makes main
                  // smaller. The cap keeps at least half the viewport for main,
                  // so the page's actions and its scrolling child stay usable
                  // under a tall notice. Past the cap the footer scrolls itself.
                  flexShrink: 0,
                  maxHeight: '50%',
                  overflowY: 'auto',
                }
              : undefined
          }
        >
          {progress && (
            // 24 px above the progress bar keep it off the action row on tall
            // pages such as the invite step with its QR code. The 40 px below
            // it, above the legal links, are the design's value.
            <Box
              sx={{
                paddingInline: '39px',
                paddingBlockStart: '24px',
                paddingBlockEnd: '40px',
              }}
            >
              {progress}
            </Box>
          )}
          {/* Every entry page carries imprint, privacy and accessibility.
              Outside main, so the route focus still lands on the page
              heading. */}
          {/* The operator's notice stands above the legal links, with their
              side gutter. After the progress bar its 40 px bottom padding is
              the gap, as for the links. */}
          <OperatorNotice sx={{ paddingInline: '40px', pt: progress ? 0 : 2 }} />
          {/* Under a notice the links keep their own top padding, so they do
              not touch its last line. */}
          <LegalLinks sx={{ paddingInline: '40px', pt: progress && !notice ? 0 : 2 }} />
        </Box>
      </Box>
    </Box>
  );
};

export default OnboardingShell;
