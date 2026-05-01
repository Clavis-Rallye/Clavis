import React from 'react';
import Box from '@mui/material/Box';
import LegalLinks from './LegalLinks';

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
        <Box component="footer">
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
          <LegalLinks sx={{ paddingInline: '40px', pt: progress ? 0 : 2 }} />
        </Box>
      </Box>
    </Box>
  );
};

export default OnboardingShell;
