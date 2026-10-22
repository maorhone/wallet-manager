// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { ApiError } from '../../api/client';
import type { GroupSummary, Me } from '../../api/types';
import Overview from './Overview';

window.matchMedia ??= ((q: string) => ({
  matches: false,
  media: q,
  addEventListener() {},
  removeEventListener() {},
})) as unknown as typeof window.matchMedia;

const mocks = vi.hoisted(() => ({ roles: [] as Me['roles'], usage: vi.fn() }));
vi.mock('../../api/queries', () => ({
  useMe: () => ({
    data: { roles: mocks.roles, current_period: '2026-10', timezone: 'Asia/Jerusalem' },
  }),
  useGroupSummary: () => ({}),
  useUsageReport: (...args: unknown[]) => mocks.usage(...args),
}));

const summary = {
  group: { id: 7, department_id: 3, name: 'Group', default_member_cap: 0 },
  period: '2026-10',
  reset_date: '2026-11-01',
  spend_taken_at: null,
  read_only: false,
  wallet: {
    total: 100,
    allocated: 0,
    unallocated: 100,
    used: 0,
    remaining: 100,
    percent: 0,
    state: 'healthy',
    base: 100,
    grants: 0,
  },
  pots: [],
  grants: [],
  top_consumers: [],
  near_limit_members: [],
  pending_sync_count: 0,
  actions: {
    edit_members: false,
    edit_pots_limits: false,
    edit_defaults: false,
    service_keys: { view: false, manage: false },
  },
} satisfies GroupSummary;

afterEach(() => {
  cleanup();
  mocks.usage.mockReset();
});

function showOverview(editMembers = false) {
  render(
    <MantineProvider>
      <MemoryRouter>
        <Overview
          summary={{ ...summary, actions: { ...summary.actions, edit_members: editMembers } }}
        />
      </MemoryRouter>
    </MantineProvider>,
  );
}

it.each(['department', 'group'])(
  'hides usage for a PMO with a viewer role on another %s',
  (kind) => {
    mocks.roles = [
      { role: 'pmo', scope_kind: 'department', scope_id: 3 },
      { role: 'viewer', scope_kind: kind, scope_id: 99 },
    ];
    mocks.usage.mockReturnValue({
      isPending: false,
      error: new ApiError(403, { code: 'FORBIDDEN' }),
    });
    showOverview();
    expect(screen.queryByTestId('usage-chart')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(mocks.usage).not.toHaveBeenCalled();
  },
);

it.each([
  ['global', null],
  ['department', 3],
  ['group', 7],
] as const)('shows usage for a viewer scoped to this %s', (kind, id) => {
  mocks.roles = [{ role: 'viewer', scope_kind: kind, scope_id: id }];
  mocks.usage.mockReturnValue({ isPending: false, error: null, data: { series: [], cap: 100 } });
  showOverview();
  expect(screen.getByTestId('usage-chart')).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(mocks.usage).toHaveBeenCalledWith('group', '7', '2026-10', 'day', 'model');
});

it('keeps usage visible for member editors', () => {
  mocks.roles = [];
  mocks.usage.mockReturnValue({ isPending: false, error: null, data: { series: [], cap: 100 } });
  showOverview(true);
  expect(screen.getByTestId('usage-chart')).toBeTruthy();
});
