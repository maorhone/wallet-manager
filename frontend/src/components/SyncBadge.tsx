import { Badge, Button, Group } from '@mantine/core';
import { useMe, useRetryOutbox } from '../api/queries';
import type { SyncStatus } from '../api/types';
import { he } from '../i18n/he';

const ICON: Record<SyncStatus, string> = { pending: '⏳', applied: '✓', failed: '⚠' };
const COLOR: Record<SyncStatus, string> = { pending: 'blue', applied: 'gray', failed: 'red' };

/** Text and icon, never color alone (§12.1). Admins get a retry on failed rows. */
export function SyncBadge({
  status,
  outboxId,
}: {
  status: SyncStatus;
  outboxId?: number | string;
}) {
  const { data: me } = useMe();
  const retry = useRetryOutbox();
  const isAdmin = me?.scopes.some((s) => s.kind === 'platform') ?? false;
  return (
    <Group gap={4} wrap="nowrap" display="inline-flex">
      <Badge color={COLOR[status]} variant="light" data-testid="sync-badge" data-status={status}>
        <span aria-hidden="true">{ICON[status]}</span> {he.sync[status]}
      </Badge>
      {status === 'failed' && isAdmin && outboxId !== undefined && (
        <Button
          size="compact-xs"
          variant="default"
          onClick={() => retry.mutate({ id: outboxId, reason: he.retry })}
        >
          {he.retry}
        </Button>
      )}
    </Group>
  );
}
