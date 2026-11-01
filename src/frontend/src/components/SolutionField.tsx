import React from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Typography } from '@mui/material';
import RichTextEditor from './RichTextEditor';

interface SolutionFieldProps {
  /** The reference answer as stored: HTML, legacy plain text or empty. */
  value: string;
  onChange: (html: string) => void;
  disabled?: boolean;
}

/**
 * The reference answer of a task, in the rich-text editor.
 *
 * A coordinator reads it beside the answers in the grading view, so a list of
 * what counts, a link to the source or an image of the expected motif for a
 * photo task helps. It has one language, because only coordinators read it,
 * never a team.
 */
const SolutionField: React.FC<SolutionFieldProps> = ({ value, onChange, disabled }) => {
  const { t } = useTranslation('content');
  const label = t('challenge.create.solutionLabel');
  return (
    <Box>
      <Typography variant="subtitle2" component="label" sx={{ display: 'block', mb: 0.5 }}>
        {label}
      </Typography>
      <RichTextEditor value={value} onChange={onChange} ariaLabel={label} disabled={disabled} />
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5, ml: 1.75 }}>
        {t('challenge.create.solutionHelp')}
      </Typography>
    </Box>
  );
};

export default SolutionField;
