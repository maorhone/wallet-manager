import { expect, loginAs, resetData, test } from './fixtures';
test.beforeAll(() => resetData());
test('admin sees two separate envelopes and the departments tab', async ({ page }) => {
  await loginAs(page, 'admin@corp.example');
  await page.goto('/v2/organization');
  await expect(page.getByText('מעטפת מחלקות')).toBeVisible();
  await expect(page.getByText('מעטפת מערכות ארגוניות')).toBeVisible();
  await page.getByRole('tab', { name: 'מערכות ארגוניות' }).click();
  await expect(page.getByText('שתי המעטפות נפרדות ואינן מתערבבות.')).toBeVisible();
});
