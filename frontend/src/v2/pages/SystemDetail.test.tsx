// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Initiative from './Initiative';

const h = vi.hoisted(() => ({
  keys: [] as unknown[],
  mutate: vi.fn(),
  initiatives: [] as unknown[],
  mine: [] as unknown[],
  health: { available: true, by_reason: [], last_blocks: [], taken_at: '' } as unknown,
}));
vi.mock('../../api/queries', () => ({
  useMyInitiatives: () => ({ isPending: false, data: { items: h.mine } }),
  useMySystems: () => ({ isPending: false, data: { items: [] } }),
  useServiceKeys: () => ({ isPending: false, data: { items: h.keys, spend_taken_at: null } }),
  useOrgEnvelopes: () => ({
    isPending: false,
    data: { systems: { rows: [] }, initiatives: h.initiatives },
  }),
  useSubjectTelemetry: () => ({ isPending: false, data: h.health }),
  useMoveKey: () => ({ mutate: h.mutate, isPending: false, isError: false }),
}));

const key = (id: number, alias: string, status = 'active') => ({
  id, alias, status, used: 0, max_parallel: 1, rpm_limit: 1, sync_status: 'applied',
}); // prettier-ignore
const ini = { id: 5, name: 'Other', budget: 1000, used: 400, owner_upn: 'x@corp.example' };

function mount() {
  return render(
    <MemoryRouter>
      <Initiative canMove />
    </MemoryRouter>,
  );
}
const submitMove = (keyId: string, target: string) => {
  fireEvent.change(screen.getByLabelText('מפתח להעברה'), { target: { value: keyId } });
  fireEvent.change(screen.getByLabelText('מזהה מערכת ארגונית'), { target: { value: target } });
  fireEvent.submit(screen.getByLabelText('מזהה מערכת ארגונית').closest('form')!);
};

describe('SystemDetail / Initiative (review-p5-r6 majors)', () => {
  beforeEach(() => {
    h.keys = [key(1, 'a'), key(2, 'b')];
    h.initiatives = [ini];
    h.mine = [];
    h.health = { available: true, by_reason: [], last_blocks: [], taken_at: '' };
    h.mutate.mockReset();
  });
  afterEach(cleanup);

  // review-p5-r12 major F1: the owner sees classified failures, or that they cannot be read
  it('shows classified service failures and an explicit unavailable state', () => {
    h.health = {
      available: true,
      by_reason: [{ reason: 'RATE_LIMIT', sub: 'concurrency', count: 3, action: 'x' }],
      last_blocks: [],
      taken_at: '',
    };
    const { unmount } = mount();
    expect(screen.getByText(/מגבלת קצב או מקביליות/)).toBeTruthy();
    unmount();
    h.health = { available: false, by_reason: [], last_blocks: [], taken_at: '' };
    mount();
    expect(screen.getByRole('alert').textContent).toContain('אינם זמינים');
  });

  it('lists and shows consumption of an initiative the caller does not own', () => {
    mount();
    expect(screen.getByRole('heading', { name: 'Other' })).toBeTruthy();
    expect(screen.getByText('600')).toBeTruthy(); // balance = 1000 - server-side used 400
  });

  it('binds the idempotency key to the submitted key and destination', () => {
    mount();
    submitMove('1', '9');
    submitMove('1', '9');
    submitMove('2', '9');
    submitMove('1', '8');
    const keys = h.mutate.mock.calls.map(([v]) => v.key);
    expect(keys[0]).toBe(keys[1]);
    expect(new Set([keys[0], keys[2], keys[3]]).size).toBe(3);
  });

  it('keeps the move outcome after the source has no active key left', () => {
    h.keys = [key(1, 'a')];
    const { rerender } = mount();
    submitMove('1', '9');
    h.mutate.mock.calls[0][1].onSuccess({ status: 'pending' });
    h.keys = [key(1, 'a', 'revoked')];
    rerender(
      <MemoryRouter>
        <Initiative canMove />
      </MemoryRouter>,
    );
    expect(screen.getByRole('status').textContent).toContain('ממתינה לסנכרון');
  });
});
