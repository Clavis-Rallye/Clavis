import { test, expect, type Locator, type Page } from '@playwright/test';
import { joinAsGuest, setupActiveRunViaApi } from './helpers';

/**
 * The team chat fits the phone screen.
 *
 * A fixed chat height such as `100dvh - 220px` pushes the input below the
 * bottom navigation, because the header, the team code, the tabs and the bottom
 * navigation together take far more than 220 px. jsdom lays nothing out, so
 * this is asserted in a real browser: at four phone sizes, in a team without a
 * message and in a team with fifty.
 */

/** The four phone sizes, in CSS px. The first is the Pixel 8 Pro in Chrome. */
const SIZES = [
  { width: 448, height: 867 },
  { width: 390, height: 844 },
  { width: 360, height: 640 },
  { width: 320, height: 568 },
] as const;

const MESSAGE_COUNT = 50;

async function box(locator: Locator) {
  const rect = await locator.boundingBox();
  expect(rect, 'the element has a box').not.toBeNull();
  return rect!;
}

/** The chat geometry at the current viewport size of the page. */
async function expectChatFitsTheScreen(page: Page, messageCount: number) {
  const viewport = page.viewportSize()!;
  const at = `${viewport.width} by ${viewport.height}, ${messageCount} messages`;

  const input = page.getByRole('textbox', { name: 'Nachricht eingeben' });
  const send = page.getByRole('button', { name: 'Senden', exact: true });
  const list = page.getByRole('log');
  const nav = page.locator('.MuiBottomNavigation-root');
  await expect(input).toBeVisible();
  await expect(nav).toBeVisible();

  const navBox = await box(nav);
  const listBox = await box(list);

  // The input line and its send button are on the screen, above the bottom
  // navigation. So is the character counter under the input.
  for (const [name, locator] of [
    ['input', input],
    ['send button', send],
    ['character counter', page.getByText(/^\d+\/2000$/)],
  ] as const) {
    const rect = await box(locator);
    expect(rect.y, `${name} starts on the screen (${at})`).toBeGreaterThanOrEqual(0);
    expect(rect.y + rect.height, `${name} ends above the bottom navigation (${at})`).toBeLessThanOrEqual(
      navBox.y,
    );
    expect(rect.y + rect.height, `${name} ends inside the viewport (${at})`).toBeLessThanOrEqual(
      viewport.height,
    );
    expect(rect.y, `${name} stands below the message list (${at})`).toBeGreaterThanOrEqual(
      listBox.y + listBox.height,
    );
  }

  // The page itself does not scroll on the chat tab.
  const doc = await page.evaluate(() => {
    window.scrollTo(0, 10_000);
    const el = document.documentElement;
    return { scrollHeight: el.scrollHeight, clientHeight: el.clientHeight, scrollY: window.scrollY };
  });
  expect(doc.scrollHeight, `the document is no higher than the screen (${at})`).toBeLessThanOrEqual(
    doc.clientHeight,
  );
  expect(doc.scrollY, `the document cannot be scrolled (${at})`).toBe(0);

  // The list keeps a usable height at every size: the space is what the layout
  // leaves, not what a subtracted number assumed.
  expect(listBox.height, `the message list keeps some lines (${at})`).toBeGreaterThanOrEqual(120);

  // The list of messages scrolls inside its own area.
  const listScroll = await list.evaluate((el) => ({
    scrollHeight: el.scrollHeight,
    clientHeight: el.clientHeight,
    scrollTop: el.scrollTop,
    overflowY: getComputedStyle(el).overflowY,
  }));
  expect(listScroll.overflowY).toBe('auto');
  if (messageCount > 0) {
    expect(listScroll.scrollHeight, `fifty messages overflow the list (${at})`).toBeGreaterThan(
      listScroll.clientHeight,
    );
    // The newest message is the one in view, at the lower end of the list.
    await expect(page.getByText(`Nachricht ${messageCount}`, { exact: true })).toBeInViewport();
  }

  // The team code takes one line on the chat tab and stays one tap away.
  const copy = page.getByRole('button', { name: 'Code kopieren' });
  await expect(copy).toBeInViewport();
  const codeLine = await box(page.getByTestId('team-code-line'));
  expect(codeLine.height, `the team code is one line (${at})`).toBeLessThanOrEqual(48);
}

test.describe('participant chat layout', () => {
  test('the input stays above the bottom navigation at four phone sizes, empty and with fifty messages', async ({
    page,
    browser,
  }) => {
    // Eight layouts in one test, each after a resize. The default 60 s is too
    // short on a loaded stack once the run and the guest are set up.
    test.setTimeout(120_000);

    const { joinCode } = await setupActiveRunViaApi(page, 'chatfit');
    const guestCtx = await browser.newContext({ viewport: SIZES[0] });
    const guest = await joinAsGuest(guestCtx, joinCode, 'ChatFit', 'Fit Team');

    await guest.getByRole('button', { name: 'Team', exact: true }).click();
    await expect(guest).toHaveURL(/\/participant\/team\/leaderboard$/);
    await guest.getByRole('tab', { name: 'Chat', exact: true }).click();
    await expect(guest).toHaveURL(/\/participant\/team\/chat$/);
    // The team has loaded once its code line is there.
    await expect(guest.getByRole('button', { name: 'Code kopieren' })).toBeVisible();

    // A team with no message.
    await expect(guest.getByText('Hier ist es noch still')).toBeVisible();
    for (const size of SIZES) {
      await guest.setViewportSize(size);
      await expectChatFitsTheScreen(guest, 0);
    }

    // A team with fifty messages. The API accepts thirty chat messages a minute,
    // so the fifty are put into the response of the list request. The test reads
    // the layout, not the storage of a message.
    await guest.route(/\/teams\/[0-9a-f-]+\/chat\/\?/, async (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      const response = await route.fetch();
      const body = (await response.json()) as { items: unknown[]; total: number };
      const started = Date.parse('2026-11-21T10:00:00Z');
      body.items = Array.from({ length: MESSAGE_COUNT }, (_, i) => ({
        id: `00000000-0000-4000-8000-${String(i + 1).padStart(12, '0')}`,
        organization_id: '00000000-0000-4000-8000-000000000000',
        team_id: '00000000-0000-4000-8000-000000000000',
        // Every third message is long, so the list also holds wrapped bubbles.
        author_id: `00000000-0000-4000-9000-${String(i % 3).padStart(12, '0')}`,
        content: i % 3 === 0 ? `Nachricht ${i + 1} ${'mit einem langen Text '.repeat(6)}` : `Nachricht ${i + 1}`,
        is_system_message: false,
        author_role: 'guest',
        author_display_name: `Mitglied ${i % 3}`,
        created_at: new Date(started + i * 60_000).toISOString(),
      }));
      body.total = MESSAGE_COUNT;
      await route.fulfill({ response, json: body });
    });
    for (const size of SIZES) {
      // The list jumps to its newest message when the messages arrive, so each
      // size loads the chat anew, as a phone of that size would.
      await guest.setViewportSize(size);
      await guest.reload();
      await expect(guest).toHaveURL(/\/participant\/team\/chat$/);
      await expect(guest.getByRole('button', { name: 'Code kopieren' })).toBeVisible();
      await expect(guest.getByText(`Nachricht ${MESSAGE_COUNT}`, { exact: true })).toBeVisible();
      await expectChatFitsTheScreen(guest, MESSAGE_COUNT);
    }

    await guestCtx.close();
  });
});
