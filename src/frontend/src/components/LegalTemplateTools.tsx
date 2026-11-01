import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, Box, Button, Typography } from '@mui/material';
import ConfirmDialog from './ConfirmDialog';
import { findLegalPlaceholders } from '../utils/legalPlaceholders';
import { legalTemplate, type LegalTemplateKind, type LegalTemplateValues } from '../utils/legalTemplates';
import { isRichTextEmpty } from '../utils/richtext';

interface LegalTemplateToolsProps {
  kind: LegalTemplateKind;
  /** Visible name of the field, for the button's accessible name and the dialog. */
  fieldLabel: string;
  /** Current editor document. */
  value: string;
  /** The text as last loaded from or saved to the server. */
  savedValue: string | null | undefined;
  /** Receives the template. The owner puts it into the editor and saves nothing. */
  onInsert: (html: string) => void;
  disabled?: boolean;
  /** The periods the app knows. The template states them instead of a placeholder. */
  templateValues?: LegalTemplateValues;
}

/**
 * The tools below one legal text editor: the button that fills the editor
 * with the template, and the warning about placeholders that are still open.
 * The button never saves. The page's own save button does that.
 */
const LegalTemplateTools: React.FC<LegalTemplateToolsProps> = ({
  kind,
  fieldLabel,
  value,
  savedValue,
  onInsert,
  disabled = false,
  templateValues,
}) => {
  const { t } = useTranslation('common');
  const [confirmOpen, setConfirmOpen] = useState(false);

  const insert = () => {
    onInsert(legalTemplate(kind, templateValues));
    setConfirmOpen(false);
  };

  const handleClick = () => {
    if (isRichTextEmpty(value)) {
      insert();
    } else {
      setConfirmOpen(true);
    }
  };

  const inEditor = findLegalPlaceholders(value);
  // The saved text only matters while the editor itself is clean: then the
  // published page still shows placeholders that the editor no longer has.
  const inSaved = inEditor.length === 0 ? findLegalPlaceholders(savedValue) : [];
  const helperId = `legal-template-helper-${kind}`;

  return (
    <Box sx={{ mt: 1 }}>
      <Button
        variant="outlined"
        size="small"
        onClick={handleClick}
        disabled={disabled}
        aria-label={t('legalTemplate.insertFor', { field: fieldLabel })}
        aria-describedby={helperId}
      >
        {t('legalTemplate.insert')}
      </Button>
      <Typography id={helperId} variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
        {t('legalTemplate.helper')}
      </Typography>
      {inEditor.length > 0 && (
        <Alert severity="warning" role="status" sx={{ mt: 1 }}>
          {t('legalTemplate.placeholdersInEditor', { placeholders: inEditor.join(', ') })}
        </Alert>
      )}
      {inSaved.length > 0 && (
        <Alert severity="warning" role="status" sx={{ mt: 1 }}>
          {t('legalTemplate.placeholdersInSaved', { placeholders: inSaved.join(', ') })}
        </Alert>
      )}
      <ConfirmDialog
        open={confirmOpen}
        title={t('legalTemplate.confirmTitle')}
        message={t('legalTemplate.confirmMessage', { field: fieldLabel })}
        confirmLabel={t('legalTemplate.confirmLabel')}
        destructive
        onConfirm={insert}
        onCancel={() => setConfirmOpen(false)}
      />
    </Box>
  );
};

export default LegalTemplateTools;
