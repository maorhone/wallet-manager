// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { WriteResponse } from '../../api/types';
import { WriteOutcome } from './WriteOutcome';

const res = (status: 'pending' | 'applied' | 'failed'): WriteResponse => ({
  request_id: 'r-' + status,
  status: status === 'applied' ? 'applied' : 'pending',
  entities: [{ entity: 'employee', id: 'a', sync_status: status, values: {} }],
});

const show = (initial: WriteResponse, replay = vi.fn()) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <WriteOutcome result={initial} replay={replay} done="DONE" />
    </QueryClientProvider>,
  );

// review-p5-r7 major F1: the saved message waits for LiteLLM synchronization
describe('WriteOutcome', () => {
  afterEach(cleanup);
  it('says pending, then done once the replay reports applied', async () => {
    const replay = vi.fn().mockResolvedValue(res('applied'));
    show(res('pending'), replay);
    await waitFor(() => expect(screen.getByText('DONE')).toBeTruthy(), { timeout: 5000 });
    expect(replay).toHaveBeenCalled();
  });
  it('shows a permanent failure as an alert', () => {
    show(res('failed'));
    expect(screen.getByRole('alert')).toBeTruthy();
  });
  it('shows pending without claiming it is enforced', () => {
    show(res('pending'), vi.fn().mockReturnValue(new Promise(() => {})));
    expect(screen.queryByText('DONE')).toBeNull();
    expect(screen.getByRole('status')).toBeTruthy();
  });
});
