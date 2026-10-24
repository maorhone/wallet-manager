import { expect, loginAs, resetData, test } from './fixtures';
test.beforeAll(() => resetData());
test('employee submits throughput; section head forwards; department head forwards; AI team applies; requester sees applied', async ({
  page,
}) => {
  await loginAs(page, 'dani@corp.example');
  await page.goto('/v2/requests');
  await page.getByLabel('סוג').last().selectOption('throughput');
  await page.getByLabel('מקביליות מבוקשת').fill('4');
  await page.getByLabel('תיאור הצורך').fill('עיבוד אצווה');
  await page.getByRole('button', { name: 'שליחת בקשה' }).click();
  await expect(page.getByText('הבקשה נשלחה.')).toBeVisible();
  for (const [upn, action] of [
    ['dana@corp.example', 'העברה להמשך טיפול'],
    ['rep@corp.example', 'העברה להמשך טיפול'],
    ['aiteam@corp.example', 'יישום אצל צוות ה-AI'],
  ]) {
    await loginAs(page, upn);
    await page.goto('/v2/requests');
    await page.getByRole('tab', { name: 'לטיפולי' }).click();
    await page
      .getByRole('button', { name: /#\d+ Throughput/ })
      .first()
      .click();
    await page.getByRole('button', { name: action }).click();
    await expect(page.getByText('הטיפול נשמר.')).toBeVisible();
  }
  await loginAs(page, 'dani@corp.example');
  await page.goto('/v2/requests');
  await expect(page.getByRole('row').first()).toContainText(/יושמה|ממתינה ליישום/);
});
