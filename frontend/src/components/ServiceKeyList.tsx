import { Badge, Button, Card, Group, Table } from '@mantine/core';
import type { ServiceKey } from '../api/types';
import { Credits } from './Credits';
import { EmptyState } from './EmptyState';
import { Num } from './Num';
import { SyncBadge } from './SyncBadge';
import { he } from '../i18n/he';

const t = he.serviceKeys;
const STATUS_COLOR: Record<string, string> = {
  pending: 'blue',
  active: 'green',
  revoked: 'gray',
  failed: 'red',
};

/** §12.5.2 tab 5 / §12.5.4 tab 6: alias, budget, concurrency, used, status; rotate/revoke from the server `actions`. */
export function ServiceKeyList({
  items,
  onRotate,
  onRevoke,
  manage,
}: {
  items: ServiceKey[];
  manage: boolean;
  onRotate: (key: ServiceKey) => void;
  onRevoke: (key: ServiceKey) => void;
}) {
  return (
    <Card withBorder data-testid="service-key-list">
      {items.length === 0 ? (
        <EmptyState message={t.empty} />
      ) : (
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t.alias}</Table.Th>
              <Table.Th>{t.budget}</Table.Th>
              <Table.Th>{t.used}</Table.Th>
              <Table.Th>{t.concurrency}</Table.Th>
              <Table.Th>{t.rpm}</Table.Th>
              <Table.Th>{t.models}</Table.Th>
              <Table.Th>{t.status}</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {items.map((k) => (
              <Table.Tr key={k.id} data-testid={`service-key-${k.id}`} data-alias={k.alias}>
                <Table.Td>
                  <Num>{k.alias}</Num>
                </Table.Td>
                <Table.Td>
                  <Credits value={k.max_budget} />
                </Table.Td>
                <Table.Td>
                  <Credits value={k.used} />
                </Table.Td>
                <Table.Td>{k.max_parallel}</Table.Td>
                <Table.Td>{k.rpm_limit}</Table.Td>
                <Table.Td>
                  {k.models.length > 0 ? <Num>{k.models.join(', ')}</Num> : t.allModels}
                </Table.Td>
                <Table.Td>
                  <Group gap={4} wrap="nowrap">
                    <Badge
                      variant="light"
                      color={STATUS_COLOR[k.status]}
                      data-testid={`service-key-status-${k.id}`}
                    >
                      {t.statuses[k.status] ?? k.status}
                    </Badge>
                    <SyncBadge status={k.sync_status} />
                  </Group>
                </Table.Td>
                <Table.Td>
                  <Group gap={4} wrap="nowrap">
                    {manage && k.actions.rotate && (
                      <Button
                        size="compact-xs"
                        variant="default"
                        data-testid={`service-key-rotate-${k.id}`}
                        onClick={() => onRotate(k)}
                      >
                        {t.rotate}
                      </Button>
                    )}
                    {manage && k.actions.revoke && (
                      <Button
                        size="compact-xs"
                        color="red"
                        data-testid={`service-key-revoke-${k.id}`}
                        onClick={() => onRevoke(k)}
                      >
                        {t.revoke}
                      </Button>
                    )}
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Card>
  );
}
