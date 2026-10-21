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

test('PMO sees grant totals by project; the representative sees blocked employees per day', async ({
  page,
}) => {
  await loginAs(page, 'pmo@corp.example');
  const me = await (await page.request.get('/me')).json();
  const dept = me.department.id;
  const period = me.current_period;
  const pmo = await apiAs('pmo@corp.example');
  const summary = await (await pmo.get(`/departments/${dept}/summary`)).json();
  const group = summary.groups.find((g: { name: string }) => g.name === 'Group A1').id;
  for (const amount of [1200, 300]) {
    expect(
      (
        await pmo.post('/grants', {
          group_id: group,
          amount,
          project_ref: 'PRJ-REPORT',
          purpose: 'report',
          starts_at: new Date(Date.now() - 60_000).toISOString(),
          expires_at: new Date(Date.now() + 86400000).toISOString(),
        })
      ).ok(),
    ).toBeTruthy();
  }
  const grants = await (await pmo.get(`/grants?department=${dept}`)).json();
  const closed = grants.find((g: { amount: number }) => g.amount === 300);
  expect((await pmo.post(`/grants/${closed.id}/close`, {})).ok()).toBeTruthy();
  await pmo.dispose();
  await page.goto(`/pmo/${dept}/pool`);
  await page.getByRole('link', { name: 'דוחות', exact: true }).click();
  await expect(page.getByTestId('scope-switcher')).toHaveValue(`pmo:${dept}`);
  const project = page.getByTestId('grants-report').locator('tbody tr', { hasText: 'PRJ-REPORT' });
  await expect(project).toBeVisible();
  await expect(project.locator('td').nth(3)).toHaveText('2');
  await expect(project.locator('td').nth(4)).toHaveText('1,500');
  await expect(project.locator('td').nth(5)).toHaveText('0');
  await expect(project.locator('td').nth(7)).toHaveText('0');
  await expect(project.locator('td').nth(8)).toHaveText('300');
  await expect(page.getByTestId('grants-report').locator('tfoot')).toContainText('1,500');
  await expect(page.getByTestId('grants-report').locator('tfoot')).toContainText('300');
  await expect(page.getByRole('tab', { name: 'חסומים', exact: true })).toHaveCount(0);
  await expect(page.getByRole('tab', { name: 'הקצאה וניצול', exact: true })).toHaveCount(0);

  const admin = await apiAs('admin@corp.example');
  expect(
    (
      await admin.put(`/groups/${group}/members/caps`, [
        { upn: 'dani@corp.example', base_cap: 100 },
      ])
    ).ok(),
  ).toBeTruthy();
  expect(
    (await admin.put(`/departments/${dept}/model-allowances`, { 'blocked-model': 1000 })).ok(),
  ).toBeTruthy();
  expect(
    (await admin.put(`/groups/${group}/model-pots`, { 'blocked-model': 1000 })).ok(),
  ).toBeTruthy();
  expect(
    (
      await admin.put(`/groups/${group}/members/dani@corp.example/model-caps`, {
        'blocked-model': 40,
      })
    ).ok(),
  ).toBeTruthy();
  await admin.dispose();
  await fakeSpend([
    {
      team_id: 'A1',
      user_id: 'dani@corp.example',
      model: 'blocked-model',
      amount: 100,
      at: `${period}-01T12:00:00Z`,
    },
  ]);
  await loginAs(page, 'rep@corp.example');
  await expect
    .poll(async () => {
      const report = await (
        await page.request.get(
          `/reports/blocked?scope=department&id=${dept}&from=${period}-01&to=${period}-01`,
        )
      ).json();
      return report.rows?.filter((r: { upn: string }) => r.upn === 'dani@corp.example').length;
    })
    .toBe(2);
  await page.goto(`/department/${dept}/overview`);
  await page.getByRole('link', { name: 'דוחות', exact: true }).click();
  await page.getByRole('tab', { name: 'חסומים', exact: true }).click();
  const rows = page
    .getByTestId('blocked-report')
    .locator('tbody tr', { hasText: 'dani@corp.example' });
  const firstDay = rows.filter({ hasText: `${period}-01` });
  await expect(firstDay).toHaveCount(2);
  await expect(firstDay.filter({ hasText: 'blocked-model' })).toContainText('40');
  await expect(firstDay.filter({ hasText: 'מכסת בסיס' })).toContainText('100');
  await expect(firstDay.first().locator('td').nth(5)).toHaveText('100');
  await expect(firstDay.first().locator('td').nth(6)).toHaveText('100');
  const [year, month] = period.split('-').map(Number);
  const previous = new Date(Date.UTC(year, month - 2, 1)).toISOString().slice(0, 7);
  await page.getByTestId('period-selector').selectOption(previous);
  await expect(page.getByTestId('blocked-report').getByTestId('empty-state')).toBeVisible();
  await loginAs(page, 'viewer@corp.example');
  await page.goto(`/report/department/${dept}/allocation`);
  await expect(page.getByRole('tab', { name: 'חסומים', exact: true })).toHaveCount(0);
});
