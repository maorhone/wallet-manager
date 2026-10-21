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
