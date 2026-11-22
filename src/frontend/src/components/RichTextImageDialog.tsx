import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  LinearProgress,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import AddPhotoAlternateIcon from '@mui/icons-material/AddPhotoAlternate';
import { ORG_MEDIA_IMAGE_TYPES, uploadOrgMedia } from '../api/media';

interface InsertedImage {
  src: string;
  alt: string;
  caption?: string;
}

interface RichTextImageDialogProps {
  open: boolean;
  orgSlug: string;
  onClose: () => void;
  onInsert: (image: InsertedImage) => void;
}

/**
 * Picks an image, asks for its alternative text and uploads it to the
 * organisation's media storage. Alt text is mandatory: a city rally
 * is read by screen-reader users too, and a creator cannot tell for them which
 * image is decorative.
 */
const RichTextImageDialog: React.FC<RichTextImageDialogProps> = ({ open, orgSlug, onClose, onInsert }) => {
  const { t } = useTranslation('common');
  const [file, setFile] = useState<File | null>(null);
  const [alt, setAlt] = useState('');
  const [caption, setCaption] = useState('');
  const [altTouched, setAltTouched] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const previewUrl = useMemo(() => (file ? URL.createObjectURL(file) : null), [file]);
  useEffect(() => () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
  }, [previewUrl]);

  const reset = () => {
    setFile(null);
    setAlt('');
    setCaption('');
    setAltTouched(false);
    setProgress(null);
    setError(null);
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  const altMissing = alt.trim() === '';
  const busy = progress !== null;

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const next = e.target.files?.[0] ?? null;
    setError(null);
    if (next && !ORG_MEDIA_IMAGE_TYPES.includes(next.type)) {
      setError(t('richTextEditor.imageDialog.wrongType'));
      setFile(null);
      return;
    }
    setFile(next);
  };

  const handleInsert = async () => {
    setAltTouched(true);
    if (!file || altMissing) return;
    setError(null);
    setProgress(0);
    try {
      const { url } = await uploadOrgMedia(orgSlug, file, setProgress);
      const trimmedCaption = caption.trim();
      onInsert(trimmedCaption ? { src: url, alt: alt.trim(), caption: trimmedCaption } : { src: url, alt: alt.trim() });
      handleClose();
    } catch {
      setError(t('richTextEditor.imageDialog.uploadFailed'));
      setProgress(null);
    }
  };

  return (
    <Dialog open={open} onClose={busy ? undefined : handleClose} aria-labelledby="rich-text-image-dialog-title" fullWidth maxWidth="xs">
      <DialogTitle id="rich-text-image-dialog-title">{t('richTextEditor.imageDialog.title')}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 0.5 }}>
          <input
            ref={inputRef}
            type="file"
            accept={ORG_MEDIA_IMAGE_TYPES.join(',')}
            onChange={handleFile}
            style={{ display: 'none' }}
            tabIndex={-1}
            aria-hidden="true"
            data-testid="rich-text-image-input"
          />
          <Button
            variant="outlined"
            startIcon={<AddPhotoAlternateIcon />}
            onClick={() => inputRef.current?.click()}
            disabled={busy}
          >
            {file ? t('richTextEditor.imageDialog.changeFile') : t('richTextEditor.imageDialog.chooseFile')}
          </Button>
          {previewUrl && (
            <Box
              component="img"
              src={previewUrl}
              alt=""
              sx={{ maxWidth: '100%', maxHeight: 240, objectFit: 'contain', borderRadius: 1, alignSelf: 'center' }}
            />
          )}
          {file && (
            <Typography variant="caption" color="text.secondary">
              {file.name}
            </Typography>
          )}
          <TextField
            label={t('richTextEditor.imageDialog.altLabel')}
            value={alt}
            onChange={(e) => setAlt(e.target.value)}
            onBlur={() => setAltTouched(true)}
            required
            fullWidth
            disabled={busy}
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
            disabled={busy}
            helperText={t('richTextEditor.imageDialog.captionHelper')}
            slotProps={{ htmlInput: { maxLength: 300 } }}
          />
          {busy && <LinearProgress variant="determinate" value={progress ?? 0} aria-label={t('richTextEditor.imageDialog.uploading')} />}
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose} disabled={busy}>
          {t('richTextEditor.imageDialog.cancel')}
        </Button>
        <Button variant="contained" onClick={() => void handleInsert()} disabled={!file || busy}>
          {t('richTextEditor.imageDialog.insert')}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default RichTextImageDialog;
