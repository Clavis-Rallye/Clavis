import React from 'react';
import Box from '@mui/material/Box';
import logoSmall from '../assets/logos/logo-small.svg';

interface OnboardingBrandHeaderProps {
  /**
   * The mark to show, the small fox logo by default. A page may pass its own
   * icon, such as the profile mark on ProfileSetupStep. Size and position stay.
   */
  src?: string;
  alt?: string;
}

/**
 * The brand mark at the top of every onboarding and account page.
 *
 * Every mark gets the same 72 px size and spacing, whatever its image. The
 * steps then read as one app, and the content column below starts at the same
 * height on every step instead of jumping.
 */
const OnboardingBrandHeader: React.FC<OnboardingBrandHeaderProps> = ({
  src = logoSmall,
  alt = '',
}) => (
  <Box
    sx={{
      display: 'flex',
      justifyContent: 'center',
      pt: '48px',
      pb: '24px',
    }}
  >
    <Box component="img" src={src} alt={alt} sx={{ height: 72, width: 'auto', display: 'block' }} />
  </Box>
);

export default OnboardingBrandHeader;
