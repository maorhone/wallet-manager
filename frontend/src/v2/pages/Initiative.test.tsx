// @vitest-environment jsdom
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
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
});
