import { expect, loginAs, resetData, test } from './fixtures';
test.beforeAll(() => resetData());
test('department head adds a group above a section and sees org systems outside department totals', async ({
  page,
}) => {
  await loginAs(page, 'rep@corp.example');
  await page.goto('/v2/department/1');
  await expect(page.getByText('השוואת מדורים')).toBeVisible();
  await page
    .getByRole('button', { name: /הוסף קבוצה מעל/ })
    .first()
    .click();
  await page.getByLabel('שם הקבוצה החדשה').fill('קבוצת על');
  await page.getByLabel('סיבת השינוי').fill('ארגון מחדש');
  await page.getByRole('button', { name: 'שמירה' }).click();
  await expect(page.getByRole('status')).toContainText('השינוי נשמר');
  await page.getByRole('tab', { name: 'מערכות באחריותנו' }).click();
  await expect(page.getByText('מערכות ארגוניות אינן נכללות בסכומי המחלקה.')).toBeVisible();
});
