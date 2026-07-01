import React from 'react';
import SafeHtml from './SafeHtml';
import {
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Button,
} from '@mui/material';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import CloseIcon from '@mui/icons-material/Close';
import { useTranslation } from 'react-i18next';
import type { StationTips } from '../api/types';
import { dirAttr, resolveLocalized } from '../i18n/contentLocales';

interface StationTipsDialogProps {
  tips: StationTips;
  open: boolean;
  onClose: () => void;
}

/**
 * Participant-facing popup listing a station's tips & links. Content
 * is resolved to the current UI language with the usual de/en fallback
 * (`resolveLocalized`), and each resolved field gets its own `dir` so
 * RTL-authored content (e.g. Arabic) renders correctly even though the
 * dialog chrome stays LTR.
 */
const StationTipsDialog: React.FC<StationTipsDialogProps> = ({ tips, open, onClose }) => {
  const { t, i18n } = useTranslation('runs');
  const uiLang = i18n.language;

  const header = resolveLocalized(tips.header, uiLang);

  return (
    <Dialog open={open} onClose={onClose} aria-labelledby="station-tips-dialog-title" fullWidth maxWidth="xs">
      <DialogTitle
        id="station-tips-dialog-title"
        component="div"
        dir={dirAttr(header.locale)}
        sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 1 }}
      >
        <SafeHtml
          html={header.text}
          dir={dirAttr(header.locale)}
          sx={{ flex: 1, minWidth: 0, '& p': { mb: 0.5 }, '& h2, & h3': { mt: 0 } }}
        />
        <IconButton
          aria-label={t('stationTipsDialog.close')}
          onClick={onClose}
          size="small"
          edge="end"
        >
          <CloseIcon fontSize="small" />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers>
        <List disablePadding>
          {tips.links.map((link, idx) => {
            const label = resolveLocalized(link.label, uiLang);
            return (
              <ListItemButton
                key={idx}
                component="a"
                href={link.url}
                target="_blank"
                rel="noopener noreferrer"
                dir={dirAttr(label.locale)}
              >
                <ListItemText primary={label.text || link.url} />
                <ListItemIcon sx={{ minWidth: 'auto', ml: 1 }}>
                  <OpenInNewIcon fontSize="small" />
                </ListItemIcon>
              </ListItemButton>
            );
          })}
        </List>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t('stationTipsDialog.close')}</Button>
      </DialogActions>
    </Dialog>
  );
};

export default StationTipsDialog;
