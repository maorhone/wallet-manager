import { apiAs, expect, fakeSpend, loginAs, resetData, test } from './fixtures';

test.beforeEach(() => resetData());

test("Rep opens the department allocation report with group and model rows, and a past period shows that period's values", async ({
  page,
}) => {
  await loginAs(page, 'rep@corp.example');
  const me = await (await page.request.get('/me')).json();
  const current = me.current_period as string;
  const [year, month] = current.split('-').map(Number);
  const previous = new Date(Date.UTC(year, month - 2, 1)).toISOString().slice(0, 7);
  const dept = me.department.id;
  const admin = await apiAs('admin@corp.example');
  expect(
    (
      await admin.put(`/departments/${dept}/budget`, {
        budget: 1100000,
        budget_period: '1mo',
      })
    ).ok(),
  ).toBeTruthy();
  expect(
    (
      await admin.put(`/departments/${dept}/model-allowances`, {
        'report-model': 500000,
      })
    ).ok(),
  ).toBeTruthy();
  await admin.dispose();
  await fakeSpend([
    {
      team_id: 'A1',
      user_id: 'dani@corp.example',
      model: 'report-model',
      amount: 1234,
      at: `${current}-01T12:00:00Z`,
    },
    {
      team_id: 'A1',
      user_id: 'dani@corp.example',
      model: 'report-model',
      amount: 5678,
      at: `${previous}-01T12:00:00Z`,
    },
  ]);
  // Wait for the real snapshot worker, then exercise the report through its scope link.
  await expect
    .poll(
      async () =>
        (
          await (
            await page.request.get(
              `/reports/allocation?level=department&id=${dept}&period=${previous}`,
            )
          ).json()
        ).spend,
    )
    .toBe(5678);
  await page.goto(`/department/${dept}/overview`);
  await page.getByRole('link', { name: 'דוחות', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/report/department/${dept}/allocation$`));
  await expect(page.getByTestId('scope-switcher')).toHaveValue(`department:${dept}`);
  await expect(page.getByTestId('wallet-card')).toContainText('1,100,000');
  await expect(page.getByTestId('wallet-card')).toContainText('1,234');
  await expect(page.getByTestId('wallet-card')).toContainText('עודכן');
  const rows = page.getByTestId('report-allocation-rows');
  await expect(rows).toContainText('Group A1');
  await expect(rows).toContainText('קופת פרויקטים');
  await expect(rows).toContainText('רזרבה');
  await expect(rows.locator('tr', { hasText: 'Group A1' })).toContainText('1,234');
  const models = page.getByTestId('model-table');
  await expect(models).toContainText('report-model');
  await expect(models).toContainText('500,000');
  await expect(models).toContainText('1,234');
  await page.getByTestId('period-selector').selectOption(previous);
  await expect(page.getByTestId('period-selector')).toHaveValue(previous);
  await expect(page.getByText('תקופה עברית: תצוגה בלבד')).toBeVisible();
  await expect(page.getByTestId('wallet-card')).toContainText('1,000,000');
  await expect(page.getByTestId('wallet-card')).toContainText('5,678');
  await expect(models).toContainText('5,678');
  await expect(rows.locator('tr', { hasText: 'Group A1' })).toContainText('5,678');
  await expect(page.locator('main input')).toHaveCount(0);
  await page.getByRole('tab', { name: 'הקצאת יתר', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/overallocation\\?period=${previous}$`));
  // The existing findings API is current-only, even while a past money period is selected.
  await expect(page.getByText('ממצאים עדכניים בלבד; אינם תלויים בתקופה שנבחרה')).toBeVisible();
  await expect(page.getByTestId('overallocation-table')).toContainText('D1 staff');
  await expect(page.getByTestId('overallocation-table')).toContainText('30,000');
  await expect(page.getByTestId('overallocation-table')).toContainText('20,000');
  await page.getByRole('tab', { name: 'הקצאה וניצול', exact: true }).click();
  await expect(page.getByTestId('wallet-card')).toContainText('5,678');
});
