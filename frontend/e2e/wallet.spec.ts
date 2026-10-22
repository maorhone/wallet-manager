import { expect, fake, loginAs, resetData, test } from './fixtures';

test.beforeAll(() => resetData());

test('wallet shows limits and owners', async ({ page }) => {
  await loginAs(page, 'dani@corp.example');
  await page.goto('/wallet');
  await expect(page.getByTestId('wallet-card')).toContainText('15,000');
  await expect(page.getByTestId('wallet-card')).toContainText('מוגבל על ידי');
  await expect(page.getByTestId('limits-box')).toContainText('בקשות במקביל');
  await expect(page.getByTestId('limits-box')).toContainText('429');
  await expect(page.getByTestId('owners-box')).toContainText('Dana Levi');
  await expect(page.getByTestId('shares-table')).toContainText('אין שיתופי פרויקטים');
  await expect(page.getByTestId('group-context')).toBeVisible();
  await expect(page.getByTestId('usage-chart')).toBeVisible();
});

// QUARANTINED 22/10: flaky under load (toContainText timeout in a 19-min full run), see OPEN-ITEMS. Restore test() once root-caused.
test.fixme('U4 group exhausted shows "חסום: תקציב הקבוצה נוצל" and names the group head', async ({
  page,
}) => {
  // A1's whole base budget is spent by yael; dani's own spend stays low.
  await fake('/_test/spend', {
    team_id: 'A1',
    user_id: 'yael@corp.example',
    model: 'gpt-4',
    amount: 250000,
  });
  await loginAs(page, 'dani@corp.example');
  await page.goto('/wallet');
  // The snapshot loop runs every 2 s in the E2E stack.
  await expect(page.getByTestId('wallet-card')).toContainText('חסום: תקציב הקבוצה נוצל', {
    timeout: 30_000,
  });
  await expect(page.getByTestId('blocked-contact')).toContainText('Dana Levi');
});
