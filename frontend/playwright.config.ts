import { defineConfig, devices } from '@playwright/test';

// Ports are overridable (scripts/test.sh uses 4120-4122) so tests can run next to a live preview.
const UI_PORT = process.env.E2E_UI_PORT ?? '4110';
const API_PORT = process.env.E2E_API_PORT ?? '4111';

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  timeout: 30_000,
  reporter: 'list',
  use: { baseURL: `http://127.0.0.1:${UI_PORT}`, locale: 'he-IL' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: 'uv run python scripts/e2e_stack.py',
      cwd: '..',
      url: `http://127.0.0.1:${API_PORT}/readyz`,
      timeout: 180_000,
      reuseExistingServer: false,
    },
    {
      command: `npx vite --port ${UI_PORT} --host 127.0.0.1 --strictPort`,
      url: `http://127.0.0.1:${UI_PORT}`,
      env: { API_ORIGIN: `http://127.0.0.1:${API_PORT}` },
      reuseExistingServer: false,
    },
  ],
});
