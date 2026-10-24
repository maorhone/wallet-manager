export interface ErrorEnvelope {
  code: string;
  message?: string;
  requested?: number;
  available?: number;
  parent_total?: number;
  details?: Record<string, unknown>;
  request_id?: string;
}

export interface Scope {
  kind: 'self' | 'group' | 'department' | 'platform';
  id?: number | null;
  name?: string | null;
  role?: string | null;
}

export interface Me {
  upn: string;
  display_name: string;
  group: { id: number; name: string } | null;
  department: { id: number; name: string } | null;
  roles: {
    role: string;
    scope_kind: string;
    scope_id?: number | null;
    scope_name?: string | null;
  }[];
  scopes: Scope[];
  timezone: string;
  current_period: string;
  dev_auth: boolean;
}

export interface Status {
  litellm_reachable: boolean;
  redis: 'not_configured' | 'connected' | 'unreachable' | 'unknown';
  litellm_version: string | null;
  pending_placements: number | null;
  outbox: { pending: number; failed: number; oldest_pending_at: string | null };
  spend_taken_at: string | null;
  last_sync: string | null;
}

export type SyncStatus = 'pending' | 'applied' | 'failed';

export interface OutboxEntry {
  id: number;
  entity: string;
  entity_id: string;
  endpoint: string;
  status: SyncStatus;
  attempts: number;
  last_error: string | null;
  idempotency_key: string;
  created_at: string;
}

export interface WriteEntity {
  entity: string;
  id: number | string;
  sync_status: SyncStatus;
  values: Record<string, unknown>;
}

export interface WriteResponse {
  request_id: string;
  status: 'pending' | 'applied';
  entities: WriteEntity[];
}

export interface DevUser {
  upn: string;
  display_name: string;
  roles: string[];
}

export type ApiState =
  | 'healthy'
  | 'near_limit'
  | 'blocked_own'
  | 'blocked_group'
  | 'blocked_department'
  | 'over_allocated'
  | 'muted';

export interface Person {
  upn: string;
  display_name: string;
}

export interface Owner {
  role: string;
  people: Person[];
}

export interface MyWallet {
  period: string;
  reset_date: string;
  spend_taken_at: string | null;
  read_only: boolean;
  sync_status: SyncStatus;
  wallet: {
    total: number;
    base_cap: number;
    shares_total: number;
    used: number;
    remaining: number;
    percent: number;
    state: ApiState;
    effective_remaining: number;
    binding: {
      level: 'self' | 'group' | 'department';
      group_percent: number;
      department_percent: number;
    };
  };
  models: { model: string; cap: number | null; used: number; remaining: number | null }[];
  shares: {
    id: number;
    grant_id: number;
    project_ref: string;
    amount: number;
    used?: number;
    expires_at: string | null;
  }[];
  limits: { max_parallel: number | null; rpm_limit: number | null };
  group: { id: number; name: string; percent: number; state: ApiState };
  department: { id: number; name: string; percent: number };
  owners: Record<'base_cap' | 'model_caps' | 'limits' | 'shares', Owner>;
}

export interface UsagePoint {
  day: string;
  model: string;
  spend: number;
}

interface PeriodEnvelope {
  period: string;
  reset_date: string;
  spend_taken_at: string | null;
  read_only: boolean;
}

export interface MemberSlim {
  upn: string;
  display_name: string;
  total: number;
  used: number;
  percent: number;
  state: ApiState;
}

export interface GroupSummary extends PeriodEnvelope {
  group: { id: number; department_id: number; name: string; default_member_cap: number };
  wallet: {
    total: number;
    allocated: number;
    unallocated: number;
    used: number;
    remaining: number;
    percent: number;
    state: ApiState;
    base: number;
    grants: number;
  };
  pots: { model: string; amount: number; used: number; members_caps_sum: number }[];
  grants: { id: number; amount: number; model: string | null; project_ref: string }[];
  top_consumers: MemberSlim[];
  near_limit_members: MemberSlim[];
  pending_sync_count: number;
  actions: {
    edit_members: boolean;
    edit_pots_limits: boolean;
    edit_defaults: boolean;
    service_keys: { view: boolean; manage: boolean };
  };
}

/** One group head's personal values (DR9) with the actor's rights on them. */
export interface GroupHead {
  upn: string;
  display_name: string;
  group_id: number;
  group_name: string;
  base_cap: number;
  shares: number;
  model_caps: Record<string, number>;
  max_parallel: number | null;
  rpm_limit: number | null;
  model_tpm_limits: Record<string, number>;
  sync_status: SyncStatus;
  actions: { edit: boolean; share: boolean };
}

export interface DepartmentSummary extends PeriodEnvelope {
  department: { id: number; name: string };
  wallet: {
    total: number;
    allocated: number;
    unallocated: number;
    used: number;
    remaining: number;
    percent: number;
    state: ApiState;
  };
  split: {
    groups: { id: number; name: string; kind: string; base: number }[];
    pmo_pool: number;
    reserve: number;
    unallocated: number;
    pmo_reserve_sync_status: SyncStatus;
  };
  groups: {
    id: number;
    name: string;
    kind: string;
    overalloc_factor: number;
    base: number;
    grants: number;
    total: number;
    used: number;
    percent: number;
    state: ApiState;
    sync_status: SyncStatus;
  }[];
  allowances: { model: string; amount: number; pots_sum: number }[];
  group_heads: GroupHead[];
  actions: {
    edit_split: boolean;
    edit_budget: boolean;
    edit_allowances: boolean;
    place: boolean;
    view_production: boolean;
    edit_production: boolean;
  };
}

export interface Member extends MemberSlim {
  base_cap: number;
  shares_total: number;
  model_caps: Record<string, number>;
  max_parallel: number | null;
  rpm_limit: number | null;
  model_tpm_limits: Record<string, number>;
  status: string;
  is_group_head: boolean;
  sync_status: SyncStatus;
  editable: boolean;
  owner: Owner;
  actions: Record<MemberActionKind, boolean>;
}

export interface PendingPlacement {
  upn: string;
  display_name: string;
  reason: string | null;
  pending_since: string | null;
  department_id: number | null;
  department_name: string | null;
}

export type MemberActionKind = 'kick' | 'restore' | 'move' | 'delete';

export interface MoveTarget {
  group_id: number;
  name: string;
  department_id: number;
  department_name: string;
}

export interface MembersList extends PeriodEnvelope {
  members: Member[];
}

export interface AuditEntry {
  id: number;
  at: string;
  actor_upn: string;
  actor_role: string | null;
  entity: string;
  entity_id: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  reason: string | null;
  request_id: string | null;
  litellm_calls: {
    endpoint: string;
    status: string;
    attempts: number;
    last_error: string | null;
  }[];
}

export interface PmoPool {
  department_id: number;
  period: string;
  pool: number;
  active_grants: number;
  used: number;
  returned: number;
  available: number;
  actions: { create_grant: boolean };
}

export interface Grant {
  id: number;
  department_id: number;
  group_id: number;
  group_name: string;
  project_ref: string;
  purpose: string;
  amount: number;
  model: string | null;
  starts_at: string;
  expires_at: string;
  status: 'pending' | 'active' | 'closed';
  shared: number;
  unshared: number;
  used: number;
  returned: number;
  days_left: number | null;
  sync_status: SyncStatus;
  actions: { edit: boolean; close: boolean; share: boolean };
}

export interface GrantIn {
  group_id: number;
  amount: number;
  model: string | null;
  starts_at: string;
  expires_at: string;
  project_ref: string;
  purpose: string;
}

export interface ProjectSummary {
  project_ref: string;
  totals: { amount: number; shared: number; used: number; returned: number };
  grants: Grant[];
}

export interface GrantShare {
  id: number;
  upn: string;
  display_name: string;
  amount: number;
  expires_at: string;
  status: string;
  used: number;
  returned: number;
  closed_reason: string | null;
  sync_status: SyncStatus;
  actions: { edit: boolean; close: boolean };
}

export interface AppNotification {
  id: number;
  kind: string;
  critical: boolean;
  scope_kind: string | null;
  scope_id: number | null;
  payload: Record<string, unknown>;
  created_at: string;
  read_at: string | null;
}

export interface NotificationPref {
  kind: string;
  critical: boolean;
  muted: boolean;
}

export interface NotificationsList {
  items: AppNotification[];
  unread_count: number;
  prefs: NotificationPref[];
}

export type ServiceKeyStatus = 'pending' | 'active' | 'revoked' | 'failed';

export interface ServiceKey {
  id: number;
  owner_kind: 'group' | 'system' | 'initiative';
  owner_id: number;
  alias: string;
  max_budget: number;
  max_parallel: number;
  rpm_limit: number;
  models: string[];
  status: ServiceKeyStatus;
  used: number;
  sync_status: SyncStatus;
  created_at: string;
  actions: { rotate: boolean; revoke: boolean };
}

export interface ServiceKeysList {
  items: ServiceKey[];
  spend_taken_at: string | null;
}

export interface ServiceKeyIn {
  owner_kind: 'group' | 'system';
  owner_id: number;
  alias: string;
  max_budget: number;
  max_parallel: number;
  rpm_limit: number;
  models: string[];
}

/** Create and rotate responses carry the one-time secret; it is never cached. */
export interface ServiceKeyWriteResponse extends WriteResponse {
  secret?: string;
}

export interface PolicyRange {
  min: number;
  default: number;
  max: number;
}

export interface Policies {
  concurrency: PolicyRange;
  rpm: PolicyRange;
  overalloc_factor_default: number;
  thresholds: { near?: number; full?: number };
}

export type PoliciesIn = Partial<Policies> & { overalloc_factors?: Record<string, number> };

export interface AuditFilters {
  entity?: string;
  id?: string;
  actor?: string;
  from?: string;
  to?: string;
}

export interface DepartmentRecord {
  id: number;
  name: string;
  budget: number;
  budget_period: string;
}

export interface DepartmentIn {
  name: string;
  litellm_org_id: string;
  ad_group_dn: string | null;
  budget: number;
  budget_period: string;
}

export interface GroupIn {
  name: string;
  litellm_team_id: string;
  ad_group_dn: string | null;
  kind: 'regular' | 'department_staff';
}

export interface RoleBindingScope {
  scope_kind: 'global' | 'department' | 'group';
  scope_id?: number;
}

export interface RoleBindingIn extends RoleBindingScope {
  role: string;
  ad_group_dns: string[];
}

export interface RoleBinding extends RoleBindingIn {
  members: Person[];
}

export type AllocationLevel = 'department' | 'group' | 'employee';

export interface ModelAllocation {
  model: string;
  allocation: number | null;
  spend: number;
  pct: number | null;
}

export interface AllocationValues {
  total: number;
  allocated: number;
  spend: number;
  remaining: number;
  pct: number;
  state: ApiState;
}

export interface AllocationRow extends AllocationValues {
  id: number | string;
  name: string;
  kind: string;
  base: number | null;
  grants: number | null;
  base_cap: number | null;
  shares_total: number | null;
  model_caps: Record<string, number> | null;
  models: ModelAllocation[];
}

export interface AllocationReport extends AllocationValues {
  level: AllocationLevel;
  id: number | string;
  name: string;
  period: string;
  rows: AllocationRow[];
  models: ModelAllocation[];
  spend_taken_at: string | null;
}

export interface OverallocationFinding {
  source: 'wm' | 'litellm';
  level: 'department' | 'group' | 'group_model' | 'member_model';
  entity_id: number | string;
  name: string | null;
  allocated: number;
  total: number;
  department_id: number | null;
  model: string | null;
}

export type UsageScope = AllocationLevel | 'global';
export type UsageGranularity = 'day' | 'week' | 'month';
export type UsageBy = 'model' | 'member';
export interface UsageSeriesPoint {
  bucket: string;
  key: string;
  spend: number;
}
export interface UsageReport {
  series: UsageSeriesPoint[];
  cap: number;
  taken_at: string | null;
}

export interface ConcurrencyPeaks {
  daily: { day: string; peak: number; at: string }[];
  members: {
    upn: string;
    display_name: string | null;
    peak: number;
    at: string;
    max_parallel: number | null;
  }[];
}

export interface GrantTotals {
  departments: number;
  groups: number;
  grants: number;
  amount: number;
  shared: number;
  unshared: number;
  used: number;
  returned: number;
}

export interface GrantsReport {
  rows: (GrantTotals & { project_ref: string | null })[];
  totals: GrantTotals;
}

export interface BlockedReport {
  rows: {
    day: string;
    upn: string;
    display_name: string | null;
    group_id: number;
    kind: 'base' | 'model';
    model: string | null;
    cap: number;
    period_spend_to_date: number;
    day_spend: number;
  }[];
}

export interface ProductionOrg {
  litellm_org_id: string;
  budget: number;
  budget_period: string;
  used: number;
  over_allocated: boolean;
  sync_status: SyncStatus;
}

export interface ProductionSystemRow {
  id: number;
  name: string;
  litellm_team_id: string;
  budget: number;
  used: number;
  keys_count: number;
  sync_status: SyncStatus;
}

export interface Production {
  department_id: number;
  org: ProductionOrg | null;
  systems: ProductionSystemRow[];
  spend_taken_at: string | null;
  read_only: boolean;
  actions: { edit: boolean };
}

export interface ProductionOrgIn {
  budget: number;
  budget_period?: string;
}

export interface ProductionSystemIn {
  name: string;
  budget: number;
}
