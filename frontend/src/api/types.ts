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
