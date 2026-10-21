import { expect, loginAs, resetData, test } from './fixtures';

test.beforeEach(() => resetData());

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
