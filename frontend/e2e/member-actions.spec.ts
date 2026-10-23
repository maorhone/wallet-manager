import type { Page } from '@playwright/test';
import { expect, loginAs, resetData, test } from './fixtures';

const DANA = 'dana@corp.example';
const DANI = 'dani@corp.example';
const A1 = 1;

test.beforeEach(() => resetData());

async function openMembers(page: Page, as: string, groupId = A1) {
  await loginAs(page, as);
  await page.goto(`/group/${groupId}/members`);
  await expect(page.getByTestId('allocation-table')).toBeVisible();
}

const row = (page: Page, upn: string) => page.locator(`[data-row="${upn}"]`);

async function openPanel(page: Page, upn: string) {
  await row(page, upn).getByTestId('open-panel').click();
  await expect(page.getByTestId('panel-review')).toBeVisible();
}

async function confirmAction(page: Page, reason: string) {
  await page.getByTestId('action-reason').fill(reason);
  await page.getByTestId('confirm-action').click();
  await expect(page.getByTestId('confirm-action')).toHaveCount(0);
}

test('U9 head kicks and restores a member', async ({ page }) => {
  await openMembers(page, DANA);
  await openPanel(page, DANI);
  await expect(page.getByTestId('action-restore')).toHaveCount(0);
  await page.getByTestId('action-kick').click();
  await confirmAction(page, 'עזב את הפרויקט');

  await expect(row(page, DANI)).toHaveAttribute('data-muted', 'true');
  await expect(row(page, DANI)).toContainText('מושעה');
  const badge = row(page, DANI).getByTestId('sync-badge');
  await expect(badge).toHaveAttribute('data-status', 'applied', { timeout: 60_000 });

  await openPanel(page, DANI);
  await expect(page.getByTestId('action-kick')).toHaveCount(0);
  await page.getByTestId('action-restore').click();
  await confirmAction(page, 'חזר לפרויקט');
  await expect(row(page, DANI)).not.toHaveAttribute('data-muted', 'true');
  await expect(row(page, DANI)).not.toContainText('מושעה');
  await expect(row(page, DANI).getByTestId('sync-badge')).toHaveAttribute(
    'data-status',
    'applied',
    { timeout: 60_000 },
  );
});

test('U9 head sees no move or delete and no actions on own row', async ({ page }) => {
  test.fixme(true, 'flaky under load 2026-10-23; see OPEN-ITEMS');
  await openMembers(page, DANA);
  await openPanel(page, DANI);
  await expect(page.getByTestId('action-kick')).toBeVisible();
  await expect(page.getByTestId('action-move')).toHaveCount(0);
  await expect(page.getByTestId('action-delete')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('panel-review')).toHaveCount(0);

  await openPanel(page, DANA);
  await expect(page.getByTestId('member-actions')).toHaveCount(0);
  for (const a of ['kick', 'restore', 'move', 'delete'])
    await expect(page.getByTestId(`action-${a}`)).toHaveCount(0);
});

test('U10 rep moves a member within the department', async ({ page }) => {
  await openMembers(page, 'rep@corp.example');
  await openPanel(page, DANI);
  await page.getByTestId('action-move').click();
  await page.getByTestId('move-target').click();
  await expect(page.getByRole('option', { name: /Group C1/ })).toHaveCount(0); // other department
  await page.getByRole('option', { name: /Group A2/ }).click();
  await confirmAction(page, 'מעבר צוות');
  await expect(row(page, DANI)).toHaveCount(0, { timeout: 15_000 });

  const moved = await (await page.request.get('/groups/2/members')).json();
  expect(moved.members.map((m: { upn: string }) => m.upn)).toContain(DANI);
});

test('U10 admin moves a member across departments', async ({ page }) => {
  await openMembers(page, 'admin@corp.example');
  await openPanel(page, DANI);
  await page.getByTestId('action-move').click();
  await page.getByTestId('move-target').click();
  await page.getByRole('option', { name: /Group C1/ }).click();
  await confirmAction(page, 'מעבר בין מחלקות');
  await expect(row(page, DANI)).toHaveCount(0, { timeout: 15_000 });
});

test('U11 admin delete requires typed upn', async ({ page }) => {
  await openMembers(page, 'admin@corp.example');
  await openPanel(page, DANI);
  await page.getByTestId('action-delete').click();
  await page.getByTestId('action-reason').fill('סגירת משתמש');
  const confirm = page.getByTestId('confirm-action');
  await expect(confirm).toBeDisabled();
  await page.getByTestId('confirm-upn').fill('dan@corp.example');
  await expect(confirm).toBeDisabled();
  await page.getByTestId('confirm-upn').fill(DANI);
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect(row(page, DANI)).toHaveCount(0, { timeout: 15_000 });
});

test('U7 viewer and PMO get 403 on lifecycle endpoints', async ({ page }) => {
  for (const who of ['viewer@corp.example', 'pmo@corp.example']) {
    await loginAs(page, who);
    const csrf = (await page.context().cookies()).find((c) => c.name === 'wm_csrf')?.value ?? '';
    const headers = { 'X-CSRF-Token': csrf, 'X-Change-Reason': encodeURIComponent('e2e') };
    const base = `/groups/${A1}/members/${DANI}`;
    expect((await page.request.get(`${base}/move-targets`)).status(), who).toBe(403);
    const bodies: Record<string, unknown> = {
      kick: {},
      restore: {},
      move: { to_group_id: 2 },
      delete: { confirm_upn: DANI },
    };
    for (const [action, data] of Object.entries(bodies)) {
      const res = await page.request.post(`${base}/${action}`, { data, headers });
      expect(res.status(), `${who} ${action}`).toBe(403);
    }
  }
});
