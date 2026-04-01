import React from 'react';
import { useTranslation } from 'react-i18next';
import { ToggleButton, ToggleButtonGroup } from '@mui/material';
import { SUPPORTED_LANGUAGES, type SupportedLanguage } from '../i18n/resolveDefaultLanguage';

const LanguageSwitcher: React.FC = () => {
  const { i18n, t } = useTranslation('common');
  // The resolved language, as api/client.ts reads it for X-Clavis-Language:
  // i18n.language keeps the raw detected tag (en-US), which names no button.
  const current = i18n.resolvedLanguage;

  const handleChange = (_: React.MouseEvent<HTMLElement>, lang: SupportedLanguage | null) => {
    if (lang) {
      i18n.changeLanguage(lang);
    }
  };

  return (
    <ToggleButtonGroup
      value={current}
      exclusive
      onChange={handleChange}
      aria-label={t('language.switchTo')}
      size="small"
      sx={{
        '& .MuiToggleButton-root': { py: 0.25, px: 1.5 },
        // The switch showed no focus to a keyboard user ("outline: none").
        // The group is one tab stop, the arrow keys move inside it.
        '& .MuiToggleButton-root.Mui-focusVisible': {
          outline: '2px solid',
          outlineColor: 'primary.main',
          outlineOffset: '2px',
        },
      }}
    >
      {SUPPORTED_LANGUAGES.map((lang) => (
        <ToggleButton key={lang} value={lang} aria-label={t(`language.${lang}`)}>
          {t(`language.${lang}`)}
        </ToggleButton>
      ))}
    </ToggleButtonGroup>
  );
};

export default LanguageSwitcher;
