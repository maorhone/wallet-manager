import { expect, loginAs, resetData, test } from './fixtures';

test.beforeAll(() => resetData());

test('U8 tables mirror and numbers/model names stay LTR', async ({ page }) => {
  await loginAs(page, 'rep@corp.example');
  const me = await (await page.request.get('/me')).json();
  const dept = me.scopes.find((s: { kind: string }) => s.kind === 'department').id;
  await page.goto(`/department/${dept}/overview`);
  const table = page.getByTestId('groups-table').locator('table');
  await expect(table).toBeVisible();
  expect(await table.evaluate((el) => getComputedStyle(el).direction)).toBe('rtl');

  // The first column sits to the right of the last one.
  const first = await table.locator('thead th').first().boundingBox();
  const last = await table.locator('thead th').last().boundingBox();
  expect(first!.x).toBeGreaterThan(last!.x);

  // Numbers sit in <bdi dir=ltr> inside Hebrew text.
  const num = page.locator('bdi[dir="ltr"]', { hasText: /^250,000$/ }).first();
  await expect(num).toBeVisible();
  expect(await num.evaluate((el) => getComputedStyle(el).direction)).toBe('ltr');
  expect(await num.evaluate((el) => el.closest('[dir="rtl"], html')?.getAttribute('dir'))).toBe(
    'rtl',
  );

  // Model names too (group pots table).
  const a1 = (await (await page.request.get(`/departments/${dept}/summary`)).json()).groups.find(
    (g: { name: string }) => g.name === 'Group A1',
  ).id;
  await page.goto(`/group/${a1}/overview`);
  const pots = await (await page.request.get(`/groups/${a1}/summary`)).json();
  if (pots.pots.length) {
    const model = page.locator('bdi[dir="ltr"]', { hasText: pots.pots[0].model }).first();
    await expect(model).toBeVisible();
  }
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
});
