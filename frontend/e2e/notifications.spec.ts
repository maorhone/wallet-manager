import { apiAs, expect, loginAs, resetData, test } from './fixtures';

test.beforeEach(() => resetData());

const PMO = 'pmo@corp.example';
const DANA = 'dana@corp.example';
const inDays = (n: number) => new Date(Date.now() + n * 86_400_000).toISOString();

test('Group head sees grant_received, marks it read, mutes grant_expiring; critical kinds cannot be muted', async ({
  page,
}) => {
  // The PMO grants to Dana's group, which notifies the group head.
  const me = await apiAs(DANA);
  const gid = (await (await me.get('/me')).json()).scopes.find(
    (s: { kind: string }) => s.kind === 'group',
  ).id;
  await me.dispose();
  const pmo = await apiAs(PMO);
  const res = await pmo.post('/grants', {
    group_id: gid,
    project_ref: 'PRJ-N1',
    purpose: 'Pilot',
    amount: 500,
    starts_at: new Date(Date.now() - 60_000).toISOString(),
    expires_at: inDays(30),
  });
  expect(res.ok()).toBeTruthy();
  await pmo.dispose();

  await loginAs(page, DANA);
  await page.goto('/wallet');
  await expect(page.getByTestId('bell-count')).toHaveText('1');
  await page.getByTestId('bell').click();
  const item = page.locator('[data-testid^="notification-"][data-kind="grant_received"]');
  await expect(item).toContainText('PRJ-N1');
  await expect(item.getByRole('link')).toHaveAttribute('href', `/group/${gid}/grants`);
  await item.getByTestId('notification-read').click();
  await expect(page.getByTestId('bell-count')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);

  // Preferences: non-critical kinds can be muted, critical ones are disabled.
  const prefs = page.getByTestId('notification-prefs');
  await expect(prefs.getByTestId('pref-employee_100')).toBeDisabled();
  await expect(prefs.getByTestId('pref-employee_100')).toBeChecked();
  const expiring = prefs.getByTestId('pref-grant_expiring');
  await expect(expiring).toBeEnabled();
  await expiring.click(); // the state flips once the server confirms
  await expect(expiring).not.toBeChecked();
  await page.reload();
  await expect(page.getByTestId('pref-grant_expiring')).not.toBeChecked();
  await expect(page.getByTestId('pref-grant_received')).toBeChecked();
});
