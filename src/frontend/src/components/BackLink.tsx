import React from 'react';
import { Link as RouterLink } from 'react-router';
import { Link } from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';

interface BackLinkProps {
  to: string;
  label: string;
  /** Called on a click. `preventDefault()` on the event keeps the page. */
  onClick?: (event: React.MouseEvent<HTMLAnchorElement>) => void;
}

const BackLink: React.FC<BackLinkProps> = ({ to, label, onClick }) => (
  <Link
    component={RouterLink}
    to={to}
    onClick={onClick}
    underline="hover"
    // minHeight 44 gives the row a thumb-sized hit box. The extra height sits
    // as padding around text and icon.
    sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5, mb: 2, minHeight: 44 }}
  >
    <ArrowBackIcon fontSize="small" />
    {label}
  </Link>
);

export default BackLink;
