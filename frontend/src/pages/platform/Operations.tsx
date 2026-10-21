import { Button, Card, Loader, NativeSelect, Stack, Table, Text, Title } from '@mantine/core';
import { useState } from 'react';
import {
  useMe,
  useOutbox,
  useReconciliationAudit,
  useRetryOutbox,
  useStatus,
} from '../../api/queries';
import type { OutboxEntry, SyncStatus } from '../../api/types';
import { ChangeSummaryDialog } from '../../components/ChangeSummaryDialog';
import { DateText } from '../../components/DateText';
import { EmptyState } from '../../components/EmptyState';
import { ErrorState } from '../../components/ErrorState';
import { HistoryTimeline } from '../../components/HistoryTimeline';
import { Num } from '../../components/Num';
import { SyncBadge } from '../../components/SyncBadge';
import { freshness } from '../../format';
import { he } from '../../i18n/he';

const t = he.operations;

export default function Operations() {
  const { data: me } = useMe();
  const status = useStatus();
  const [filter, setFilter] = useState<SyncStatus | ''>('failed');
  const outbox = useOutbox(filter);
  const audit = useReconciliationAudit();
  const retry = useRetryOutbox();
  const [selected, setSelected] = useState<OutboxEntry | null>(null);
  const data = status.data;

  return (
    <Stack>
      <Card withBorder data-testid="operations-status">
        <Title order={4} mb="sm">
          {t.status}
        </Title>
        {status.error ? (
          <ErrorState error={status.error} onRetry={status.refetch} />
        ) : !data ? (
          <Loader aria-label={he.loading} />
        ) : (
          <Stack gap="xs">
            <Text>
              {t.litellm}:{' '}
              <span data-testid="litellm-status">
                {data.litellm_reachable ? t.reachable : t.unreachable}
              </span>
            </Text>
            <Text>
              {t.redis}: <span data-testid="redis-status">{t.redisStates[data.redis]}</span>
            </Text>
            <Text>
              {t.depth}: <Num>{data.outbox.pending + data.outbox.failed}</Num>
            </Text>
            <Text>
              {t.pending}:{' '}
              <span data-testid="outbox-pending">
                <Num>{data.outbox.pending}</Num>
              </span>
            </Text>
            <Text>
              {t.failed}:{' '}
              <span data-testid="outbox-failed">
                <Num>{data.outbox.failed}</Num>
              </span>
            </Text>
            <Text>
              {t.pendingPlacements}:{' '}
              <span data-testid="pending-placements">
                <Num>{data.pending_placements ?? '–'}</Num>
              </span>
            </Text>
            <Text>
              {t.spendFreshness}:{' '}
              <span data-testid="spend-freshness">
                {data.spend_taken_at ? (
                  <>
                    {freshness(data.spend_taken_at)}{' '}
                    <Num>
                      <time dateTime={data.spend_taken_at}>
                        {new Date(data.spend_taken_at).toLocaleString('he-IL', {
                          timeZone: me?.timezone ?? 'Asia/Jerusalem',
                        })}
                      </time>
                    </Num>
                  </>
                ) : (
                  t.noData
                )}
              </span>
            </Text>
          </Stack>
        )}
      </Card>

      <Card withBorder>
        <Title order={4} mb="sm">
          {t.outbox}
        </Title>
        <NativeSelect
          label={t.filter}
          value={filter}
          onChange={(event) => setFilter(event.currentTarget.value as SyncStatus | '')}
          data={[
            { value: '', label: t.all },
            ...Object.entries(he.sync).map(([value, label]) => ({ value, label })),
          ]}
          mb="sm"
        />
        {outbox.error ? (
          <ErrorState error={outbox.error} onRetry={outbox.refetch} />
        ) : !outbox.data ? (
          <Loader aria-label={he.loading} />
        ) : outbox.data.length === 0 ? (
          <EmptyState />
        ) : (
          <Table.ScrollContainer minWidth={900}>
            <Table>
              <Table.Thead>
                <Table.Tr>
                  {[
                    t.id,
                    t.entity,
                    t.endpoint,
                    t.state,
                    t.attempts,
                    t.error,
                    t.created,
                    t.retry,
                  ].map((label) => (
                    <Table.Th key={label}>{label}</Table.Th>
                  ))}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {outbox.data.map((row) => (
                  <Table.Tr key={row.id} data-testid={`outbox-row-${row.id}`}>
                    <Table.Td>
                      <Num>{row.id}</Num>
                    </Table.Td>
                    <Table.Td>
                      <Num>
                        {row.entity} {row.entity_id}
                      </Num>
                    </Table.Td>
                    <Table.Td>
                      <Num>{row.endpoint}</Num>
                    </Table.Td>
                    <Table.Td>
                      <SyncBadge status={row.status} />
                    </Table.Td>
                    <Table.Td>
                      <Num>{row.attempts}</Num>
                    </Table.Td>
                    <Table.Td data-testid="outbox-error">
                      <Text size="sm" style={{ overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>
                        {row.last_error ?? '–'}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <DateText value={row.created_at} />
                    </Table.Td>
                    <Table.Td>
                      {row.status !== 'applied' && (
                        <Button
                          size="compact-sm"
                          variant="default"
                          onClick={() => setSelected(row)}
                        >
                          {t.retry}
                        </Button>
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Card>

      <Card withBorder>
        <Title order={4} mb="sm">
          {t.drift}
        </Title>
        {audit.error ? (
          <ErrorState error={audit.error} onRetry={audit.refetch} />
        ) : !audit.data ? (
          <Loader aria-label={he.loading} />
        ) : (
          <HistoryTimeline entries={audit.data} />
        )}
      </Card>
      {selected && (
        <ChangeSummaryDialog
          opened
          rows={[
            {
              key: String(selected.id),
              label: (
                <Num>
                  {selected.id} {selected.endpoint}
                </Num>
              ),
              before: he.sync[selected.status],
              after: he.sync.pending,
            },
          ]}
          onClose={() => setSelected(null)}
          onConfirm={(reason, idempotencyKey) =>
            retry.mutateAsync({ id: selected.id, reason, idempotencyKey })
          }
        />
      )}
    </Stack>
  );
}
