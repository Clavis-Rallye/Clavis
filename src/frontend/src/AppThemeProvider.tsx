import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { deDE, enUS } from '@mui/material/locale';
import theme from './theme';

/**
 * The app theme with MUI's own texts in the interface language.
 *
 * MUI components carry a few built-in strings: the close button of an alert,
 * the pagination labels, "no options". Without a locale they are English, so
 * a German screen reader would hear "Close" on every toast.
 */
const AppThemeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { i18n } = useTranslation();
  const localized = useMemo(
    () => createTheme(theme, i18n.language.startsWith('de') ? deDE : enUS),
    [i18n.language],
  );
  return <ThemeProvider theme={localized}>{children}</ThemeProvider>;
};

export default AppThemeProvider;
