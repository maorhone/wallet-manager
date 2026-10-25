// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Initiative from './Initiative';

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
      items: [{ id: 3, alias: 'bot', used: 250, max_parallel: 2, rpm_limit: 100 }],
      spend_taken_at: null,
    },
  }),
  useMoveKey: () => ({ mutate: vi.fn(), isPending: false, isSuccess: false }),
}));

describe('Initiative', () => {
  afterEach(cleanup);
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
