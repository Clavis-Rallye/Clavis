import { test, expect } from '@playwright/test';

// A language chosen on /welcome outlives the browser session. The storage key is
// `clavis_language` (i18n/index.ts, detection.lookupLocalStorage).

test.describe('welcome language persistence', () => {
  test('selecting DE in EN session persists into a fresh session', async ({ browser }) => {
    // Session 1: EN browser, no prior storage.
    const ctxEn = await browser.newContext({ locale: 'en-US' });
    const page = await ctxEn.newPage();
    await page.goto('/welcome');

    // Defaults to EN because navigator says en-US and localStorage is empty.
    await expect(
      page.getByRole('heading', { name: 'Welcome to Clavis!' }),
    ).toBeVisible();

    await page.getByRole('combobox', { name: 'Language' }).click();
    await page.getByRole('option', { name: 'Deutsch' }).click();

    // WelcomeStep switches the language on selection, not on Continue, so the
    // button already reads "Weiter". The pattern accepts either label.
    await page.getByRole('button', { name: /continue|weiter/i }).click();
    await expect(page).not.toHaveURL(/\/welcome$/);

    // i18n.changeLanguage wrote the storage key on selection.
    await expect
      .poll(async () => page.evaluate(() => localStorage.getItem('clavis_language')))
      .toBe('de');

    const storage = await ctxEn.storageState();
    await ctxEn.close();

    // Session 2: navigator still EN, but storage now carries clavis_language=de.
    // resolveDefaultLanguage prefers localStorage over navigator.
    const ctxRestore = await browser.newContext({
      locale: 'en-US',
      storageState: storage,
    });
    const page2 = await ctxRestore.newPage();
    await page2.goto('/welcome');

    await expect(
      page2.getByRole('heading', { name: 'Willkommen bei Clavis!' }),
    ).toBeVisible();
    await expect(page2.getByRole('combobox', { name: 'Sprache' })).toContainText(
      'Deutsch',
    );

    await ctxRestore.close();
  });
});
