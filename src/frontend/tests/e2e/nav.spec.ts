import { test, expect } from '@playwright/test';
import { registerNewOrg, navTo } from './helpers';

test.describe('sidebar navigation (org_admin sees all routes)', () => {
  test('all admin routes load through Caddy @smoke', async ({ page }) => {
    await registerNewOrg(page, 'nav');

    // After an in-app navigation (here the post-registration redirect) the focus
    // sits on the dashboard's h1, so Tab from here reaches the next control
    // and not the skip link.
    await expect(page.locator('main h1')).toBeFocused();

    // A fresh load moves no focus, so there the first Tab reaches the skip link.
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Dashboard', level: 1 })).toBeVisible();
    await page.keyboard.press('Tab');
    await expect(page.getByRole('link', { name: 'Zum Hauptinhalt springen' })).toBeFocused();

    await navTo(page, 'Rallyes');
    await expect(page).toHaveURL(/\/rallyes$/);
    await expect(page.getByRole('heading', { name: 'Rallyes', level: 1 })).toBeVisible();

    // Stations and challenges are top-level creator pages.
    await navTo(page, 'Stationen');
    await expect(page).toHaveURL(/\/stations$/);
    await expect(page.getByRole('heading', { name: 'Stationen', level: 1 })).toBeVisible();

    await navTo(page, 'Aufgaben');
    await expect(page).toHaveURL(/\/challenges$/);
    await expect(page.getByRole('heading', { name: 'Aufgaben', level: 1 })).toBeVisible();

    await navTo(page, 'Läufe');
    await expect(page).toHaveURL(/\/runs$/);
    await expect(page.getByRole('heading', { name: 'Läufe', level: 1 })).toBeVisible();

    await navTo(page, 'Personen');
    await expect(page).toHaveURL(/\/admin\/users$/);
    await expect(page.getByRole('heading', { name: 'Personen', level: 1 })).toBeVisible();

    await navTo(page, 'Protokolle');
    await expect(page).toHaveURL(/\/admin\/logs$/);
    await expect(page.getByRole('heading', { name: 'Protokoll', level: 1 })).toBeVisible();
    // The labelled landmark of the LogViewer is the table itself.
    await expect(page.getByRole('table', { name: 'Audit-Protokolleinträge' })).toBeVisible();

    await navTo(page, 'Einstellungen');
    await expect(page).toHaveURL(/\/settings$/);

    // There is no Dashboard sidebar item. The brand button in the header routes
    // back to the dashboard. Its accessible name is its visible text (logo alt,
    // "Willkommen zurück, <name>", organization) followed by "Zum Dashboard". It
    // has no accessible description, so a screen reader does not say "Dashboard"
    // twice. Match the stable part of the text, so the display name does not matter.
    const brandButton = page.getByRole('banner').getByRole('button', { name: /Willkommen zurück/ });
    await expect(brandButton).toHaveAccessibleName(/Zum Dashboard$/);
    await expect(brandButton).toHaveAccessibleDescription('');
    await expect(brandButton).toHaveAccessibleName(/^(?:(?!Dashboard).)*Dashboard(?:(?!Dashboard).)*$/s);
    await brandButton.click();
    await expect(page).toHaveURL(/\/dashboard$/);
  });

  test('language switch (now in profile) toggles UI strings DE↔EN', async ({ page }) => {
    await registerNewOrg(page, 'i18n');
    await page.goto('/profile');
    await page.getByRole('button', { name: 'EN', exact: true }).click();
    // The sidebar labels switch to English.
    await expect(page.getByRole('button', { name: 'Rallyes', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Runs', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'DE', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Läufe', exact: true })).toBeVisible();
  });
});
