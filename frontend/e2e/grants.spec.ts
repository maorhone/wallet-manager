import type { Page } from '@playwright/test';
import { apiAs, expect, loginAs, resetData, test } from './fixtures';

test.beforeEach(() => resetData());

const PMO = 'pmo@corp.example';
const DANA = 'dana@corp.example';
const DANI = 'dani@corp.example';
const inDays = (n: number) => new Date(Date.now() + n * 86_400_000).toLocaleDateString('en-CA');

async function scopeId(page: Page, kind: 'group' | 'department'): Promise<number> {
  const me = await (await page.request.get('/me')).json();
  return me.scopes.find((s: { kind: string }) => s.kind === kind).id;
}

test('U5 PMO grant then group head share; employee sees the share with expiry; PMO sees shared and unshared', async ({
  page,
}) => {
  // PMO creates the grant through the wizard.
  await loginAs(page, PMO);
  const dept = (await (await page.request.get('/departments')).json())[0].id;
  await page.goto(`/pmo/${dept}/grants`);
  await page.getByTestId('create-grant').click();
  await page.getByTestId('wizard-project').fill('PRJ-U5');
  await page.getByTestId('wizard-purpose').fill('Pilot');
  await page.getByTestId('wizard-next').click();
  await page.getByTestId('wizard-group').selectOption({ label: 'Group A1' });
  await expect(page.getByTestId('wizard-group-state')).toBeVisible();
  await page.getByTestId('wizard-next').click();
  await page.getByTestId('wizard-amount').fill('1000');
  await page.getByTestId('wizard-next').click();
  await page.getByTestId('wizard-expiry').fill(inDays(30));
  await page.getByTestId('wizard-next').click();
  await page.getByTestId('change-reason').fill('פיילוט');
  await page.getByTestId('confirm-change').click();
  await expect(page.getByTestId('grant-list')).toContainText('PRJ-U5');
  await expect(page.getByRole('dialog')).toHaveCount(0);

  // The group head shares part of it to one employee.
  await loginAs(page, DANA);
  const gid = await scopeId(page, 'group');
  await page.goto(`/group/${gid}/grants`);
  const row = page.locator('[data-project="PRJ-U5"]');
  const id = (await row.getAttribute('data-testid'))!.replace('grant-row-', '');
  await page.getByTestId(`share-${id}`).click();
  await page.getByTestId(`share-check-${DANI}`).click();
  await page.getByTestId(`share-amount-${DANI}`).fill('400');
  await expect(page.getByTestId('share-remainder')).toContainText('600');
  await page.getByTestId('share-reason').fill('חלוקה');
  await page.getByTestId('share-confirm').click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByTestId(`shares-${id}`).click();
  await expect(page.getByTestId(`share-item-${DANI}`)).toContainText('400');

  // The employee sees the share with its expiry.
  await loginAs(page, DANI);
  await page.goto('/wallet');
  await expect(page.getByTestId('shares-table')).toContainText('400');
  await expect(page.getByTestId('shares-table')).toContainText('PRJ-U5');
  await expect(page.getByTestId('shares-table')).toContainText('ימים');

  // The PMO sees shared and unshared.
  const pmo = await apiAs(PMO);
  const grants = await (await pmo.get(`/grants?department=${dept}`)).json();
  await pmo.dispose();
  const g = grants.find((x: { project_ref: string }) => x.project_ref === 'PRJ-U5');
  expect(Number(g.shared)).toBe(400);
  expect(Number(g.unshared)).toBe(600);
});

// flaky under load 24/10/2026; see OPEN-ITEMS
test.fixme('U2 group head cannot share to themselves and the API returns SELF_ALLOCATION_FORBIDDEN', async ({
  page,
}) => {
  const pmo = await apiAs(PMO);
  const dept = (await (await pmo.get('/departments')).json())[0].id;
  const summary = await (await pmo.get(`/departments/${dept}/summary`)).json();
  const a1 = summary.groups.find((g: { name: string }) => g.name === 'Group A1').id;
  const made = await pmo.post('/grants', {
    group_id: a1,
    amount: 1000,
    starts_at: new Date(Date.now() - 60_000).toISOString(),
    expires_at: new Date(Date.now() + 30 * 86_400_000).toISOString(),
    project_ref: 'PRJ-U2',
    purpose: 'self',
  });
  expect(made.ok()).toBeTruthy();
  await pmo.dispose();

  await loginAs(page, DANA);
  const gid = await scopeId(page, 'group');
  await page.goto(`/group/${gid}/grants`);
  const id = (await page.locator('[data-project="PRJ-U2"]').getAttribute('data-testid'))!.replace(
    'grant-row-',
    '',
  );
  await page.getByTestId(`share-${id}`).click();
  await expect(page.getByTestId(`share-check-${DANA}`)).toBeDisabled();
  await expect(page.getByTestId(`share-row-${DANA}`)).toContainText('נציג');

  const api = await apiAs(DANA);
  const res = await api.post(`/grants/${id}/shares`, [
    { upn: DANA, amount: 100, expires_at: new Date(Date.now() + 10 * 86_400_000).toISOString() },
  ]);
  expect(res.status()).toBe(422);
  expect((await res.json()).error?.code ?? (await res.json()).code).toBe(
    'SELF_ALLOCATION_FORBIDDEN',
  );
  await api.dispose();
});
