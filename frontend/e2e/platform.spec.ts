import { apiAs, expect, fake, fakeFail, loginAs, resetData, test } from './fixtures';

test.beforeEach(() => resetData());

test('Admin sees Redis not configured, retries a failed outbox row and it turns applied', async ({
  page,
}) => {
  await fake('/_test/spend', {
    team_id: 'A1',
    user_id: 'dani@corp.example',
    model: 'gpt-4',
    amount: 1,
  });
  const admin = await apiAs('admin@corp.example');
  let failedId: number;
  try {
    await fakeFail('/organization/update', 1, 400);
    const write = await admin.put('/departments/1/budget', {
      budget: 1100000,
      budget_period: '1mo',
    });
    expect(write.ok()).toBeTruthy();
    await expect
      .poll(async () => (await (await admin.get('/outbox?status=failed')).json()).length)
      .toBe(1);
    failedId = (await (await admin.get('/outbox?status=failed')).json())[0].id;
    await expect
      .poll(async () => (await (await admin.get('/status')).json()).spend_taken_at)
      .not.toBeNull();
  } finally {
    await admin.dispose();
  }

  // The scheduler's five-minute drift scan is covered by backend tests. Supply its read model
  // here to verify the actor filter and shared timeline without waiting for a scheduler tick.
  await page.route('**/audit?actor=system:reconcile', (route) =>
    route.fulfill({
      json: [
        {
          id: 900001,
          at: new Date().toISOString(),
          actor_upn: 'system:reconcile',
          actor_role: 'platform_admin',
          entity: 'department',
          entity_id: '1',
          before: { max_budget: 900000 },
          after: { max_budget: 1100000 },
          reason: 'Reconcile drift',
          request_id: null,
          litellm_calls: [
            {
              endpoint: '/organization/update',
              status: 'applied',
              attempts: 0,
              last_error: null,
            },
          ],
        },
      ],
    }),
  );
  await loginAs(page, 'admin@corp.example');
  await page.goto('/platform');
  await page.getByRole('tab', { name: 'תפעול', exact: true }).click();
  const status = page.getByTestId('operations-status');
  await expect(status.getByTestId('redis-status')).toHaveText('לא מוגדר');
  await expect(status.getByTestId('litellm-status')).toHaveText('זמין');
  await expect(status.getByTestId('outbox-failed')).toHaveText('1');
  await expect(status.getByTestId('pending-placements')).toHaveText('2');
  await expect(status.getByTestId('spend-freshness')).not.toHaveText('אין נתונים');
  await expect(page.getByTestId('history-timeline')).toContainText('system:reconcile');
  await expect(page.getByTestId('history-timeline')).toContainText('900000 → 1100000');

  const row = page.getByTestId(`outbox-row-${failedId}`);
  await expect(row).toContainText('/organization/update');
  await expect(row.getByTestId('outbox-error')).toContainText('injected failure');
  await expect(row.getByTestId('sync-badge')).toHaveAttribute('data-status', 'failed');
  await row.getByRole('button', { name: 'ניסיון חוזר' }).click();
  await expect(page.getByTestId('confirm-change')).toBeDisabled();
  await page.getByTestId('change-reason').fill('ניסיון חוזר לאחר תיקון התקלה');
  const retried = page.waitForResponse(
    (res) => res.url().endsWith(`/outbox/${failedId}/retry`) && res.request().method() === 'POST',
  );
  await page.getByTestId('confirm-change').click();
  const response = await retried;
  expect(response.ok()).toBeTruthy();
  expect(decodeURIComponent(response.request().headers()['x-change-reason'])).toBe(
    'ניסיון חוזר לאחר תיקון התקלה',
  );
  expect(response.request().headers()['idempotency-key']).toBeTruthy();
  await expect(row).toHaveCount(0);
  await page.getByLabel('סינון לפי מצב').selectOption('applied');
  await expect(row.getByTestId('sync-badge')).toHaveAttribute('data-status', 'applied');
  await expect(row.getByRole('button', { name: 'ניסיון חוזר' })).toHaveCount(0);
  await expect(status.getByTestId('outbox-failed')).toHaveText('0');
  await page.getByLabel('סינון לפי מצב').selectOption('pending');
  await expect(row).toHaveCount(0);
  await page.getByLabel('סינון לפי מצב').selectOption('');
  await expect(row).toBeVisible();

  await loginAs(page, 'rep@corp.example');
  await page.goto('/platform/operations');
  await expect(page.getByText('הגישה לפלטפורמה מוגבלת למנהלי פלטפורמה')).toBeVisible();
  await expect(page.getByTestId('operations-status')).toHaveCount(0);
});

test('Admin creates a department, its staff group and a representative binding; the new group shows sync applied at zero budget', async ({
  page,
}) => {
  await loginAs(page, 'admin@corp.example');
  await page.goto('/platform');
  await expect(page).toHaveURL(/\/platform\/pending$/);
  await page.getByRole('tab', { name: 'מחלקות', exact: true }).click();
  await page.getByRole('button', { name: 'יצירת מחלקה', exact: true }).click();
  await page.getByRole('textbox', { name: 'שם מחלקה', exact: true }).fill('Department D3');
  await page.getByLabel('מזהה ארגון LiteLLM').fill('D3');
  await page.getByRole('textbox', { name: 'תקציב', exact: true }).fill('1000');
  await page.getByRole('textbox', { name: 'תקופת תקציב', exact: true }).fill('1mo');
  await page.getByTestId('platform-review').click();
  await expect(page.getByTestId('confirm-change')).toBeDisabled();
  await page.getByTestId('change-reason').fill('הקמת מחלקה');
  await page.getByTestId('confirm-change').click();

  const department = page.getByTestId('platform-department').filter({ hasText: 'Department D3' });
  await expect(department).toContainText('1,000');
  await department.getByRole('button', { name: 'עריכת תקציב ותקופה' }).click();
  await page.getByRole('textbox', { name: 'תקציב', exact: true }).fill('2000');
  await page.getByRole('textbox', { name: 'תקופת תקציב', exact: true }).fill('30d');
  await page.getByTestId('platform-review').click();
  await expect(page.getByTestId('change-summary')).toContainText('1mo');
  await expect(page.getByTestId('change-summary')).toContainText('30d');
  await page.getByTestId('change-reason').fill('עדכון תקציב ותקופה');
  await page.getByTestId('confirm-change').click();
  await expect(department).toContainText('2,000');
  await expect(department).toContainText('30d');

  await department.getByRole('button', { name: 'יצירת קבוצה', exact: true }).click();
  await page.getByRole('textbox', { name: 'שם קבוצה', exact: true }).fill('D3 staff');
  await page.getByLabel('מזהה צוות LiteLLM').fill('D3-staff');
  await page.getByLabel('סוג קבוצה').selectOption('department_staff');
  await page.getByTestId('platform-review').click();
  await expect(page.getByTestId('change-summary')).toContainText('0');
  await page.getByTestId('change-reason').fill('הקמת קבוצת מטה');
  await page.getByTestId('confirm-change').click();
  const group = page.getByTestId('platform-group').filter({ hasText: 'D3 staff' });
  await expect(group.getByTestId('group-base')).toHaveText('0');
  await expect(group.getByTestId('sync-badge')).toHaveAttribute('data-status', 'applied', {
    timeout: 60_000,
  });

  await department.getByRole('button', { name: 'נציגים והרשאות' }).click();
  const representatives = page.getByTestId('role-editor-department_rep');
  const dn = 'CN=wm-rep-d1,OU=Groups,DC=corp,DC=example';
  await representatives.getByLabel('קבוצות AD (DN אחד בכל שורה)').fill(dn);
  await representatives.getByRole('button', { name: 'סקירת שינוי' }).click();
  await expect(page.getByTestId('change-summary')).toContainText(dn);
  await page.getByTestId('change-reason').fill('מינוי נציג מחלקה');
  const bindingWrite = page.waitForResponse(
    (res) => res.url().includes('/role-bindings') && res.request().method() === 'PUT',
  );
  await page.getByTestId('confirm-change').click();
  const response = await bindingWrite;
  expect(response.ok()).toBeTruthy();
  expect(decodeURIComponent(response.request().headers()['x-change-reason'])).toBe(
    'מינוי נציג מחלקה',
  );
  expect(response.request().headers()['idempotency-key']).toBeTruthy();
  await expect(representatives.getByTestId('role-members')).toContainText('rep@corp.example');
  await expect(representatives.getByTestId('role-members')).toContainText('Rina Rep');
  await page.getByRole('button', { name: 'סגירת הרשאות' }).click();

  const link = department.getByRole('link', { name: 'ערכים אישיים של נציגים' });
  await expect(link).toHaveAttribute('href', /\/group\/\d+\/members$/);
  await link.click();
  await expect(page).toHaveURL(/\/group\/\d+\/members$/);
  await page.goto('/platform/departments');
  await page.getByTestId('period-selector').selectOption({ index: 1 });
  await expect(page.getByRole('button', { name: 'יצירת מחלקה', exact: true })).toBeDisabled();
  await expect(department.getByRole('button', { name: 'עריכת תקציב ותקופה' })).toBeDisabled();

  await loginAs(page, 'rep@corp.example');
  await page.goto('/platform/departments');
  await expect(page.getByText('הגישה לפלטפורמה מוגבלת למנהלי פלטפורמה')).toBeVisible();
  await expect(page.getByRole('button', { name: 'יצירת מחלקה', exact: true })).toHaveCount(0);
});

test('Admin lowers the concurrency maximum; a head saving a limit above it gets LIMIT_OUT_OF_POLICY', async ({
  page,
}) => {
  await loginAs(page, 'admin@corp.example');
  await page.goto('/platform');
  await page.getByRole('tab', { name: 'מדיניות', exact: true }).click();
  await page.getByTestId('policy-concurrency-max').fill('3');
  await expect(page.getByTestId('policies-review')).toBeDisabled();
  await page.getByTestId('policy-concurrency-max').fill('6');
  await page.getByTestId('policy-rpm-default').fill('100');
  await page.getByTestId('policy-thresholds-near').fill('0.75');
  await page.getByTestId('policy-overalloc_factor_default').fill('1.1');
  await page.getByTestId('policies-review').click();
  await expect(page.getByTestId('change-summary')).toContainText('8');
  await expect(page.getByTestId('change-summary')).toContainText('6');
  await expect(page.getByTestId('confirm-change')).toBeDisabled();
  await page.getByTestId('change-reason').fill('הורדת תקרת מקביליות');
  const saved = page.waitForResponse(
    (res) => res.url().endsWith('/policies') && res.request().method() === 'PUT',
  );
  await page.getByTestId('confirm-change').click();
  const response = await saved;
  expect(response.ok()).toBeTruthy();
  expect(response.request().postDataJSON()).toEqual({
    concurrency: { min: 1, default: 4, max: 6 },
    rpm: { min: 1, default: 100, max: 600 },
    thresholds: { near: 0.75, full: 1 },
    overalloc_factor_default: 1.1,
  });
  expect(decodeURIComponent(response.request().headers()['x-change-reason'])).toBe(
    'הורדת תקרת מקביליות',
  );
  expect(response.request().headers()['idempotency-key']).toBeTruthy();
  await expect(page.getByTestId('confirm-change')).toHaveCount(0);
  await page.reload();
  await expect(page.getByTestId('policy-concurrency-max')).toHaveValue('6');
  await expect(page.getByTestId('policy-rpm-default')).toHaveValue('100');
  await expect(page.getByTestId('policy-thresholds-near')).toHaveValue('0.75');
  await expect(page.getByTestId('policy-overalloc_factor_default')).toHaveValue('1.1');

  const factors = page.getByTestId('department-factors-1');
  await expect(factors.getByTestId('group-factor-1')).toHaveValue('1');
  await factors.getByTestId('group-factor-1').fill('3');
  await expect(factors.getByRole('button', { name: 'סקירת שינוי' })).toBeDisabled();
  await factors.getByTestId('group-factor-1').fill('1.2');
  await factors.getByRole('button', { name: 'סקירת שינוי' }).click();
  await page.getByTestId('change-reason').fill('עדכון מקדם לקבוצה');
  await page.getByTestId('confirm-change').click();
  await expect(page.getByTestId('confirm-change')).toHaveCount(0);
  await page.reload();
  await expect(factors.getByTestId('group-factor-1')).toHaveValue('1.2');
  await page.getByTestId('period-selector').selectOption({ index: 1 });
  await expect(page.getByTestId('policy-concurrency-max')).toHaveAttribute('readonly');
  await expect(page.getByTestId('policies-review')).toBeDisabled();
  await expect(factors.getByRole('button', { name: 'סקירת שינוי' })).toBeDisabled();
  await page.goto('/platform/policies');

  await page.getByRole('tab', { name: 'חיפוש ביקורת', exact: true }).click();
  await page.getByLabel('ישות', { exact: true }).fill('policy');
  await page.getByLabel('מזהה ישות').fill('concurrency');
  await page.getByLabel('מבצע').fill('admin@corp.example');
  await page.getByRole('button', { name: 'חיפוש', exact: true }).click();
  await expect(page.getByTestId('history-timeline')).toContainText('הורדת תקרת מקביליות');
  await expect(page.getByTestId('history-timeline')).toContainText('"max":8');
  await expect(page.getByTestId('history-timeline')).toContainText('"max":6');
  await expect(page.getByTestId('history-timeline')).not.toContainText('עדכון מקדם לקבוצה');

  await loginAs(page, 'dana@corp.example');
  await page.goto('/group/1/members');
  await page.locator('[data-row="dani@corp.example"]').getByTestId('open-panel').click();
  await page.getByTestId('limit-parallel').fill('7');
  await page.getByTestId('panel-review').click();
  await page.getByTestId('change-reason').fill('בקשת מקביליות מעל התקרה');
  const rejected = page.waitForResponse(
    (res) => res.url().includes('/members/dani') && res.url().endsWith('/limits'),
  );
  await page.getByTestId('confirm-change').click();
  const error = await rejected;
  expect(error.status()).toBe(422);
  expect((await error.json()).code).toBe('LIMIT_OUT_OF_POLICY');
  await expect(page.getByTestId('summary-error')).toContainText('מחוץ לטווח המותר (1–6)');
  const head = await apiAs('dana@corp.example');
  try {
    const members = (await (await head.get('/groups/1/members')).json()).members;
    expect(
      members.find((m: { upn: string }) => m.upn === 'dani@corp.example').max_parallel,
    ).toBeNull();
  } finally {
    await head.dispose();
  }
  for (const tab of ['policies', 'audit']) {
    await page.goto(`/platform/${tab}`);
    await expect(page.getByText('הגישה לפלטפורמה מוגבלת למנהלי פלטפורמה')).toBeVisible();
    await expect(page.getByTestId('policies-review')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'חיפוש', exact: true })).toHaveCount(0);
  }
});
