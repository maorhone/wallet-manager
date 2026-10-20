import type { Page } from '@playwright/test';
import { expect, loginAs, resetData, test } from './fixtures';

const PAZIT = 'pazit@corp.example';
const LOST = 'lost@corp.example';

test.beforeEach(() => resetData());

async function departmentId(page: Page) {
  const me = await (await page.request.get('/me')).json();
  return me.scopes.find((s: { kind: string }) => s.kind === 'department').id as number;
}

async function groupId(page: Page, dept: number, name: string) {
  const s = await (await page.request.get(`/departments/${dept}/summary`)).json();
  return s.groups.find((g: { name: string }) => g.name === name).id as number;
}

async function place(page: Page, upn: string, group: RegExp) {
  const row = page.locator(`[data-row="${upn}"]`);
  await row.getByTestId('place').click();
  await page.getByTestId('move-target').click();
  await page.getByRole('option', { name: group }).click();
  await page.getByTestId('action-reason').fill('שיבוץ ראשוני');
  await page.getByTestId('confirm-action').click();
  await expect(row).toHaveCount(0, { timeout: 15_000 });
}

async function expectProvisioned(page: Page, group: number, upn: string) {
  await page.goto(`/group/${group}/members`);
  const member = page.locator(`[data-row="${upn}"]`);
  await expect(member).toBeVisible();
  await expect(member.getByTestId('sync-badge')).toHaveAttribute('data-status', 'applied', {
    timeout: 60_000,
  });
}

test('U12 rep places a pending user of the department', async ({ page }) => {
  await loginAs(page, 'rep@corp.example');
  const dept = await departmentId(page);
  const a1 = await groupId(page, dept, 'Group A1');
  await page.goto(`/department/${dept}/pending`);
  await expect(page.getByTestId('pending-table')).toBeVisible();
  await expect(page.locator(`[data-row="${LOST}"]`)).toHaveCount(0); // unknown department
  await place(page, PAZIT, /Group A1/);
  await expectProvisioned(page, a1, PAZIT);
});

test('U12 admin places an unknown-department user from the platform view', async ({ page }) => {
  await loginAs(page, 'admin@corp.example');
  await page.goto('/platform');
  await expect(page.locator(`[data-row="${PAZIT}"]`)).toBeVisible();
  await expect(page.locator(`[data-row="${LOST}"]`)).toBeVisible();
  await place(page, LOST, /Group C1/);
  await expect(page.locator(`[data-row="${LOST}"]`)).toHaveCount(0);
  const depts = await (await page.request.get('/departments')).json();
  const d2 = depts.find((d: { name: string }) => d.name === 'Department D2').id as number;
  await expectProvisioned(page, await groupId(page, d2, 'Group C1'), LOST);
});

test('U12 group head has no pending tab and the API returns 404', async ({ page }) => {
  await loginAs(page, 'dana@corp.example');
  const me = await (await page.request.get('/me')).json();
  const a1 = me.scopes.find((s: { kind: string }) => s.kind === 'group').id as number;
  const dept = 1; // Department D1 (seed order); the head has no role on it
  await page.goto(`/group/${a1}/overview`);
  await expect(page.getByTestId('wallet-card')).toBeVisible();
  expect((await page.request.get(`/departments/${dept}/pending-placements`)).status()).toBe(404);
  await page.goto(`/department/${dept}/pending`);
  await expect(page.getByTestId('pending-table')).toHaveCount(0);
});
