import { test, expect } from '@playwright/test';
import { registerNewOrg, login, uniqueOrg } from './helpers';

test.describe('auth flows', () => {
  test('register lands on dashboard with org_admin variant @smoke', async ({ page }) => {
    const creds = await registerNewOrg(page, 'auth');
    await expect(page.getByText(`Willkommen, ${creds.displayName}!`)).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Aktive Läufe', level: 2 })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Aktuelle Audit-Ereignisse', level: 2 })).toBeVisible();
  });

  test('logout returns to login', async ({ page }) => {
    await registerNewOrg(page, 'logout');
    await page.getByRole('button', { name: 'Abmelden', exact: true }).click();
    await expect(page).toHaveURL(/\/login$/);
  });

  test('login round-trip with known creds @smoke', async ({ page }) => {
    const creds = await registerNewOrg(page, 'rt');
    await page.getByRole('button', { name: 'Abmelden', exact: true }).click();
    await expect(page).toHaveURL(/\/login$/);
    // An org_admin lands on /2fa-setup (TOTP enforcement) instead of /dashboard.
    // So assert only that the URL left /login.
    await login(page, creds.email, creds.password);
    await expect(page).not.toHaveURL(/\/login$/);
  });

  test('register with reserved-TLD email surfaces inline error (regression: 422 detail-array crash)', async ({ page }) => {
    const creds = uniqueOrg('badtld');
    // The operator's org-bootstrap form lives at /register/organization.
    await page.goto('/register/organization');
    await page.getByRole('textbox', { name: 'Name der Organisation', exact: true }).fill(creds.name);
    await page.getByRole('textbox', { name: 'Kurzname der Organisation' }).fill(creds.slug);
    await page.getByRole('textbox', { name: 'E-Mail', exact: true }).fill(`${creds.slug}@admin.local`);
    await page.getByRole('textbox', { name: 'Anzeigename', exact: true }).fill(creds.displayName);
    const passwords = page.locator('input[type="password"]');
    await passwords.nth(0).fill(creds.password);
    await passwords.nth(1).fill(creds.password);
    await page.getByRole('button', { name: 'Konto erstellen' }).click();
    // The API client localizes the 422 detail array and does not echo the raw
    // English Pydantic message. A reserved-TLD email is a `value_error` on the
    // email field, so the inline alert shows the translated email-invalid message.
    await expect(page.getByRole('alert')).toContainText('Ungültige E-Mail-Adresse');
    // ErrorBoundary did NOT swallow the page
    await expect(page.getByRole('heading', { name: 'Konto erstellen', level: 1 })).toBeVisible();
  });
});
