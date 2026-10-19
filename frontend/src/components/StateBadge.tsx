import { Badge } from '@mantine/core';
import { he } from '../i18n/he';

export type WalletState =
  'healthy' | 'near' | 'blockedOwn' | 'blockedGroup' | 'blockedDept' | 'overAllocated' | 'pending';

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
