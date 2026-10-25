import { expect, loginAs, resetData, test } from './fixtures';

test.beforeAll(() => resetData());

test('U6 permissions follow selected scope', async ({ page }) => {
  await loginAs(page, 'noa@corp.example');
  await page.goto('/wallet');
  const switcher = page.getByTestId('scope-switcher');
  const me = await (await page.request.get('/me')).json();
  const csrf = (await page.context().cookies()).find((c) => c.name === 'wm_csrf')?.value ?? '';

  // Group scope (head of A2): members are editable.
  await switcher.selectOption({ index: 1 });
  await expect(page).toHaveURL(/\/group\/\d+\/overview/);
  await page.getByTestId('tab-members').click();
  await expect(page.getByTestId('alloc-input-avi@corp.example')).toBeVisible();

  // Department scope (viewer of D1): Split is read-only and the API refuses the write.
  await switcher.selectOption({ index: 2 });
  await expect(page).toHaveURL(/\/department\/\d+\/overview/);
  await page.getByTestId('tab-split').click();
  await expect(page.getByTestId('allocation-table')).toBeVisible();
  await expect(page.locator('[data-testid^="alloc-input-"]')).toHaveCount(0);
  await expect(page.getByTestId('alloc-save')).toHaveCount(0);
  const dept = me.scopes.find((s: { kind: string }) => s.kind === 'department').id;
  const res = await page.request.put(`/departments/${dept}/split`, {
    data: { groups: [], pmo_pool: 0, reserve: 0 },
    headers: { 'X-CSRF-Token': csrf, 'X-Change-Reason': encodeURIComponent('e2e') },
  });
  expect(res.status()).toBe(403);
});
