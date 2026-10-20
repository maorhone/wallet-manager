import type { Page } from '@playwright/test';
import { expect, loginAs, resetData, test } from './fixtures';

test.beforeAll(() => resetData());

async function write(page: Page, path: string, data: unknown) {
  const csrf = (await page.context().cookies()).find((c) => c.name === 'wm_csrf')?.value ?? '';
  return page.request.put(path, {
    data,
    headers: { 'X-CSRF-Token': csrf, 'X-Change-Reason': encodeURIComponent('e2e') },
  });
}

test('U7 viewer sees no action buttons and write endpoints return 403', async ({ page }) => {
  await loginAs(page, 'viewer@corp.example');
  const me = await (await page.request.get('/me')).json();
  const dept = me.scopes.find((s: { kind: string }) => s.kind === 'department').id;
  const groups = await (await page.request.get(`/departments/${dept}/summary`)).json();
  const a1 = groups.groups.find((g: { name: string }) => g.name === 'Group A1').id;

  await page.goto(`/department/${dept}/split`);
  await expect(page.getByTestId('allocation-table')).toBeVisible();
  await expect(page.locator('[data-testid^="alloc-input-"]')).toHaveCount(0);
  await expect(page.getByTestId('alloc-save')).toHaveCount(0);
  await expect(page.getByTestId('budget-edit')).toHaveCount(0);

  // Group page: Members tab is hidden (D11) and nothing is editable.
  await page.goto(`/group/${a1}/overview`);
  await expect(page.getByTestId('wallet-card')).toBeVisible();
  await expect(page.getByTestId('tab-members')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /שמירה|סקירת שינויים/ })).toHaveCount(0);

  const rows = groups.split.groups.map((g: { id: number; base: number }) => ({
    group_id: g.id,
    base: g.base,
  }));
  const calls = [
    write(page, `/departments/${dept}/split`, {
      groups: rows,
      pmo_pool: groups.split.pmo_pool,
      reserve: groups.split.reserve,
    }),
    write(page, `/groups/${a1}/model-pots`, {}),
    write(page, `/groups/${a1}/members/caps`, [{ upn: 'dani@corp.example', base_cap: 1 }]),
    write(page, `/departments/${dept}/budget`, { budget: 1, budget_period: '1mo' }),
  ];
  for (const res of await Promise.all(calls)) expect(res.status()).toBe(403);
});
