import type { Page } from '@playwright/test';
import { apiAs, expect, loginAs, resetData, test } from './fixtures';

test.beforeEach(() => resetData());

async function departmentId(page: Page): Promise<number> {
  return (await (await page.request.get('/departments')).json())[0].id;
}

const PMO = 'pmo@corp.example';
const inDays = (n: number) => new Date(Date.now() + n * 86_400_000).toLocaleDateString('en-CA');

async function available(page: Page): Promise<number> {
  const el = page.getByTestId('pool-available');
  await expect(el).toBeVisible();
  return Number(await el.getAttribute('data-value'));
}

async function walkTo(page: Page, step: 'amount' | 'dates', amount: string) {
  await page.getByTestId('create-grant').click();
  await page.getByTestId('wizard-project').fill('PRJ-E2E');
  await page.getByTestId('wizard-purpose').fill('Pilot');
  await page.getByTestId('wizard-next').click();
  await page.getByTestId('wizard-group').selectOption({ label: 'Group A1' });
  await expect(page.getByTestId('wizard-group-state')).toBeVisible();
  await page.getByTestId('wizard-next').click();
  if (step === 'amount') return;
  await page.getByTestId('wizard-amount').fill(amount);
  await page.getByTestId('wizard-next').click();
}

test('PMO creates a grant through the wizard and pool available drops', async ({ page }) => {
  await loginAs(page, PMO);
  const dept = await departmentId(page);
  await page.goto(`/pmo/${dept}/pool`);
  const before = await available(page);

  await page.goto(`/pmo/${dept}/grants`);
  await walkTo(page, 'dates', '1000');
  await page.getByTestId('wizard-expiry').fill(inDays(30));
  await page.getByTestId('wizard-next').click();
  await expect(page.getByTestId('change-summary')).toContainText('PRJ-E2E');
  await page.getByTestId('change-reason').fill('פיילוט');
  await page.getByTestId('confirm-change').click();
  await expect(page.getByTestId('grant-list')).toContainText('PRJ-E2E');

  await page.getByTestId('tab-pool').click();
  await expect.poll(() => available(page)).toBe(before - 1000);
});

test('A11 PMO amount above pool is blocked and the race returns PMO_POOL_INSUFFICIENT', async ({
  page,
}) => {
  await loginAs(page, PMO);
  const dept = await departmentId(page);
  await page.goto(`/pmo/${dept}/pool`);
  const pool = await available(page);

  await page.goto(`/pmo/${dept}/grants`);
  await walkTo(page, 'amount', '');
  await page.getByTestId('wizard-amount').fill(String(pool + 1));
  await expect(page.getByTestId('wizard-amount-error')).toBeVisible();
  await expect(page.getByTestId('wizard-next')).toBeDisabled();

  // Valid in the UI, then someone else takes most of the pool before the confirm.
  await page.getByTestId('wizard-amount').fill(String(pool - 100));
  await page.getByTestId('wizard-next').click();
  await page.getByTestId('wizard-expiry').fill(inDays(30));
  const api = await apiAs(PMO);
  const summary = await (await api.get(`/departments/${dept}/summary`)).json();
  const res = await api.post('/grants', {
    group_id: summary.groups[0].id,
    amount: pool - 50,
    starts_at: new Date(Date.now() - 60_000).toISOString(),
    expires_at: new Date(Date.now() + 30 * 86_400_000).toISOString(),
    project_ref: 'PRJ-RACE',
    purpose: 'race',
  });
  expect(res.ok()).toBeTruthy();
  await api.dispose();

  await page.getByTestId('wizard-next').click();
  await page.getByTestId('change-reason').fill('מרוץ');
  await page.getByTestId('confirm-change').click();
  await expect(page.getByTestId('summary-error')).toContainText('בקופת גוף הפרויקטים זמינים');
});
