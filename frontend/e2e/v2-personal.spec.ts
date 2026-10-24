import { expect, fake, loginAs, resetData, test } from './fixtures';
test.beforeAll(() => resetData());
test('employee blocked by concurrency with positive balance sees RATE_LIMIT explanation and throughput CTA', async ({
  page,
}) => {
  await fake('/_test/errors', [
    {
      team_id: 'A1',
      user_id: 'avi@corp.example',
      model: 'm',
      status_code: 429,
      exception_type: 'max_parallel_requests',
      message: 'Max parallel request limit reached',
      at: new Date().toISOString(),
    },
  ]);
  await loginAs(page, 'avi@corp.example');
  await page.goto('/v2/personal');
  await expect(page.getByTestId('last-block')).toContainText('מגבלת קצב');
  await expect(page.getByTestId('block-cta')).toHaveText('בקשת Throughput');
  await page.getByRole('button', { name: 'בקשת Throughput' }).first().click();
  await expect(page.getByLabel('מה הצורך?')).toBeVisible();
});
