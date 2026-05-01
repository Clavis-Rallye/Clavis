import { test, expect } from '@playwright/test';
import { registerNewOrg, createRallye, createStation } from './helpers';

// Locks in the @dnd-kit/* + restrictToVerticalAxis/restrictToParentElement
// contract on RallyeDetail. The build and the type checker cannot catch a
// regression in the modifier runtime, but a real drag and drop on the station
// list can.

test.describe('station reorder (dnd-kit)', () => {
  test('drag station to a new position persists across reload', async ({ page }) => {
    await registerNewOrg(page, 'stnreorder');

    const rallyeId = await createRallye(page, `Reorder-${Date.now().toString(36)}`);
    // createStation lands back on the rallye with the new station open. The
    // goto below reloads the page, so every station starts collapsed.
    await createStation(page, rallyeId, 'Station-Alpha');
    await createStation(page, rallyeId, 'Station-Bravo');
    await createStation(page, rallyeId, 'Station-Charlie');

    await page.goto(`/rallyes/${rallyeId}`);

    // Each station card of RallyeDetail has a drag handle, and the DOM order of
    // the cards is the asserted position. hasNot excludes the section wrapper
    // card, which contains the station cards and so also matches the `has:` filter.
    const row = (idx: number) =>
      page
        .locator('.MuiCard-root')
        .filter({ has: page.getByRole('button', { name: 'Reihenfolge ändern' }) })
        .filter({ hasNot: page.locator('.MuiCard-root') })
        .nth(idx);

    await expect(row(0)).toContainText('Station-Alpha');
    await expect(row(1)).toContainText('Station-Bravo');
    await expect(row(2)).toContainText('Station-Charlie');

    // HTML5 dragTo() does not work: dnd-kit listens to PointerEvents. Its
    // PointerSensor has activationConstraint.distance=4, so the first move after
    // pointerdown must exceed 4 px before the drag starts. The sensor attaches
    // only to the element the pointer hovered, and a bare mouse.move() to
    // coordinates does not reliably dispatch that pointerover. So hover() the
    // handle before mouse.down().
    const handle = page.getByRole('button', { name: 'Reihenfolge ändern' });
    await expect(handle).toHaveCount(3);
    const source = handle.nth(0);
    const target = handle.nth(2);
    await source.scrollIntoViewIfNeeded();

    const reorderRequest = page.waitForResponse(
      (r) => r.url().includes(`/rallyes/${rallyeId}`) && r.request().method() === 'PUT',
    );

    await source.hover();
    await page.mouse.down();
    const sourceBox = await source.boundingBox();
    const targetBox = await target.boundingBox();
    if (!sourceBox || !targetBox) throw new Error('drag handles not visible');
    // Activation jiggle: a single move of more than 4 px.
    await page.mouse.move(
      sourceBox.x + sourceBox.width / 2 + 8,
      sourceBox.y + sourceBox.height / 2,
    );
    // Drag in steps, so the collision detection of dnd-kit sees intermediate
    // positions and ends with `over` = target row.
    await page.mouse.move(
      targetBox.x + targetBox.width / 2,
      targetBox.y + targetBox.height / 2 + 8,
      { steps: 20 },
    );
    await page.mouse.up();

    const resp = await reorderRequest;
    expect(resp.status()).toBe(200);

    // The optimistic update is already shown. The reload proves the server agrees.
    await page.reload();
    await expect(row(0)).toContainText('Station-Bravo');
    await expect(row(1)).toContainText('Station-Charlie');
    await expect(row(2)).toContainText('Station-Alpha');
  });
});
