// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { cleanup, render, screen } from '@testing-library/react';
import { createElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { DepartmentSummary, Grant } from '../../api/types';

window.matchMedia ??= ((q: string) => ({
  matches: false,
  media: q,
  addEventListener() {},
  removeEventListener() {},
})) as unknown as typeof window.matchMedia;
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

const grant = {
  id: 7,
  department_id: 1,
  group_id: 2,
  group_name: 'G',
  project_ref: 'PRJ-1',
  purpose: 'p',
  amount: 500,
  model: null,
  starts_at: '2026-10-01T00:00:00Z',
  expires_at: '2026-12-01T00:00:00Z',
  status: 'active',
  shared: 0,
  unshared: 500,
  used: 0,
  returned: 0,
  days_left: 30,
  sync_status: 'synced',
  actions: { edit: true, close: true, share: true },
} as unknown as Grant;
const poolData = {
  pool: 1000,
  used: 100,
  available: 200,
  active_grants: 700,
  returned: 0,
  actions: { create_grant: true },
};
vi.mock('../../api/queries', () => ({
  useGrants: () => ({ isPending: false, error: null, data: [grant] }),
  usePmoPool: () => ({ isPending: false, error: null, data: poolData }),
  useGrantWrite: () => ({ mutateAsync: vi.fn() }),
  useGrantAction: () => ({ mutateAsync: vi.fn() }),
  useMe: () => ({ data: { timezone: 'Asia/Jerusalem', scopes: [] } }),
  useRetryOutbox: () => ({ mutate: vi.fn(), isPending: false }),
}));

import Grants from './Grants';
import Pool from './Pool';

const summary = (read_only: boolean) =>
  ({
    department: { id: 1 },
    read_only,
    reset_date: '2026-11-01',
    spend_taken_at: null,
    groups: [],
    allowances: [],
    split: { pmo_reserve_sync_status: 'synced' },
  }) as unknown as DepartmentSummary;
const wrap = (el: React.ReactElement) => render(createElement(MantineProvider, null, el));

afterEach(cleanup);

describe('PMO pages', () => {
  it('Grants: a past period has no create button and no grant actions', () => {
    wrap(createElement(Grants, { summary: summary(true), period: '2026-09' }));
    expect(screen.getByTestId('grant-list')).toBeTruthy();
    expect(screen.queryByTestId('create-grant')).toBeNull();
    for (const a of ['extend', 'increase', 'close'])
      expect(screen.queryByTestId(`${a}-7`)).toBeNull();
  });

  it('Grants: the current period shows them', () => {
    wrap(createElement(Grants, { summary: summary(false), period: '2026-10' }));
    expect(screen.getByTestId('create-grant')).toBeTruthy();
    expect(screen.getByTestId('extend-7')).toBeTruthy();
  });

  it('Pool: remaining is pool.available, not pool - used', () => {
    wrap(createElement(Pool, { summary: summary(false), period: '2026-10' }));
    const card = screen.getByTestId('wallet-card').textContent ?? '';
    expect(card).toContain('200');
    expect(card).not.toContain('900');
  });
});
