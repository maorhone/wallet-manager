import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { apiFetch, apiPost, apiPut } from './client';
import type {
  AuditEntry,
  DepartmentSummary,
  DevUser,
  GroupSummary,
  Me,
  Member,
  MemberActionKind,
  MembersList,
  MoveTarget,
  MyWallet,
  Policies,
  Status,
  UsagePoint,
  WriteResponse,
} from './types';

export const keys = {
  me: ['me'] as const,
  status: ['status'] as const,
  devUsers: ['dev-users'] as const,
  wallet: (period: string) => ['me-wallet', period] as const,
  usage: (from: string, to: string) => ['me-usage', from, to] as const,
  groupSummary: (id: string, period: string) => ['group', id, 'summary', period] as const,
  members: (id: string, period: string) => ['group', id, 'members', period] as const,
  policies: ['policies'] as const,
  audit: (entity: string, id: string) => ['audit', entity, id] as const,
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

const hasPendingMembers = (d: MembersList) => d.members.some((m) => m.sync_status === 'pending');

export const useGroupSummary = (id: string, period: string) =>
  useQuery({
    queryKey: keys.groupSummary(id, period),
    queryFn: () => apiFetch<GroupSummary>(`/groups/${id}/summary?period=${period}`),
    enabled: period !== '',
    refetchInterval: (q) => (q.state.data?.pending_sync_count ? 2000 : 15000),
  });

const hasPendingDepartment = (d: DepartmentSummary) =>
  d.split.pmo_reserve_sync_status === 'pending' ||
  d.groups.some((g) => g.sync_status === 'pending');

export const useDepartmentSummary = (id: string, period: string) =>
  useQuery({
    queryKey: ['department', id, 'summary', period],
    queryFn: () => apiFetch<DepartmentSummary>(`/departments/${id}/summary?period=${period}`),
    enabled: period !== '',
    refetchInterval: (q) => (q.state.data && hasPendingDepartment(q.state.data) ? 1000 : 15000),
  });

/** One department write (split or budget) per confirm; refreshes the department queries. */
export const useDepartmentWrite = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { path: string; body: unknown; reason: string; key: string }) =>
      apiPut<WriteResponse>(v.path, v.body, { reason: v.reason, idempotencyKey: v.key }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['department'] });
      qc.invalidateQueries({ queryKey: ['audit'] });
    },
  });
};

export const useGroupMembers = (id: string, period: string, enabled = true) =>
  useQuery({
    queryKey: keys.members(id, period),
    queryFn: () => apiFetch<MembersList>(`/groups/${id}/members?period=${period}`),
    enabled: enabled && period !== '',
    refetchInterval: pollWhilePending(hasPendingMembers),
  });

export const usePolicies = () =>
  useQuery({ queryKey: keys.policies, queryFn: () => apiFetch<Policies>('/policies') });

export const useGroupAudit = (id: string) =>
  useQuery({
    queryKey: keys.audit('group', id),
    queryFn: () => apiFetch<AuditEntry[]>(`/audit?entity=group&id=${id}`),
  });

/**
 * One PUT per confirm. The response is merged into the members cache (pending badges show at once,
 * §12.10) and the group queries are refreshed; the Idempotency-Key is the caller's.
 */
export const useGroupWrite = (groupId: string, period: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { path: string; body: unknown; reason: string; key: string }) =>
      apiPut<WriteResponse>(v.path, v.body, { reason: v.reason, idempotencyKey: v.key }),
    onSuccess: (res) => {
      applyWriteResponse<MembersList>(qc, keys.members(groupId, period), res, (old, r) => ({
        ...old,
        members: old.members.map((m) => {
          const e = r.entities.find((x) => x.entity === 'employee' && x.id === m.upn);
          return e ? { ...m, ...(e.values as Partial<Member>), sync_status: e.sync_status } : m;
        }),
      }));
      qc.invalidateQueries({ queryKey: ['group', groupId] });
      qc.invalidateQueries({ queryKey: ['audit'] });
    },
  });
};

/** Target groups the actor may move this member to (loaded only while the move dialog is open). */
export const useMoveTargets = (groupId: string, upn: string, enabled: boolean) =>
  useQuery({
    queryKey: ['group', groupId, 'move-targets', upn],
    queryFn: () =>
      apiFetch<MoveTarget[]>(`/groups/${groupId}/members/${encodeURIComponent(upn)}/move-targets`),
    enabled,
  });

/** Kick, restore, move or delete one member; refreshes the group's members and summary. */
export const useMemberAction = (groupId: string, period: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: {
      action: MemberActionKind;
      upn: string;
      body?: unknown;
      reason: string;
      key: string;
    }) =>
      apiPost<WriteResponse>(
        `/groups/${groupId}/members/${encodeURIComponent(v.upn)}/${v.action}`,
        v.body ?? {},
        { reason: v.reason, idempotencyKey: v.key },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.members(groupId, period) });
      qc.invalidateQueries({ queryKey: keys.groupSummary(groupId, period) });
      qc.invalidateQueries({ queryKey: ['department'] });
      qc.invalidateQueries({ queryKey: ['audit'] });
    },
  });
};

export const useMyUsage = (from: string, to: string) =>
  useQuery({
    queryKey: keys.usage(from, to),
    queryFn: () => apiFetch<UsagePoint[]>(`/me/usage?from=${from}&to=${to}`),
    enabled: from !== '',
  });
