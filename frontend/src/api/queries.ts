import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query';
import { apiFetch, apiPatch, apiPost, apiPut, type WriteOptions } from './client';
import type {
  AuditEntry,
  DepartmentSummary,
  DepartmentRecord,
  DepartmentIn,
  GroupIn,
  RoleBinding,
  RoleBindingIn,
  RoleBindingScope,
  DevUser,
  Grant,
  GrantIn,
  GrantShare,
  ProjectSummary,
  GroupSummary,
  Me,
  Member,
  MemberActionKind,
  MembersList,
  MoveTarget,
  MyWallet,
  NotificationPref,
  NotificationsList,
  OutboxEntry,
  PendingPlacement,
  PmoPool,
  Policies,
  Status,
  SyncStatus,
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
  notifications: ['notifications'] as const,
  notificationsUnread: ['notifications', 'unread'] as const,
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

export const useOutbox = (status: SyncStatus | '') =>
  useQuery({
    queryKey: ['outbox', status],
    queryFn: () => apiFetch<OutboxEntry[]>(`/outbox${status ? `?status=${status}` : ''}`),
    refetchInterval: 2000,
  });

export const useReconciliationAudit = () =>
  useQuery({
    queryKey: ['audit', 'reconcile'],
    queryFn: () => apiFetch<AuditEntry[]>('/audit?actor=system:reconcile'),
    refetchInterval: 5000,
  });

export const useRetryOutbox = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...write }: WriteOptions & { id: number | string }) =>
      apiPost(`/outbox/${id}/retry`, undefined, write),
    onSuccess: () => qc.invalidateQueries(),
  });
};

export const useNotifications = () =>
  useQuery({
    queryKey: keys.notifications,
    queryFn: () => apiFetch<NotificationsList>('/me/notifications'),
    refetchInterval: 30000,
  });

export const useUnreadNotifications = () =>
  useQuery({
    queryKey: keys.notificationsUnread,
    queryFn: () => apiFetch<NotificationsList>('/me/notifications?unread=true'),
    refetchInterval: 30000,
  });

export const useMarkRead = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => apiPost(`/me/notifications/${id}/read`, undefined),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.notifications }),
  });
};

/** Prefs come with the notifications list; muting a kind is a plain PUT (no change reason). */
export const useNotificationPrefs = () => {
  const qc = useQueryClient();
  const list = useNotifications();
  const set = useMutation({
    mutationFn: (v: { kind: string; muted: boolean }) =>
      apiFetch<NotificationPref[]>('/me/notification-prefs', {
        method: 'PUT',
        body: JSON.stringify({ [v.kind]: v.muted }),
      }),
    onSuccess: (prefs) =>
      qc.setQueryData<NotificationsList>(keys.notifications, (old) => old && { ...old, prefs }),
  });
  return { prefs: list.data?.prefs, isPending: list.isPending, error: list.error, set };
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
  d.groups.some((g) => g.sync_status === 'pending') ||
  d.group_heads.some((h) => h.sync_status === 'pending');

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
      qc.invalidateQueries({ queryKey: ['departments'] });
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
      qc.invalidateQueries({ queryKey: ['department'] });
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

/** Pending employees of one department, or everyone (admin) with 'all'. */
export const usePendingPlacements = (deptId: string | 'all') =>
  useQuery({
    queryKey: ['pending-placements', deptId],
    queryFn: () =>
      apiFetch<PendingPlacement[]>(
        deptId === 'all' ? '/pending-placements' : `/departments/${deptId}/pending-placements`,
      ),
    refetchInterval: 15000,
  });

export const useDepartmentIds = (enabled: boolean) =>
  useQuery({
    queryKey: ['departments'],
    queryFn: () => apiFetch<{ id: number }[]>('/departments'),
    enabled,
    select: (rows) => rows.map((d) => d.id),
  });

export const useDepartments = () =>
  useQuery({
    queryKey: ['departments'],
    queryFn: () => apiFetch<DepartmentRecord[]>('/departments'),
  });

const usePlatformCreate = <T>(path: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { body: T; reason: string; key: string }) =>
      apiPost<WriteResponse>(path, v.body, { reason: v.reason, idempotencyKey: v.key }),
    onSuccess: () => {
      for (const k of ['departments', 'department', 'me', 'audit'])
        qc.invalidateQueries({ queryKey: [k] });
    },
  });
};

export const useCreateDepartment = () => usePlatformCreate<DepartmentIn>('/departments');
export const useCreateGroup = (departmentId: number) =>
  usePlatformCreate<GroupIn>(`/departments/${departmentId}/groups`);

export const useRoleBindings = (scope: RoleBindingScope) =>
  useQuery({
    queryKey: ['role-bindings', scope.scope_kind, scope.scope_id],
    queryFn: () => {
      const params = new URLSearchParams({ scope_kind: scope.scope_kind });
      if (scope.scope_id !== undefined) params.set('scope_id', String(scope.scope_id));
      return apiFetch<RoleBinding[]>(`/role-bindings?${params}`);
    },
  });

export const useRoleBindingsWrite = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { body: RoleBindingIn; reason: string; key: string }) =>
      apiPut<WriteResponse>('/role-bindings', v.body, {
        reason: v.reason,
        idempotencyKey: v.key,
      }),
    onSuccess: () => {
      for (const k of ['role-bindings', 'me', 'department', 'group', 'audit'])
        qc.invalidateQueries({ queryKey: [k] });
    },
  });
};

/** Place a pending employee into a group; the list and the department views refresh. */
export const usePlace = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { upn: string; groupId: number; reason: string; key: string }) =>
      apiPost<WriteResponse>(
        `/pending-placements/${encodeURIComponent(v.upn)}/place`,
        { group_id: v.groupId },
        { reason: v.reason, idempotencyKey: v.key },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pending-placements'] });
      qc.invalidateQueries({ queryKey: ['department'] });
      qc.invalidateQueries({ queryKey: ['group'] });
      qc.invalidateQueries({ queryKey: ['audit'] });
    },
  });
};

/** Active groups a pending employee may be placed into: the department's, or all for admin. */
export const useDepartmentGroups = (deptIds: number[], period: string) =>
  useQueries({
    queries: deptIds.map((id) => ({
      queryKey: ['department', String(id), 'summary', period],
      queryFn: () => apiFetch<DepartmentSummary>(`/departments/${id}/summary?period=${period}`),
      enabled: period !== '',
    })),
    combine: (rs) => ({
      groups: rs.flatMap((r) =>
        (r.data?.groups ?? []).map((g) => ({
          group_id: g.id,
          name: g.name,
          department_id: r.data!.department.id,
          department_name: r.data!.department.name,
        })),
      ),
    }),
  });

export const usePmoPool = (deptId: string, period: string) =>
  useQuery({
    queryKey: ['pmo', deptId, 'pool', period],
    queryFn: () => apiFetch<PmoPool>(`/departments/${deptId}/pmo-pool?period=${period}`),
    enabled: period !== '',
    refetchInterval: 15000,
  });

export const useGrants = (filters: { department?: string; group?: string }) => {
  const qs = new URLSearchParams(
    Object.entries(filters).filter((e): e is [string, string] => !!e[1]),
  ).toString();
  return useQuery({
    queryKey: ['grants', qs],
    queryFn: () => apiFetch<Grant[]>(`/grants${qs ? `?${qs}` : ''}`),
    refetchInterval: (q) => (q.state.data?.some((g) => g.sync_status === 'pending') ? 2000 : 15000),
  });
};

/** Create a grant (one POST per confirm); the pool, grant lists and group views refresh. */
export const useGrantWrite = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { body: GrantIn; reason: string; key: string }) =>
      apiPost<WriteResponse>('/grants', v.body, { reason: v.reason, idempotencyKey: v.key }),
    onSuccess: () => {
      for (const k of ['pmo', 'grants', 'department', 'group', 'audit'])
        qc.invalidateQueries({ queryKey: [k] });
    },
  });
};

/** Extend, increase (PATCH) or close early (POST close) one grant. */
export const useGrantAction = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: {
      id: number;
      patch?: { amount?: number; expires_at?: string };
      reason: string;
      key: string;
    }) => {
      const write = { reason: v.reason, idempotencyKey: v.key };
      return v.patch
        ? apiPatch<WriteResponse>(`/grants/${v.id}`, v.patch, write)
        : apiPost<WriteResponse>(`/grants/${v.id}/close`, { reason: v.reason }, write);
    },
    onSuccess: () => {
      for (const k of ['pmo', 'grants', 'project', 'department', 'group', 'audit'])
        qc.invalidateQueries({ queryKey: [k] });
    },
  });
};

/** Create shares (list), change one (PATCH) or close one (POST close); grants, wallets and groups refresh. */
export const useShareWrite = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: {
      grantId: number;
      /** New shares; or `shareId` with `patch` to change one, without `patch` to close it. */
      create?: { upn: string; amount: number; expires_at: string }[];
      shareId?: number;
      patch?: { amount: number; expires_at: string };
      reason: string;
      key: string;
    }) => {
      const write = { reason: v.reason, idempotencyKey: v.key };
      const base = `/grants/${v.grantId}/shares`;
      if (v.create) return apiPost<WriteResponse>(base, v.create, write);
      if (v.patch) return apiPatch<WriteResponse>(`${base}/${v.shareId}`, v.patch, write);
      return apiPost<WriteResponse>(`${base}/${v.shareId}/close`, { reason: v.reason }, write);
    },
    onSuccess: () => {
      for (const k of ['grants', 'project', 'department', 'group', 'audit', 'me-wallet', 'pmo'])
        qc.invalidateQueries({ queryKey: [k] });
    },
  });
};

export const useProjectSummary = (ref: string) =>
  useQuery({
    queryKey: ['project', ref],
    queryFn: () =>
      apiFetch<ProjectSummary>(`/projects/summary?project_ref=${encodeURIComponent(ref)}`),
    enabled: ref !== '',
    refetchInterval: 15000,
  });

export const useGrantShares = (grantId: number, enabled = true) =>
  useQuery({
    queryKey: ['project', 'shares', grantId],
    queryFn: () => apiFetch<GrantShare[]>(`/grants/${grantId}/shares`),
    enabled,
    refetchInterval: 15000,
  });

export const useMyUsage = (from: string, to: string) =>
  useQuery({
    queryKey: keys.usage(from, to),
    queryFn: () => apiFetch<UsagePoint[]>(`/me/usage?from=${from}&to=${to}`),
    enabled: from !== '',
  });
