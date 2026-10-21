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

test('Group head sees the group usage chart stacked by model and switches to by member; a viewer has no by-member option', async ({
  page,
}) => {
  await loginAs(page, 'dana@corp.example');
  const me = await (await page.request.get('/me')).json();
  const group = me.group.id;
  const period = me.current_period;
  await fakeSpend([
    {
      team_id: 'A1',
      user_id: 'dani@corp.example',
      model: 'usage-model-a',
      amount: 12,
      at: `${period}-01T12:00:00Z`,
    },
    {
      team_id: 'A1',
      user_id: 'dani@corp.example',
      model: 'usage-model-b',
      amount: 8,
      at: `${period}-01T12:00:00Z`,
    },
  ]);
  // Wait for the snapshot worker before mounting the cached usage query.
  await expect
    .poll(async () => {
      const response = await page.request.get(
        `/reports/usage?scope=group&id=${group}&from=${period}-01&to=${period}-01`,
      );
      const report = await response.json();
      return report.series?.reduce((sum: number, p: { spend: number }) => sum + p.spend, 0);
    })
    .toBe(20);
  await page.goto(`/group/${group}/overview`);
  await expect(page.getByTestId('usage-chart')).toContainText('usage-model-a');
  await page.getByRole('link', { name: 'דוחות', exact: true }).click();
  await page.getByRole('tab', { name: 'שימוש', exact: true }).click();
  const chart = page.getByTestId('usage-chart');
  await expect(chart).toContainText('usage-model-a');
  await expect(chart).toContainText('usage-model-b');
  await expect(chart.locator('.recharts-reference-line')).toHaveCount(1);
  await page.getByLabel('קיבוץ שימוש').selectOption('member');
  await expect(chart).toContainText('dani@corp.example');
  await expect(chart).not.toContainText('usage-model-a');
  await page.getByLabel('רזולוציה').selectOption('week');
  await expect(chart).toContainText('dani@corp.example');
  await page.getByLabel('רזולוציה').selectOption('month');
  await expect(chart).toContainText('dani@corp.example');
  await loginAs(page, 'viewer@corp.example');
  await page.goto(`/department/${me.department.id}/overview`);
  await expect(page.getByTestId('usage-chart')).toContainText('usage-model-a');
  await page.getByRole('link', { name: 'דוחות', exact: true }).click();
  await page.getByRole('tab', { name: 'שימוש', exact: true }).click();
  await expect(page.getByLabel('קיבוץ שימוש')).toHaveCount(0);
  await expect(chart).toContainText('usage-model-a');
  await expect(chart).not.toContainText('dani@corp.example');
});
