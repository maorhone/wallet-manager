import { expect, loginAs, resetData, test } from './fixtures';
test.beforeAll(() => resetData());
test('section head sets a uniform amount with one override, sees before/after and remaining, confirms', async ({
  page,
}) => {
  await loginAs(page, 'dana@corp.example');
  await page.goto('/v2/group/1');
  await page.getByRole('button', { name: 'הקצאה קבוצתית' }).click();
  await page.getByLabel('סכום אחיד לכל החברים').fill('1000');
  await page.getByLabel('חריגה לעובד אחד').selectOption({ label: 'dani' });
  await page.getByLabel('סכום החריגה').fill('2000');
  await page.getByRole('button', { name: 'תצוגה מקדימה' }).click();
  await expect(page.getByText(/נשאר להקצאה/)).toBeVisible();
  await page.getByLabel('סיבת השינוי').fill('הקצאה אחידה');
  await page.getByRole('button', { name: 'אישור הקצאה' }).click();
  await expect(page.getByRole('status')).toContainText('מחליף את התקרה הקיימת');
});
