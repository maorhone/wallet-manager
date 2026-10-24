import { expect, loginAs, resetData, test } from './fixtures';
test.beforeAll(() => resetData());
test('section head transfers unused credits with preview', async ({ page }) => {
  await loginAs(page, 'dana@corp.example');
  await page.goto('/v2/group/1');
  await page.getByRole('button', { name: 'העברה' }).first().click();
  await page.getByLabel('מקור').selectOption({ label: 'Dani Cohen' });
  await page.getByLabel('יעד').selectOption({ label: 'Yael Mizrahi' });
  await page.getByLabel('סכום').fill('1000');
  await page.getByRole('button', { name: 'תצוגה מקדימה' }).click();
  await page.getByLabel('סיבת השינוי').fill('איזון');
  await page.getByRole('button', { name: 'אישור העברה' }).click();
  await expect(page.getByRole('status')).toContainText('התקציב הכולל לא השתנה');
});
