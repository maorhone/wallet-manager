import type { Page } from '@playwright/test';
import { expect, fake, loginAs, resetData, test } from './fixtures';

test.beforeEach(() => resetData());
test.afterEach(async () => {
  await fake('/_test/outage', { on: false });
});

export async function departmentId(page: Page): Promise<number> {
  return (await (await page.request.get('/departments')).json())[0].id;
}

const input = (page: Page, key: string) => page.getByTestId(`alloc-input-${key}`);

async function openSplit(page: Page, upn = 'rep@corp.example') {
  await loginAs(page, upn);
  const id = await departmentId(page);
  await page.goto(`/department/${id}/split`);
  await expect(page.getByTestId('allocation-table')).toBeVisible();
  return id;
}

test('F1 split save pending then applied', async ({ page }) => {
  await openSplit(page);
  await expect(page.locator('[data-row="reserve"]')).toBeVisible();
  await expect(page.locator('[data-row="pmo"]')).toBeVisible();
  await fake('/_test/outage', { on: true });
  // Move 10,000 from group B1 to the reserve: the total stays within the budget.
  const b1 = page.locator('tr', { hasText: 'Group B1' });
  await b1.locator('input').fill('340000');
  await input(page, 'reserve').fill('60000');
  await page.getByTestId('alloc-save').click();
  await expect(page.getByTestId('change-summary')).toContainText('רזרבה');
  await page.getByTestId('change-reason').fill('העברה לרזרבה');
  await page.getByTestId('confirm-change').click();
  const badge = b1.getByTestId('sync-badge');
  await expect(badge).toHaveAttribute('data-status', 'pending');
  await fake('/_test/outage', { on: false });
  await expect(badge).toHaveAttribute('data-status', 'applied', { timeout: 60_000 });
  await expect(input(page, 'reserve')).toHaveValue('60000');
});

test('split over budget footer red and disabled', async ({ page }) => {
  await openSplit(page);
  await expect(page.getByTestId('alloc-footer')).toHaveAttribute('data-negative', 'false');
  await input(page, 'reserve').fill('90000');
  await expect(page.getByTestId('alloc-footer')).toHaveAttribute('data-negative', 'true');
  await expect(page.getByTestId('alloc-save')).toBeDisabled();
});

test('drill-down group is read-only for rep', async ({ page }) => {
  await loginAs(page, 'rep@corp.example');
  const id = await departmentId(page);
  await page.goto(`/department/${id}/overview`);
  await expect(page.getByTestId('wallet-card')).toBeVisible();
  await expect(page.getByTestId('split-chart')).toBeVisible();
  await page.getByRole('link', { name: /Group A1/ }).click();
  await expect(page).toHaveURL(/\/group\/\d+\/overview/);
  await page.getByTestId('tab-members').click();
  await expect(page.getByTestId('allocation-table')).toBeVisible();
  // The rep owns only the group heads' values: ordinary members are not editable.
  await expect(input(page, 'dani@corp.example')).toHaveCount(0);
  await expect(page.getByTestId('alloc-owner-dani@corp.example')).toBeVisible();
});

test('admin edits the budget inline on Split', async ({ page }) => {
  await openSplit(page, 'admin@corp.example');
  await expect(page.getByTestId('budget-edit')).toBeVisible();
  await page.getByTestId('budget-input').fill('1100000');
  await page.getByTestId('budget-save').click();
  await page.getByTestId('change-reason').fill('הגדלת תקציב');
  await page.getByTestId('confirm-change').click();
  await expect(page.getByTestId('alloc-footer')).toContainText('1,100,000', { timeout: 15_000 });
});
