// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Personal from './Personal';

const h = vi.hoisted(() => ({ failed: true }));
const refreshFailed = () => (h.failed ? new Error('down') : null);
vi.mock('../../api/queries', () => ({
  useMe: () => ({ data: { current_period: '2026-10', upn: 'a@corp.example' } }),
  useMyWallet: () => ({
    isPending: false,
    data: {
      period: '2026-10',
      wallet: { effective_remaining: 5, used: 1 },
      limits: { max_parallel: 1, rpm_limit: 1 },
    },
  }),
  // Earlier successful fetches stay in `data` while the latest refetch failed.
  useMyStatus: () => ({
    data: { last_blocks: [], blocks_available: true, limits: { max_parallel: 1, rpm_limit: 1 } },
    error: refreshFailed(),
    isFetching: false,
  }),
  useMyUsage: () => ({ data: [], error: refreshFailed(), isFetching: false }),
  useMyTagUsage: () => ({ data: { series: [] }, error: refreshFailed(), isFetching: false }),
  useServiceStatus: () => ({
    data: { state: 'ok' },
    error: refreshFailed(),
    isFetching: false,
  }),
  useSubmitRequest: () => ({ mutate: vi.fn() }),
}));

describe('Personal (review-p5-r6 major, F1 Personal.tsx:28)', () => {
  afterEach(cleanup);
  it('does not show cached empty telemetry or a healthy service after a failed refresh', () => {
    h.failed = true;
    render(<Personal />);
    expect(screen.queryByText('אין חסימות אחרונות')).toBeNull();
    expect(screen.getAllByText('לא זמין').length).toBeGreaterThanOrEqual(3);
    expect(screen.queryByText('תקין')).toBeNull();
  });
  it('shows the cached empty state when the refresh succeeded', () => {
    h.failed = false;
    render(<Personal />);
    expect(screen.queryAllByText('לא זמין')).toHaveLength(0);
  });
});
