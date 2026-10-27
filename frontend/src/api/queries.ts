import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query';
import { apiFetch, apiPatch, apiPost, apiPut, type WriteOptions } from './client';
import type {
  ConcurrencyPeaks,
  AllocationLevel,
  GrantsReport,
  BlockedReport,
  AllocationReport,
  AuditEntry,
  AuditFilters,
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
  OverallocationFinding,
  PendingPlacement,
  PmoPool,
  Policies,
  Production,
  ProductionOrgIn,
  ProductionSystemIn,
  ServiceKeyIn,
  ServiceKeysList,
  ServiceKeyWriteResponse,
  PoliciesIn,
  Status,
  SyncStatus,
  UsagePoint,
  UsageReport,
  UsageScope,
  UsageGranularity,
  UsageBy,
  WriteResponse,
} from './types';

export const useAllocationReport = (level: AllocationLevel, id: string, period: string) =>
  useQuery({
    queryKey: ['reports', 'allocation', level, id, period],
    queryFn: () =>
      apiFetch<AllocationReport>(
        `/reports/allocation?${new URLSearchParams({ level, id, period })}`,
      ),
    enabled: period !== '' && id !== '',
    refetchInterval: 15000,
  });

/** The server authorizes visible departments; this filter narrows the selected department. */
export const useOverallocationReport = (department?: string) =>
  useQuery({
    queryKey: ['reports', 'overallocation'],
    queryFn: () => apiFetch<{ findings: OverallocationFinding[] }>('/reports/overallocation'),
    select: (data) =>
      department === undefined
        ? data.findings
        : data.findings.filter((f) => f.department_id === Number(department)),
    refetchInterval: 15000,
  });

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

export const usePoliciesWrite = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { body: PoliciesIn; reason: string; key: string }) =>
      apiPut<WriteResponse>('/policies', v.body, { reason: v.reason, idempotencyKey: v.key }),
    onSuccess: () => qc.invalidateQueries(),
  });
};

export const useAuditSearch = (filters: AuditFilters | null) => {
  const params = new URLSearchParams(
    Object.entries(filters ?? {}).filter((entry): entry is [string, string] => !!entry[1]),
  ).toString();
  return useQuery({
    queryKey: ['audit', 'search', params],
    queryFn: () => apiFetch<AuditEntry[]>(`/audit${params ? `?${params}` : ''}`),
    enabled: filters !== null,
  });
};

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

const hasPendingServiceKeys = (d: ServiceKeysList) =>
  d.items.some((k) => k.sync_status === 'pending');

export const useServiceKeys = (ownerKind: 'group' | 'system' | 'initiative', ownerId: number) =>
  useQuery({
    queryKey: ['service-keys', ownerKind, ownerId],
    queryFn: () =>
      apiFetch<ServiceKeysList>(`/service-keys?owner_kind=${ownerKind}&owner_id=${ownerId}`),
    refetchInterval: pollWhilePending(hasPendingServiceKeys),
  });

/** Create, rotate or revoke a service key; create/rotate responses carry the one-time secret. */
export const useServiceKeyWrite = (onSecret?: (secret: string) => void) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (
      v:
        | { action: 'create'; body: ServiceKeyIn; reason: string; key: string }
        | {
            action: 'rotate' | 'revoke';
            id: number;
            ownerKind: 'group' | 'system';
            ownerId: number;
            reason: string;
            key: string;
          },
    ) => {
      const write = { reason: v.reason, idempotencyKey: v.key };
      const res =
        v.action === 'create'
          ? await apiPost<ServiceKeyWriteResponse>('/service-keys', v.body, write)
          : await apiPost<ServiceKeyWriteResponse>(`/service-keys/${v.id}/${v.action}`, {}, write);
      // The one-time secret goes to the caller only, never into the TanStack mutation cache.
      if (res.secret) onSecret?.(res.secret);
      return { ...res, secret: undefined };
    },
    onSuccess: (_res, v) => {
      const [ownerKind, ownerId] =
        v.action === 'create' ? [v.body.owner_kind, v.body.owner_id] : [v.ownerKind, v.ownerId];
      qc.invalidateQueries({ queryKey: ['service-keys', ownerKind, ownerId] });
      qc.invalidateQueries({ queryKey: ['group'] });
      qc.invalidateQueries({ queryKey: ['department'] });
      qc.invalidateQueries({ queryKey: ['production'] });
      qc.invalidateQueries({ queryKey: ['audit'] });
    },
  });
};

const hasPendingProduction = (d: Production) =>
  d.org?.sync_status === 'pending' || d.systems.some((s) => s.sync_status === 'pending');

export const useProduction = (deptId: string, period: string) =>
  useQuery({
    queryKey: ['production', deptId, period],
    queryFn: () => apiFetch<Production>(`/production/departments/${deptId}?period=${period}`),
    enabled: period !== '',
    refetchInterval: pollWhilePending(hasPendingProduction),
  });

/** Set the production organization (PUT) or add a system (POST); one write per confirm. */
export const useProductionWrite = (deptId: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (
      v:
        | { kind: 'org'; body: ProductionOrgIn; reason: string; key: string }
        | { kind: 'system'; body: ProductionSystemIn; reason: string; key: string },
    ) => {
      const write = { reason: v.reason, idempotencyKey: v.key };
      return v.kind === 'org'
        ? apiPut<WriteResponse>(`/production/departments/${deptId}`, v.body, write)
        : apiPost<WriteResponse>(`/production/departments/${deptId}/systems`, v.body, write);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['production', deptId] });
      qc.invalidateQueries({ queryKey: ['audit'] });
    },
  });
};

export const useUsageReport = (
  scope: UsageScope,
  id: string,
  period: string,
  granularity: UsageGranularity = 'day',
  by: UsageBy = 'model',
  enabled = true,
) =>
  useQuery({
    queryKey: ['reports', 'usage', scope, id, period, granularity, by],
    queryFn: () => {
      const [year, month] = period.split('-').map(Number);
      const to = new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10);
      return apiFetch<UsageReport>(
        `/reports/usage?${new URLSearchParams({
          scope,
          ...(scope === 'global' ? {} : { id }),
          from: `${period}-01`,
          to,
          granularity,
          by,
        })}`,
      );
    },
    enabled: enabled && period !== '' && id !== '',
    refetchInterval: 15000,
  });

export const useGrantsReport = (department?: string) =>
  useQuery({
    queryKey: ['reports', 'grants', department],
    queryFn: () =>
      apiFetch<GrantsReport>(
        `/reports/grants${department ? `?${new URLSearchParams({ department })}` : ''}`,
      ),
    refetchInterval: 15000,
  });

/** A calendar month stays within the live report's 31-local-date limit. */
export const useConcurrencyPeaks = (scope: UsageScope, id: string, period: string) =>
  useQuery({
    queryKey: ['reports', 'concurrency-peaks', scope, id, period],
    queryFn: () => {
      const [year, month] = period.split('-').map(Number);
      const to = new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10);
      return apiFetch<ConcurrencyPeaks>(
        `/reports/concurrency-peaks?${new URLSearchParams({
          scope,
          ...(scope === 'global' ? {} : { id }),
          from: `${period}-01`,
          to,
        })}`,
      );
    },
    enabled: period !== '' && id !== '',
    refetchInterval: 15000,
  });

export interface BlockSummaryData {
  by_reason: { reason: string; sub: string | null; count: number }[];
  by_consumer: { reason: string; consumer: string | null; count: number }[];
  truncated: boolean;
}

/** D-064: aggregate block reasons of a manager's own scope (the authorized report). */
export const useBlockSummary = (scope: 'group' | 'department', id: string, period: string) =>
  useQuery({
    queryKey: ['reports', 'blocks', scope, id, period],
    queryFn: () => {
      const [year, month] = period.split('-').map(Number);
      const to = new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10);
      return apiFetch<BlockSummaryData>(
        `/reports/blocks?${new URLSearchParams({ scope, id, from: `${period}-01`, to })}`,
      );
    },
    enabled: period !== '' && id !== '',
  });

export const useBlockedReport = (scope: UsageScope, id: string, period: string) =>
  useQuery({
    queryKey: ['reports', 'blocked', scope, id, period],
    queryFn: () => {
      const [year, month] = period.split('-').map(Number);
      const to = new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10);
      return apiFetch<BlockedReport>(
        `/reports/blocked?${new URLSearchParams({
          scope,
          ...(scope === 'global' ? {} : { id }),
          from: `${period}-01`,
          to,
        })}`,
      );
    },
    enabled: period !== '' && id !== '',
    refetchInterval: 15000,
  });

export interface MyStatus {
  blocks_available?: boolean;
  wallet: { state: string; remaining: number };
  limits: { max_parallel: number | null; rpm_limit: number | null };
  last_blocks: {
    at: string;
    model: string | null;
    reason: string;
    sub: string | null;
    retry_after: number | null;
    action: string;
  }[];
  taken_at: string;
}

export interface ServiceStatus {
  state: string;
  models: { model: string; state: string; checked_at: string }[];
  taken_at: string;
}

export const useMyStatus = () =>
  useQuery({
    queryKey: ['me-status'],
    queryFn: () => apiFetch<MyStatus>('/me/status'),
    refetchInterval: 15000,
  });

export const useServiceStatus = () =>
  useQuery({
    queryKey: ['service-status'],
    queryFn: () => apiFetch<ServiceStatus>('/status/service'),
    refetchInterval: 30000,
  });

/** Own spend by project tag for the period (D-059). */
export const useMyTagUsage = (upn: string, from: string, to: string) =>
  useQuery({
    queryKey: ['me-tag-usage', upn, from, to],
    queryFn: () =>
      apiFetch<{ series: { bucket: string; key: string; spend: number | null }[] }>(
        `/reports/usage?${new URLSearchParams({ scope: 'employee', id: upn, from, to, granularity: 'month', by: 'tag' })}`,
      ),
    enabled: upn !== '' && from !== '',
  });

export interface RequestItem {
  id: number;
  type: 'credits' | 'initiative' | 'throughput';
  status: string;
  requester_upn: string;
  handler_role: string | null;
  amount: number | string | null;
  payload: Record<string, unknown>;
  created_at: string;
  events?: { actor_upn: string; action: string; comment: string | null; created_at: string }[];
}

const awaitingApply = (r: Pick<RequestItem, 'status'>) => r.status === 'pending_apply';

export const useRequests = (box: 'mine' | 'to_handle', type: string, status: string) =>
  useQuery({
    queryKey: ['requests', box, type, status],
    queryFn: () =>
      apiFetch<{ items: RequestItem[] }>(
        `/requests?${new URLSearchParams({ box, ...(type && { type }), ...(status && { status }) })}`,
      ),
    // D-071: keep showing the real application state while the outbox applies a decision
    refetchInterval: (q) => (q.state.data?.items.some(awaitingApply) ? 3000 : false),
  });

export const useRequestDetail = (id: number | null) =>
  useQuery({
    queryKey: ['requests', 'detail', id],
    queryFn: () => apiFetch<RequestItem>(`/requests/${id}`),
    enabled: id !== null,
    refetchInterval: (q) => (q.state.data && awaitingApply(q.state.data) ? 3000 : false),
  });

export const useDecideRequest = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: {
      id: number;
      decision: string;
      comment?: string;
      amount?: number;
      // D-045: technical values the AI team supplies when applying a narrative request
      maxParallel?: number;
      rpmLimit?: number;
      modelTpmLimits?: Record<string, number>;
      key: string; // caller-owned idempotency key, kept across retries of one decision
    }) =>
      apiPost(
        `/requests/${v.id}/decide`,
        {
          decision: v.decision,
          comment: v.comment || undefined,
          amount: v.amount,
          max_parallel: v.maxParallel,
          rpm_limit: v.rpmLimit,
          model_tpm_limits: v.modelTpmLimits,
        },
        { reason: 'טיפול בבקשה', idempotencyKey: v.key },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['requests'] }),
  });
};

export const useSubmitRequest = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: {
      type: 'credits' | 'initiative' | 'throughput';
      need: string;
      maxParallel?: number;
      amount?: number;
      name?: string;
      target?: { kind: 'initiative' | 'system'; id: number }; // an existing subject (D-046..048)
      key: string; // caller-owned idempotency key, kept across retries of one submission
    }) =>
      apiPost(
        '/requests',
        {
          type: v.type,
          amount: v.amount,
          ...(v.target ? { target: v.target } : {}),
          payload: {
            need: v.need,
            ...(v.type === 'throughput' ? { max_parallel: v.maxParallel } : {}),
            ...(v.type === 'initiative' ? { name: v.name } : {}),
          },
        },
        { reason: 'בקשה מהמסך האישי', idempotencyKey: v.key },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['requests'] }),
  });
};

export interface BudgetRow {
  kind: 'employee' | 'group' | 'initiative';
  id: string;
  name: string;
  total: number;
  used: number;
  percent: number;
}

export interface BudgetOverview {
  period: string;
  kpis: {
    total: number;
    allocated: number;
    used: number;
    remaining: number;
    metered_value: number | null;
  };
  bar: { used: number; allocated_unused: number; unallocated: number; total: number };
  previous_period: { period: string; used: number };
  rows: BudgetRow[];
  actions: { edit_members: boolean };
}

export interface AttentionData {
  requests: { id: number; type: string; status: string; requester_upn: string }[];
  shortage_risks: { kind: string; id: string; name: string; percent: number }[];
  reallocation_opportunities: { grant_id: number; unused: number; model: string | null }[];
}

export interface TransferBody {
  group_id: number;
  source: { kind: string; id: string };
  target: { kind: string; id: string };
  amount: number;
}

export interface TransferPreview {
  source: { total: number; after: number; unused: number };
  target: { total: number; after: number };
  total_unchanged: boolean;
}

export const useBudgetOverview = (groupId: string, enabled = true) =>
  useQuery({
    enabled,
    queryKey: ['budget-overview', groupId],
    queryFn: () => apiFetch<BudgetOverview>(`/budget/overview?scope=group:${groupId}`),
  });

export const useAttention = (groupId: string) =>
  useQuery({
    queryKey: ['attention', groupId],
    queryFn: () => apiFetch<AttentionData>(`/attention?scope=group:${groupId}`),
  });

export const useTransferPreview = () =>
  useMutation({
    mutationFn: (body: TransferBody) =>
      apiPost<TransferPreview>('/transfers/preview', body, { reason: 'preview' }),
  });

export const useTransfer = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { body: TransferBody; reason: string; key: string }) =>
      apiPost<WriteResponse>('/transfers', v.body, { reason: v.reason, idempotencyKey: v.key }),
    onSuccess: () => qc.invalidateQueries(),
  });
};

/** D-071: a transfer's application state, refreshed until it reaches done or failed. */
export const useTransferStatus = (id: number | null) => {
  const qc = useQueryClient();
  return useQuery({
    queryKey: ['transfers', id],
    queryFn: async () => {
      const t = await apiFetch<{
        id: number;
        status: 'done' | 'pending_apply' | 'failed';
        state?: string;
      }>(`/transfers/${id}`);
      // Compensation changes the balances the creation already refreshed: once the transfer is
      // terminal, budget, allocation and attention data are read again.
      if (t.status !== 'pending_apply')
        void qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'transfers' });
      return t;
    },
    enabled: id !== null,
    refetchInterval: (q) => (q.state.data?.status === 'pending_apply' ? 3000 : false),
  });
};

export interface BulkBody {
  all: boolean;
  amount: number;
  overrides: Record<string, number>;
}

export interface BulkPreview {
  rows: { upn: string; before: number; after: number }[];
  allocated_after: number;
  base_budget: number;
  remaining_allocatable: number;
  over_allocation_warning: boolean;
  exceeds_limit: boolean;
}

export const useBulkPreview = (groupId: number) =>
  useMutation({
    mutationFn: (body: BulkBody) =>
      apiPost<BulkPreview>(`/groups/${groupId}/bulk-allocation/preview`, body, {
        reason: 'preview',
      }),
  });

export const useBulkAllocation = (groupId: number) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { body: BulkBody; reason: string; key: string; groupId?: number }) =>
      apiPost<WriteResponse>(`/groups/${v.groupId ?? groupId}/bulk-allocation`, v.body, {
        reason: v.reason,
        idempotencyKey: v.key,
      }),
    onSuccess: () => qc.invalidateQueries(),
  });
};

export interface DepartmentSystem {
  id: number;
  name: string;
  budget: number;
  used: number;
}

export interface DepartmentOverview {
  period: string;
  department: string;
  kpis: {
    total: number;
    allocated: number;
    used: number;
    remaining: number;
    metered_value: number | null;
  };
  bar: { used: number; allocated_unused: number; unallocated: number; total: number };
  previous_period: { period: string; used: number };
  sections: {
    id: number;
    name: string;
    total: number;
    used: number;
    percent: number;
    members: number;
    used_per_member: number | null;
    previous_used: number;
  }[];
  tree: { id: number; name: string; parent_group_id: number | null }[];
  internal_systems: DepartmentSystem[];
  org_systems: DepartmentSystem[];
  actions: { edit_hierarchy: boolean };
}

export const useDepartmentOverview = (departmentId: string, enabled = true) =>
  useQuery({
    enabled,
    queryKey: ['budget-overview', 'department', departmentId],
    queryFn: () =>
      apiFetch<DepartmentOverview>(`/budget/overview?scope=department:${departmentId}`),
  });

export type HierarchyChange =
  | { kind: 'below'; departmentId: number; parentId: number | null; name: string }
  | { kind: 'above'; groupId: number; name: string }
  | { kind: 'move'; groupId: number; parentId: number | null };

export const useHierarchyChange = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { change: HierarchyChange; reason: string; key: string }) => {
      const c = v.change;
      const write = { reason: v.reason, idempotencyKey: v.key };
      const team = (name: string) => ({
        name,
        litellm_team_id: `grp-${v.key.slice(0, 8)}`,
      });
      if (c.kind === 'below')
        return apiPost(
          '/groups',
          { ...team(c.name), department_id: c.departmentId, parent_group_id: c.parentId },
          write,
        );
      if (c.kind === 'above')
        return apiPost(`/groups/${c.groupId}/insert-parent`, team(c.name), write);
      return apiPatch(`/groups/${c.groupId}/parent`, { parent_group_id: c.parentId }, write);
    },
    onSuccess: () => qc.invalidateQueries(),
  });
};

export interface InitiativeView {
  id: number;
  name: string;
  owner_upn: string;
  group_id: number;
  period_start: string | null;
  period_end: string | null;
  budget: number;
  used: number; // attributed to the period, incl. keys since moved away
  status: string;
  tech_status: 'connected' | 'pending' | 'not_connected';
}

export const useMyInitiatives = () =>
  useQuery({
    queryKey: ['initiatives', 'mine'],
    queryFn: () => apiFetch<{ items: InitiativeView[] }>('/initiatives'),
  });

export interface OwnedSystem {
  id: number;
  name: string;
  budget: number;
  owner_upn: string | null;
  funding?: 'department' | 'organization';
  department_id?: number | null;
  used?: number; // system-level consumption with historical attribution
}

export const useMySystems = () =>
  useQuery({
    queryKey: ['systems', 'mine'],
    queryFn: () => apiFetch<{ items: OwnedSystem[] }>('/systems/mine'),
  });

export const useMoveKey = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { keyId: number; targetSystemId: number; key: string }) =>
      apiPost<WriteResponse>(
        `/service-keys/${v.keyId}/move`,
        { target_system_id: v.targetSystemId },
        { reason: 'העברת מפתח למערכות ארגוניות', idempotencyKey: v.key },
      ),
    onSuccess: () => qc.invalidateQueries(),
  });
};

export type OrgEnvelopeRow = {
  id: number;
  name: string;
  budget: number;
  previous_used?: number;
  used?: number;
  budget_period?: string;
  owner_upn?: string | null;
};
export type OrgEnvelopes = {
  organization: { id: number; name: string };
  initiatives: OrgEnvelopeRow[];
  departments: { envelope: number; allocated: number; rows: OrgEnvelopeRow[] };
  systems: { envelope: number; allocated: number; rows: OrgEnvelopeRow[] };
};

export const useOrgEnvelopes = (orgId: number, enabled = true) =>
  useQuery({
    enabled,
    queryKey: ['org-envelopes', orgId],
    queryFn: () => apiFetch<OrgEnvelopes>(`/organizations/${orgId}/envelopes`),
  });

/** One allocation write (department or Org System budget); refreshes both envelopes. */
export const useOrgAllocate = (orgId: number) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: {
      kind: 'department' | 'system';
      id: number;
      budget: number;
      budgetPeriod?: string;
      reason: string;
    }) => {
      const path =
        v.kind === 'department'
          ? `/organizations/${orgId}/departments/${v.id}/budget`
          : `/organizations/${orgId}/systems/${v.id}/budget`;
      const body =
        v.kind === 'department'
          ? { budget: v.budget, budget_period: v.budgetPeriod ?? '1mo' }
          : { budget: v.budget };
      const write = { reason: v.reason, idempotencyKey: crypto.randomUUID() };
      // The key is kept so the same write can be replayed to read its synchronization state.
      return apiPut<WriteResponse>(path, body, write).then((result) => ({
        result,
        replay: () => apiPut<WriteResponse>(path, body, write),
      }));
    },
    onSuccess: () => qc.invalidateQueries(),
  });
};

export const useCreateOrganization = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { name: string; reason: string; key: string }) =>
      apiPost<WriteResponse>(
        '/organizations',
        { name: v.name },
        { reason: v.reason, idempotencyKey: v.key },
      ),
    onSuccess: () => qc.invalidateQueries(),
  });
};
