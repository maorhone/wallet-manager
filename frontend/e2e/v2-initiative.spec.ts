import { expect, loginAs, resetData, test } from './fixtures';
test.beforeAll(() => resetData());
test('owner sees initiative usage read-only and opens a change request', async ({ page }) => {
  await loginAs(page, 'dana@corp.example');
  const csrf = (await page.context().cookies()).find((c) => c.name === 'wm_csrf')?.value ?? '';
  const created = await page.request.post('/groups/1/initiatives', {
    data: { name: 'Pilot', owner_upn: 'dani@corp.example', budget: 1000 },
    headers: { 'X-CSRF-Token': csrf, 'X-Change-Reason': 'e2e', 'Idempotency-Key': 'init-e2e-1' },
  });
  expect(created.ok()).toBeTruthy();
  await loginAs(page, 'dani@corp.example');
  await page.goto('/v2/systems');
  await expect(page.getByRole('heading', { name: 'Pilot' })).toBeVisible();
  await expect(page.getByText('תצוגה בלבד. לשינוי יש להגיש בקשה.')).toBeVisible();
  await page.getByRole('link', { name: 'בקשת שינוי' }).click();
  await expect(page).toHaveURL(/\/v2\/requests/);
});
