import { expect, loginAs, resetData, test } from './fixtures';
test.beforeAll(() => resetData());
test('service status page and guide render', async ({ page }) => {
  await loginAs(page, 'admin@corp.example');
  await page.goto('/v2/service');
  await expect(page.getByText('מילון סיבות חסימה')).toBeVisible();
  await page.goto('/v2/guide');
  await expect(page.getByRole('link', { name: 'למרכז הבקשות' })).toBeVisible();
});
