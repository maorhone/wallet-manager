import { Badge } from '@mantine/core';
import type { ApiState } from '../api/types';
import { he } from '../i18n/he';

export type WalletState =
  'healthy' | 'near' | 'blockedOwn' | 'blockedGroup' | 'blockedDept' | 'overAllocated' | 'pending';

/** API state → badge; `muted` (left members) has none. */
export const API_STATE: Record<ApiState, WalletState | null> = {
  healthy: 'healthy',
  near_limit: 'near',
  blocked_own: 'blockedOwn',
  blocked_group: 'blockedGroup',
  blocked_department: 'blockedDept',
  over_allocated: 'overAllocated',
  muted: null,
};

const LOOK: Record<WalletState, { color: string; icon: string }> = {
  healthy: { color: 'gray', icon: '●' },
  near: { color: 'yellow', icon: '▲' },
  blockedOwn: { color: 'red', icon: '■' },
  blockedGroup: { color: 'red', icon: '■' },
  blockedDept: { color: 'red', icon: '■' },
  overAllocated: { color: 'yellow', icon: '▲' },
  pending: { color: 'blue', icon: '⏳' },
};

/** §12.4 badge: label plus icon, so color is never the only signal. */
export function StateBadge({ state }: { state: WalletState }) {
  const { color, icon } = LOOK[state];
  return (
    <Badge color={color} variant="light" data-state={state}>
      <span aria-hidden="true">{icon}</span> {he.state[state]}
    </Badge>
  );
}
