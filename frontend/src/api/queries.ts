import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { apiFetch, apiPost } from './client';
import type { DevUser, Me, MyWallet, Status, UsagePoint, WriteResponse } from './types';

export const keys = {
  me: ['me'] as const,
  status: ['status'] as const,
  devUsers: ['dev-users'] as const,
  wallet: (period: string) => ['me-wallet', period] as const,
  usage: (from: string, to: string) => ['me-usage', from, to] as const,
};

export const useMe = () => useQuery({ queryKey: keys.me, queryFn: () => apiFetch<Me>('/me') });

export const useStatus = () =>
  useQuery({
    queryKey: keys.status,
    queryFn: () => apiFetch<Status>('/status'),
    refetchInterval: 5000,
  });

export const useDevUsers = () =>
  useQuery({
    queryKey: keys.devUsers,
    queryFn: () => apiFetch<DevUser[]>('/auth/dev/users'),
    retry: false,
  });

/** `refetchInterval` for queries holding pending entities: poll every 2 s until applied (§12.10). */
export const pollWhilePending =
  <T>(hasPending: (data: T) => boolean) =>
  (query: { state: { data: T | undefined } }) =>
    query.state.data !== undefined && hasPending(query.state.data) ? 2000 : false;

/** Write the response into the query cache so `pending` values show at once (§12.10). */
export function applyWriteResponse<T>(
  qc: QueryClient,
  key: readonly unknown[],
  response: WriteResponse,
  merge: (old: T, response: WriteResponse) => T,
) {
  qc.setQueryData<T>(key, (old) => (old === undefined ? old : merge(old, response)));
}

export const useRetryOutbox = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number | string) => apiPost(`/outbox/${id}/retry`, undefined),
    onSuccess: () => qc.invalidateQueries(),
  });
};

export const useMyWallet = (period: string) =>
  useQuery({
    queryKey: keys.wallet(period),
    queryFn: () => apiFetch<MyWallet>(`/me/wallet?period=${period}`),
    enabled: period !== '',
    refetchInterval: 15000,
  });

export const useMyUsage = (from: string, to: string) =>
  useQuery({
    queryKey: keys.usage(from, to),
    queryFn: () => apiFetch<UsagePoint[]>(`/me/usage?from=${from}&to=${to}`),
    enabled: from !== '',
  });
