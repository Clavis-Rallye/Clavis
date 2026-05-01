import React from 'react';
import { Avatar } from '@mui/material';
import type { SxProps, Theme } from '@mui/material/styles';

interface UserAvatarProps {
  displayName?: string | null;
  email?: string | null;
  size?: number;
  sx?: SxProps<Theme>;
}

const firstLetter = (s: string): string => {
  const m = s.match(/[\p{L}\p{N}]/u);
  return m ? m[0]! : '';
};

const initialsFor = (displayName?: string | null, email?: string | null): string => {
  const name = (displayName ?? '').trim();
  if (name) {
    const parts = name
      .split(/\s+/)
      .map(firstLetter)
      .filter(Boolean);
    if (parts.length === 0) {
      // fall through to email
    } else if (parts.length === 1) {
      const word = name.match(/[\p{L}\p{N}]+/u)?.[0] ?? parts[0]!;
      return word.slice(0, 2).toUpperCase();
    } else {
      return (parts[0]! + parts[parts.length - 1]!).toUpperCase();
    }
  }
  const e = (email ?? '').trim();
  if (e) {
    const local = e.split('@')[0] ?? e;
    return local.slice(0, 2).toUpperCase();
  }
  return '?';
};

const UserAvatar: React.FC<UserAvatarProps> = ({ displayName, email, size = 36, sx }) => {
  const initials = initialsFor(displayName, email);
  return (
    <Avatar
      sx={{
        width: size,
        height: size,
        bgcolor: 'primary.main',
        color: 'primary.contrastText',
        fontSize: size * 0.4,
        fontWeight: 600,
        ...sx,
      }}
      aria-label={displayName ?? email ?? 'Benutzer'}
    >
      {initials}
    </Avatar>
  );
};

export default UserAvatar;
