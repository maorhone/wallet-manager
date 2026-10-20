import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { test as base, expect, type Page } from '@playwright/test';

const ROOT = resolve(import.meta.dirname, '../..');

function state(): { api: string; fake: string; env: Record<string, string> } {
  const file = process.env.E2E_STATE_FILE ?? resolve(import.meta.dirname, '../.e2e-state.json');
  return JSON.parse(readFileSync(file, 'utf8'));
}

/** Dev login through the real login page UI path is covered separately; this uses the API. */
export async function loginAs(page: Page, upn: string) {
  const res = await page.request.post('/auth/dev/login', { data: { upn } });
  expect(res.ok()).toBeTruthy();
  const cookies = await page.context().cookies();
  expect(cookies.some((c) => c.name === 'wm_session')).toBeTruthy();
}

/** Re-seed the database and reset the fake LiteLLM (same as `scripts/seed.py --reset`). */
export function resetData() {
  execFileSync('uv', ['run', 'python', 'scripts/seed.py', '--reset'], {
    cwd: ROOT,
    env: { ...process.env, ...state().env },
    stdio: 'pipe',
  });
}

/** Control call on the fake LiteLLM, e.g. fake('/_test/outage', { on: true }). */
export async function fake(path: string, body?: unknown) {
  const res = await fetch(state().fake + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`fake ${path}: ${res.status}`);
  return res.json();
}

/** Make the next `times` calls of a fake LiteLLM endpoint fail with a 500. */
export const fakeFail = (endpoint: string, times: number, status = 500) =>
  fake('/_test/fail', { endpoint, status, times });

export const test = base;
export { expect };
