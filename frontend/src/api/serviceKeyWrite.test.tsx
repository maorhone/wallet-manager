// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { createElement, type ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useServiceKeyWrite, useTransferStatus } from './queries';

afterEach(() => vi.restoreAllMocks());

describe('useServiceKeyWrite', () => {
  it('hands the one-time secret to the caller and keeps it out of the mutation cache', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ secret: 'sk-once', key: { id: 1 } }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    );
    const qc = new QueryClient();
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: qc }, children);
    const onSecret = vi.fn();
    const { result } = renderHook(() => useServiceKeyWrite(onSecret), { wrapper });
    result.current.mutate({
      action: 'rotate',
      id: 1,
      ownerKind: 'group',
      ownerId: 2,
      reason: 'r',
      key: 'k',
    });
    await waitFor(() => expect(onSecret).toHaveBeenCalledWith('sk-once'));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const cached = qc
      .getMutationCache()
      .getAll()
      .map((m) => JSON.stringify(m.state.data));
    expect(cached.join()).not.toContain('sk-once');
  });
});

describe('useTransferStatus', () => {
  // review-p5-r9 major F2: a terminal transfer refreshes the budget, allocation and attention data
  it('invalidates other queries once the transfer is no longer pending', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ id: 5, status: 'failed', state: 'compensated' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    );
    const qc = new QueryClient();
    qc.setQueryData(['budget-overview', 1], { stale: true });
    qc.setQueryData(['attention', 1], { stale: true });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: qc }, children);
    const { result } = renderHook(() => useTransferStatus(5), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(qc.getQueryState(['budget-overview', 1])?.isInvalidated).toBe(true);
    expect(qc.getQueryState(['attention', 1])?.isInvalidated).toBe(true);
  });
});
