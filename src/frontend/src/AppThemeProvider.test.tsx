import { describe, it, expect, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Alert } from '@mui/material';
import i18n from './i18n';
import AppThemeProvider from './AppThemeProvider';

// A toast's close button follows the interface language, not the English default of MUI.
describe('AppThemeProvider', () => {
  afterEach(async () => {
    await i18n.changeLanguage('de');
  });

  it('names the close button of an alert in German', async () => {
    await i18n.changeLanguage('de');
    render(
      <AppThemeProvider>
        <Alert onClose={() => {}}>Gespeichert</Alert>
      </AppThemeProvider>,
    );
    expect(screen.getByRole('button', { name: 'Schließen' })).toBeInTheDocument();
  });

  it('names it in English when the interface is English', async () => {
    await i18n.changeLanguage('en');
    render(
      <AppThemeProvider>
        <Alert onClose={() => {}}>Saved</Alert>
      </AppThemeProvider>,
    );
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument();
  });
});
