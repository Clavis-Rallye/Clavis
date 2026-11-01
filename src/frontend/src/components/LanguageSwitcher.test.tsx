import { afterEach, describe, expect, it } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import i18n from '../i18n';
import LanguageSwitcher from './LanguageSwitcher';

// A browser reports a regional tag such as en-US. i18next
// resolves it to the shipped resource (en), and that resolved language is
// what the switch has to show: i18n.language keeps the raw tag.
describe('LanguageSwitcher', () => {
  afterEach(async () => {
    window.localStorage.removeItem('clavis_language');
    await i18n.changeLanguage('de');
  });

  const pressed = (name: string) =>
    screen.getByRole('button', { name }).getAttribute('aria-pressed');

  it('shows EN as pressed when the detected language is en-US', async () => {
    await i18n.changeLanguage('en-US');
    render(<LanguageSwitcher />);
    expect(pressed('EN')).toBe('true');
    expect(pressed('DE')).toBe('false');
  });

  it('shows DE as pressed when the detected language is de-DE', async () => {
    await i18n.changeLanguage('de-DE');
    render(<LanguageSwitcher />);
    expect(pressed('DE')).toBe('true');
    expect(pressed('EN')).toBe('false');
  });

  it('switches at once on a click on the other language and keeps it for a reload', async () => {
    await i18n.changeLanguage('en-US');
    render(<LanguageSwitcher />);
    await userEvent.click(screen.getByRole('button', { name: 'DE' }));
    await waitFor(() => expect(pressed('DE')).toBe('true'));
    expect(i18n.resolvedLanguage).toBe('de');
    expect(window.localStorage.getItem('clavis_language')).toBe('de');
  });
});
