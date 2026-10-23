import { expect, loginAs, resetData, test } from './fixtures';
test.beforeAll(() => resetData());
test('employee sees only personal items', async ({ page }) => {
  await page.goto('/login');
  await page.getByTestId('dev-user').click();
  await page.getByRole('option', { name: /avi@corp.example/ }).click();
  await page.getByRole('button', { name: 'התחברות למשתמש בדיקה' }).click();
  const nav = page.getByRole('navigation', { name: 'אזורי עבודה' });
  await expect(nav.getByRole('link', { name: 'ה-AI שלי', exact: true })).toBeVisible();
  await expect(nav.getByRole('link', { name: 'בקשות', exact: true })).toBeVisible();
  await expect(
    nav.getByRole('link', { name: /ניהול תקציב|תקציב ומערכות|הקצאה ארגונית|מערכות באחריותנו/ }),
  ).toHaveCount(0);
  await expect(page).toHaveURL(/\/v2\/personal$/);
  await page.goto('/v2/organization');
  await expect(page).toHaveURL(/\/v2\/personal$/);
  await expect(page.getByRole('link', { name: 'הארנק שלי', exact: true })).toBeVisible();
});
test('section head sees budget items across accumulated scopes', async ({ page }) => {
  await loginAs(page, 'noa@corp.example');
  await page.goto('/v2');
  const nav = page.getByRole('navigation', { name: 'אזורי עבודה' });
  await expect(nav.getByRole('link', { name: /ניהול תקציב - מדור/ })).toBeVisible();
  await expect(nav.getByRole('link', { name: /תקציב ומערכות - מחלקה/ })).toBeVisible();
  await expect(nav.getByRole('link', { name: /הקצאה ארגונית/ })).toHaveCount(0);
  await nav.getByRole('link', { name: /ניהול תקציב - מדור/ }).click();
  await expect(page.getByRole('heading', { name: /ניהול תקציב - מדור/ })).toBeVisible();
});
test('last area restored per user and removed scope falls back safely', async ({ page }) => {
  await loginAs(page, 'noa@corp.example');
  await page.goto('/v2');
  await page
    .getByRole('navigation')
    .getByRole('link', { name: /ניהול תקציב - מדור/ })
    .click();
  const remembered = page.url();
  await page.goto('/v2');
  await expect(page).toHaveURL(remembered);
  await page.evaluate(() =>
    localStorage.setItem('wm.v2.area.noa@corp.example', '/v2/group/999999'),
  );
  await page.goto('/v2');
  await expect(page).toHaveURL(/\/v2\/personal$/);
  await loginAs(page, 'avi@corp.example');
  await page.goto('/v2');
  await expect(page).toHaveURL(/\/v2\/personal$/);
  await page.evaluate(() =>
    localStorage.setItem('wm.v2.area.avi@corp.example', 'https://corp.example'),
  );
  await page.goto('/v2');
  await expect(page).toHaveURL(/\/v2\/personal$/);
});
