import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  timeout: 30_000,
  reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:4110', locale: 'he-IL' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: 'uv run python scripts/e2e_stack.py',
      cwd: '..',
      url: 'http://127.0.0.1:4111/readyz',
      timeout: 180_000,
      reuseExistingServer: false,
    },
    {
      command: 'npx vite --port 4110 --host 127.0.0.1 --strictPort',
      url: 'http://127.0.0.1:4110',
      env: { API_ORIGIN: 'http://127.0.0.1:4111' },
      reuseExistingServer: false,
    },
  ],
});
