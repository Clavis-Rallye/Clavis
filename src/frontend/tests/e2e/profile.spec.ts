import { test, expect } from '@playwright/test';
import { registerNewOrg } from './helpers';

test.describe('profile', () => {
  test('display name update persists', async ({ page }) => {
    const creds = await registerNewOrg(page, 'profile');
    await page.goto('/profile');
    const newName = `Renamed ${Date.now().toString(36)}`;
    await page.getByRole('textbox', { name: 'Anzeigename', exact: true }).fill(newName);
    await page.getByRole('button', { name: 'Änderungen speichern' }).click();
    await expect(page.getByText('Profil aktualisiert')).toBeVisible();
    // AppBar should re-render with the new display name
    await expect(page.getByText(newName).first()).toBeVisible();
    // Logout/login to confirm persistence
    await page.getByRole('button', { name: 'Abmelden', exact: true }).click();
    await page.getByRole('textbox', { name: 'E-Mail', exact: true }).fill(creds.email);
    await page.locator('input[type="password"]').first().fill(creds.password);
    await page.getByRole('button', { name: 'Anmelden' }).click();
    await expect(page).not.toHaveURL(/\/login$/);
  });

  test('password change with current+new round-trips', async ({ page }) => {
    const creds = await registerNewOrg(page, 'pw');
    await page.goto('/profile');
    const newPassword = `New${creds.password}`;
    const passwords = page.locator('input[type="password"]');
    await passwords.nth(0).fill(creds.password);
    await passwords.nth(1).fill(newPassword);
    await passwords.nth(2).fill(newPassword);
    await page.getByRole('button', { name: 'Passwort ändern', exact: true }).click();
    // Wait for the success message. It appears only after the API call
    // resolved, so the new password is committed before the logout. A blind
    // timeout would race the backend.
    await expect(page.getByText('Passwort geändert')).toBeVisible();

    // Log out, log back in with new password
    await page.getByRole('button', { name: 'Abmelden', exact: true }).click();
    await page.getByRole('textbox', { name: 'E-Mail', exact: true }).fill(creds.email);
    await page.locator('input[type="password"]').first().fill(newPassword);
    await page.getByRole('button', { name: 'Anmelden' }).click();
    await expect(page).not.toHaveURL(/\/login$/);
  });

  test('weak new password is rejected', async ({ page }) => {
    const creds = await registerNewOrg(page, 'pweak');
    await page.goto('/profile');
    const passwords = page.locator('input[type="password"]');
    await passwords.nth(0).fill(creds.password);
    await passwords.nth(1).fill('weak');
    await passwords.nth(2).fill('weak');
    await page.getByRole('button', { name: 'Passwort ändern', exact: true }).click();
    // Form stays on /profile; either inline error or alert
    await expect(page).toHaveURL(/\/profile$/);
  });
});
