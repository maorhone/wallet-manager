import { expect, fake, loginAs, resetData, test } from './fixtures';

test.beforeAll(() => resetData());
test.afterEach(async () => {
  await fake('/_test/outage', { on: false });
});

test('U6 switcher lists self + group + department for noa', async ({ page }) => {
  await loginAs(page, 'noa@corp.example');
  await page.goto('/');
  const switcher = page.getByTestId('scope-switcher');
  await expect(switcher).toBeVisible();
  const labels = await switcher.locator('option').allTextContents();
  expect(labels).toEqual([
    'הארנק שלי',
    expect.stringContaining('הקבוצה: Group A2'),
    expect.stringContaining('המחלקה: Department D1'),
  ]);
  // The choice is remembered per user and decides the route.
  await switcher.selectOption({ index: 1 });
  await expect(page).toHaveURL(/\/group\/\d+\/overview/);
  expect(await page.evaluate(() => localStorage.getItem('wm.scope.noa@corp.example'))).toMatch(
    /^group:\d+$/,
  );
  await page.goto('/');
  await expect(page).toHaveURL(/\/group\/\d+\/overview/);
});

test('U8 html is rtl/he and numbers render ltr', async ({ page }) => {
  await loginAs(page, 'noa@corp.example');
  await page.goto('/wallet');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.locator('html')).toHaveAttribute('lang', 'he');
  expect(await page.evaluate(() => getComputedStyle(document.body).direction)).toBe('rtl');
  // Identifiers (here the UPN in the top bar) sit in <bdi dir="ltr">.
  const upn = page.locator('bdi[dir="ltr"]', { hasText: 'noa@corp.example' });
  await expect(upn).toBeVisible();
  expect(await upn.evaluate((el) => getComputedStyle(el).direction)).toBe('ltr');
});

test('banner appears during fake outage', async ({ page }) => {
  await loginAs(page, 'noa@corp.example');
  await page.goto('/wallet');
  await expect(page.getByTestId('status-banner')).toHaveCount(0);
  await fake('/_test/outage', { on: true });
  // /status caches reachability for 10 s and the banner polls every 5 s.
  await expect(page.getByTestId('status-banner')).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId('status-banner')).toContainText('השינויים נשמרים');
  await fake('/_test/outage', { on: false });
  await expect(page.getByTestId('status-banner')).toHaveCount(0, { timeout: 20_000 });
});
