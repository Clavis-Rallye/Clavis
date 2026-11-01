import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { normalizeLinkAddress } from './linkAddress';
import { Button, Dialog, DialogActions, DialogContent, DialogTitle, TextField } from '@mui/material';

interface RichTextLinkDialogProps {
  open: boolean;
  onClose: () => void;
  /** Called with an address that has a scheme. */
  onInsert: (href: string) => void;
}

/**
 * The editor's own dialog for a link. It replaces the browser's
 * prompt, which showed the one word "URL", gave no hint and checked nothing.
 */
const RichTextLinkDialog: React.FC<RichTextLinkDialogProps> = ({ open, onClose, onInsert }) => {
  const { t } = useTranslation('common');
  const [value, setValue] = useState('');
  const [showError, setShowError] = useState(false);

  const close = () => {
    setValue('');
    setShowError(false);
    onClose();
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    // The dialog sits inside the page's form: its submit must not save the page.
    event.stopPropagation();
    const href = normalizeLinkAddress(value);
    if (!href) {
      setShowError(true);
      return;
    }
    onInsert(href);
    close();
  };

  return (
    <Dialog open={open} onClose={close} fullWidth maxWidth="xs" aria-labelledby="rich-text-link-title">
      <form onSubmit={submit} noValidate>
        <DialogTitle id="rich-text-link-title">{t('richTextEditor.addLink')}</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            margin="dense"
            label={t('richTextEditor.linkAddressLabel')}
            placeholder="https://"
            value={value}
            onChange={(event) => {
              setValue(event.target.value);
              setShowError(false);
            }}
            error={showError}
            helperText={showError ? t('richTextEditor.linkAddressInvalid') : t('richTextEditor.linkAddressHint')}
            slotProps={{ htmlInput: { inputMode: 'url' }, formHelperText: { role: showError ? 'alert' : undefined } }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={close}>{t('actions.cancel')}</Button>
          <Button type="submit" variant="contained">
            {t('richTextEditor.addLink')}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
};

export default RichTextLinkDialog;
