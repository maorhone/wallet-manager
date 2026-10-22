// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { createElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ServiceKey } from '../api/types';
import { ServiceKeyList } from './ServiceKeyList';

window.matchMedia ??= ((q: string) => ({
  matches: false,
  media: q,
  addEventListener() {},
  removeEventListener() {},
})) as unknown as typeof window.matchMedia;

const key = {
  id: 1,
  owner_kind: 'group',
  owner_id: 2,
  alias: 'ci',
  max_budget: 100,
  max_parallel: 1,
  rpm_limit: 1,
  models: [],
  status: 'active',
  used: 0,
  sync_status: 'synced',
  created_at: '2026-10-01T00:00:00Z',
  actions: { rotate: true, revoke: true },
} as unknown as ServiceKey;

const show = (manage: boolean) =>
  render(
    createElement(
      QueryClientProvider,
      { client: new QueryClient() },
      createElement(
        MantineProvider,
        null,
        createElement(ServiceKeyList, {
          items: [key],
          manage,
          onRotate: vi.fn(),
          onRevoke: vi.fn(),
        }),
      ),
    ),
  );

afterEach(cleanup);

describe('ServiceKeyList', () => {
  it('hides rotate and revoke when the tab is read-only, even if the server allows them', () => {
    show(false);
    expect(screen.queryByTestId('service-key-rotate-1')).toBeNull();
    expect(screen.queryByTestId('service-key-revoke-1')).toBeNull();
  });

  it('shows rotate and revoke when manageable', () => {
    show(true);
    expect(screen.getAllByRole('button').length).toBe(2);
  });
});
