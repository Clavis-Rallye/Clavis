import React from 'react';
import { useTranslation } from 'react-i18next';
import { Box } from '@mui/material';
import { visuallyHiddenSx } from '../../styles/visuallyHidden';
import type { TeamMember } from '../../api/types';

/** How many names the row shows before "+ n". */
const SHOWN_NAMES = 3;

interface TeamMemberNamesProps {
  members: Array<Pick<TeamMember, 'id' | 'display_name'>>;
}

/**
 * The members of a team in its row on the run page: up to three names, oldest
 * member first, and "+ n" for the rest. The coordinator sees who is in a team
 * without opening the member dialog, which lists all names.
 *
 * The names never make the row wider than the card. They wrap at spaces, and a
 * name without a space breaks anywhere. Two other forms fail at 390 px: one
 * line of shrinking names reads "Sa…, Nguyen Van …", and one line per name
 * pushes the grid column, and so the card, past the window. A screen reader
 * hears "und 5 weitere" instead of "plus 5".
 */
const TeamMemberNames: React.FC<TeamMemberNamesProps> = ({ members }) => {
  const { t } = useTranslation('runs');
  if (members.length === 0) return null;
  const shown = members.slice(0, SHOWN_NAMES);
  const rest = members.length - shown.length;

  return (
    <Box
      component="span"
      data-testid="team-member-names"
      sx={{ display: 'block', color: 'text.primary', overflowWrap: 'anywhere' }}
    >
      {shown.map((m) => m.display_name).join(', ')}
      {rest > 0 && (
        <Box component="span" sx={{ whiteSpace: 'nowrap', fontWeight: 700, pl: '0.5ch' }}>
          <span aria-hidden="true">{t('dashboard.memberNames.more', { count: rest })}</span>
          <Box component="span" sx={visuallyHiddenSx}>
            {t('dashboard.memberNames.moreLabel', { count: rest })}
          </Box>
        </Box>
      )}
    </Box>
  );
};

export default TeamMemberNames;
