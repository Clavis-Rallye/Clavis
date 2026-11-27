import React from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Link, Typography, useTheme } from '@mui/material';
import { pickLocalized, useOperatorNotice } from '../config/operatorNotice';

interface OperatorNoticeProps {
  sx?: object;
  align?: 'center' | 'start';
}

/**
 * The operator's notice: logos, a short text and links, for example the note
 * of a funding programme (spec 095). Renders nothing until a valid
 * `/operator/notice.json` has loaded, and appears without a reload when it
 * arrives after the first render.
 *
 * The section carries a name, so it is a region landmark. Text and links use
 * `text.secondary`, the colour `LegalLinks` uses on the same backgrounds.
 */
const OperatorNotice: React.FC<OperatorNoticeProps> = ({ sx, align = 'center' }) => {
  const { t, i18n } = useTranslation('common');
  const theme = useTheme();
  const notice = useOperatorNotice();
  if (!notice) return null;

  const language = i18n.language;
  const dark = theme.palette.mode === 'dark';
  const text = pickLocalized(notice.text, language, notice.text_lang);
  const justify = align === 'center' ? 'center' : 'flex-start';

  return (
    <Box
      component="section"
      aria-label={t('operatorNotice.label')}
      sx={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: align === 'center' ? 'center' : 'flex-start',
        textAlign: align === 'center' ? 'center' : 'start',
        gap: 1,
        pt: 2,
        ...sx,
      }}
    >
      {notice.logos.length > 0 && (
        <Box
          sx={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            justifyContent: justify,
            gap: 2,
          }}
        >
          {/* The order and the heights of the logos are the operator's legal
              duty, for example under the rules of a funding programme. So the
              list keeps the file order and each logo keeps its height: no
              max-height and no scaling. A narrow screen wraps the row. */}
          {notice.logos.map((logo, index) => (
            <img
              key={`${index}-${logo.src}`}
              src={`/operator/${dark && logo.src_dark ? logo.src_dark : logo.src}`}
              alt={pickLocalized(logo.alt, language)?.value ?? ''}
              decoding="async"
              style={{ height: logo.height, width: 'auto' }}
            />
          ))}
        </Box>
      )}
      {text && (
        // A string child: React escapes it, so markup shows as characters.
        <Typography
          variant="body2"
          color="text.secondary"
          lang={text.lang}
          sx={{ whiteSpace: 'pre-line' }}
        >
          {text.value}
        </Typography>
      )}
      {notice.links.length > 0 && (
        <Box
          sx={{
            display: 'flex',
            flexWrap: 'wrap',
            justifyContent: justify,
            columnGap: 2,
          }}
        >
          {notice.links.map((link, index) => (
            <Link
              key={`${index}-${link.url}`}
              href={link.url}
              target="_blank"
              rel="noopener noreferrer"
              variant="body2"
              color="text.secondary"
              underline="always"
              // 44 px tall hit area on phones, as in LegalLinks.
              sx={{ display: 'inline-flex', alignItems: 'center', minHeight: 44 }}
            >
              {link.label}
            </Link>
          ))}
        </Box>
      )}
    </Box>
  );
};

export default OperatorNotice;
