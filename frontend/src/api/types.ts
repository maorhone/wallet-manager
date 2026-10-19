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
  outbox: { pending: number; failed: number; oldest_pending_at: string | null };
  spend_taken_at: string | null;
  last_sync: string | null;
}

export type SyncStatus = 'pending' | 'applied' | 'failed';

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
  group: { id: number; name: string; default_member_cap: number };
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
  actions: { edit_members: boolean; edit_pots_limits: boolean; edit_defaults: boolean };
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

export type Policies = Record<string, { min?: number; default?: number; max?: number }>;
