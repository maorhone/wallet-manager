// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MantineProvider } from '@mantine/core';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Initiative from './Initiative';

window.matchMedia ??= ((q: string) => ({
  matches: false,
  media: q,
  addEventListener() {},
  removeEventListener() {},
})) as unknown as typeof window.matchMedia;

let managed: Record<string, unknown> = { isPending: false, data: { systems: { rows: [] } } };
const refetch = vi.fn();
vi.mock('../../api/queries', () => ({
  useMyInitiatives: () => ({
    isPending: false,
    data: {
      items: [
        {
          id: 7,
          name: 'Pilot',
          owner_upn: 'dani@corp.example',
          group_id: 1,
          period_start: null,
          period_end: null,
          budget: 1000,
          status: 'active',
          tech_status: 'connected',
        },
      ],
    },
  }),
  useMySystems: () => ({
    isPending: false,
    data: { items: [{ id: 7, name: 'Org sys', budget: 500, owner_upn: 'dani@corp.example' }] },
  }),
  useServiceKeys: () => ({
    isPending: false,
    data: {
      items: [
        { id: 3, alias: 'bot', status: 'active', used: 250, max_parallel: 2, rpm_limit: 100 },
      ],
      spend_taken_at: null,
    },
  }),
  useOrgEnvelopes: () => managed,
  useMoveKey: () => ({ mutate: vi.fn(), isPending: false, isSuccess: false }),
}));

describe('Initiative', () => {
  afterEach(() => {
    cleanup();
    managed = { isPending: false, data: { systems: { rows: [] } } };
  });
  // review-p5-r7 major F1: an unavailable managed inventory is not an empty one
  it('shows an error with retry when the managed inventory fails to load', () => {
    managed = { isPending: false, error: new Error('boom'), data: undefined, refetch };
    render(
      <MantineProvider>
        <MemoryRouter>
          <Initiative canMove />
        </MemoryRouter>
      </MantineProvider>,
    );
    expect(screen.queryByText('bot')).toBeNull();
    fireEvent.click(screen.getByRole('button'));
    expect(refetch).toHaveBeenCalled();
  });
  it('shows budget, consumption, balance and the key alias read-only', () => {
    render(
      <MemoryRouter>
        <Initiative canMove={false} />
      </MemoryRouter>,
    );
    expect(screen.getByText('bot')).toBeTruthy();
    expect(screen.getByText('מחובר')).toBeTruthy();
    expect(screen.getByText('בקשת שינוי')).toBeTruthy();
    expect(screen.queryByText('העבר מפתח למערכות ארגוניות')).toBeNull();
  });
  it('offers the key move to allocators', () => {
    render(
      <MemoryRouter>
        <Initiative canMove />
      </MemoryRouter>,
    );
    expect(screen.getByText('העבר מפתח למערכות ארגוניות')).toBeTruthy();
  });
  it('lists an owned system next to an initiative with the same id', () => {
    render(
      <MemoryRouter>
        <Initiative canMove={false} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Org sys' }));
    expect(screen.getByRole('heading', { name: 'Org sys' })).toBeTruthy();
  });
  // review-p5-r2 blocker (F1): the change request carries the selected subject
  it('links the change request to the selected initiative', () => {
    render(
      <MemoryRouter>
        <Initiative canMove={false} />
      </MemoryRouter>,
    );
    const href = screen.getByText('בקשת שינוי').getAttribute('href')!;
    const q = new URLSearchParams(href.split('?')[1]);
    expect([q.get('target_kind'), q.get('target_id'), q.get('target_name')]).toEqual([
      'initiative',
      '7',
      'Pilot',
    ]);
  });
});
