// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { createElement, type ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useServiceKeyWrite } from './queries';

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
