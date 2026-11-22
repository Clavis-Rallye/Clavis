import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  FormLabel,
  Radio,
  RadioGroup,
  Stack,
  TextField,
} from '@mui/material';

/** What the dialog shows and hands back. `null` is "not set" (original size, no position). */
export interface ImageSettings {
  alt: string | null;
  caption: string | null;
  width: string | null;
  align: string | null;
}

interface RichTextImageSettingsDialogProps {
  open: boolean;
  image: ImageSettings;
  onClose: () => void;
  onSave: (settings: ImageSettings) => void;
}

const ORIGINAL = 'original';
// An image without a position stands on the left, the dialog shows it so.
const DEFAULT_ALIGN = 'left';

const SIZES = [
  { value: '25', labelKey: 'richTextEditor.imageSizeSmall' },
  { value: '50', labelKey: 'richTextEditor.imageSizeMedium' },
  { value: '75', labelKey: 'richTextEditor.imageSizeLarge' },
  { value: '100', labelKey: 'richTextEditor.imageSizeFull' },
  { value: ORIGINAL, labelKey: 'richTextEditor.imageSizeOriginal' },
];

const ALIGNS = [
  { value: 'left', labelKey: 'richTextEditor.imageAlignLeft' },
  { value: 'center', labelKey: 'richTextEditor.imageAlignCenter' },
  { value: 'right', labelKey: 'richTextEditor.imageAlignRight' },
];

/**
 * The form is its own component, keyed by the opening, so each opening starts
 * from the values of the image.
 */
const SettingsForm: React.FC<Omit<RichTextImageSettingsDialogProps, 'open'>> = ({ image, onClose, onSave }) => {
  const { t } = useTranslation('common');
  const [alt, setAlt] = useState(image.alt ?? '');
  const [caption, setCaption] = useState(image.caption ?? '');
  const [width, setWidth] = useState<string | null>(image.width);
  const [align, setAlign] = useState<string | null>(image.align);
  const [altTouched, setAltTouched] = useState(false);

  const altMissing = alt.trim() === '';

  const handleSave = () => {
    setAltTouched(true);
    if (altMissing) return;
    const trimmedCaption = caption.trim();
    onSave({ alt: alt.trim(), caption: trimmedCaption === '' ? null : trimmedCaption, width, align });
  };

  return (
    <>
      <DialogTitle id="rich-text-image-settings-title">{t('richTextEditor.imageDescription')}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 0.5 }}>
          <TextField
            label={t('richTextEditor.imageDialog.altLabel')}
            value={alt}
            onChange={(e) => setAlt(e.target.value)}
            onBlur={() => setAltTouched(true)}
            required
            fullWidth
            error={altTouched && altMissing}
            helperText={
              altTouched && altMissing
                ? t('richTextEditor.imageDialog.altRequired')
                : t('richTextEditor.imageDialog.altHelper')
            }
            slotProps={{ htmlInput: { maxLength: 200 } }}
          />
          <TextField
            label={t('richTextEditor.imageDialog.captionLabel')}
            value={caption}
            onChange={(e) => setCaption(e.target.value)}
            fullWidth
            helperText={t('richTextEditor.imageDialog.captionHelper')}
            slotProps={{ htmlInput: { maxLength: 300 } }}
          />
          <FormControl component="fieldset">
            <FormLabel component="legend">{t('richTextEditor.imageSize')}</FormLabel>
            <RadioGroup
              aria-label={t('richTextEditor.imageSize')}
              value={width ?? ORIGINAL}
              onChange={(e) => setWidth(e.target.value === ORIGINAL ? null : e.target.value)}
            >
              {SIZES.map((size) => (
                <FormControlLabel
                  key={size.value}
                  value={size.value}
                  control={<Radio />}
                  label={t(size.labelKey)}
                />
              ))}
            </RadioGroup>
          </FormControl>
          <FormControl component="fieldset">
            <FormLabel component="legend">{t('richTextEditor.imageAlign')}</FormLabel>
            <RadioGroup
              row
              aria-label={t('richTextEditor.imageAlign')}
              value={align ?? DEFAULT_ALIGN}
              onChange={(e) => setAlign(e.target.value)}
            >
              {ALIGNS.map((option) => (
                <FormControlLabel
                  key={option.value}
                  value={option.value}
                  control={<Radio />}
                  label={t(option.labelKey)}
                />
              ))}
            </RadioGroup>
          </FormControl>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t('richTextEditor.imageDialog.cancel')}</Button>
        <Button variant="contained" onClick={handleSave}>
          {t('richTextEditor.imageSave')}
        </Button>
      </DialogActions>
    </>
  );
};

/**
 * Description (alt text), caption, size and position of one image. The alt text
 * follows the rule of the insert dialog: it is required.
 */
const RichTextImageSettingsDialog: React.FC<RichTextImageSettingsDialogProps> = ({ open, image, onClose, onSave }) => {
  // The dialog fades out with its content still mounted. A new opening gets a
  // fresh form, so it never shows what was typed before and not saved.
  const [round, setRound] = useState(0);
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setRound((value) => value + 1);
  }
  return (
    <Dialog open={open} onClose={onClose} aria-labelledby="rich-text-image-settings-title" fullWidth maxWidth="xs">
      <SettingsForm key={round} image={image} onClose={onClose} onSave={onSave} />
    </Dialog>
  );
};

export default RichTextImageSettingsDialog;
