import { Group, Loader, Stack, Text } from '@mantine/core';
import { usePmoPool } from '../../api/queries';
import type { DepartmentSummary } from '../../api/types';
import { Credits } from '../../components/Credits';
import { ErrorState } from '../../components/ErrorState';
import { SyncBadge } from '../../components/SyncBadge';
import { WalletCard } from '../../components/WalletCard';
import { he } from '../../i18n/he';

/** Tab "קופה" (§12.5.3 tab 1): pool size, active grants, used, returned, available. */
export default function Pool({ summary, period }: { summary: DepartmentSummary; period: string }) {
  const pool = usePmoPool(String(summary.department.id), period);
  if (pool.isPending) return <Loader aria-label={he.loading} />;
  if (pool.error || !pool.data) return <ErrorState error={pool.error} onRetry={pool.refetch} />;
  const p = pool.data;
  return (
    <Stack data-testid="pmo-pool">
      <WalletCard
        title={he.pmo.pool}
        total={p.pool}
        used={p.used}
        remaining={p.available}
        percent={p.pool > 0 ? p.used / p.pool : 0}
        resetDate={summary.reset_date}
        takenAt={summary.spend_taken_at}
        allocated={p.active_grants}
        unallocated={p.available}
      />
      <Group gap="xl" data-testid="pool-stats">
        {(
          [
            ['available', he.pmo.available, p.available],
            ['active', he.pmo.activeGrants, p.active_grants],
            ['used', he.pmo.used, p.used],
            ['returned', he.pmo.returned, p.returned],
          ] as const
        ).map(([k, label, v]) => (
          <Text key={k} data-testid={`pool-${k}`} data-value={v}>
            {label}: <Credits value={v} />
          </Text>
        ))}
        <SyncBadge status={summary.split.pmo_reserve_sync_status} />
      </Group>
    </Stack>
  );
}
