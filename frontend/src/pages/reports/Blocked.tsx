import { Card, Loader, Stack, Table, Text } from '@mantine/core';
import { useBlockedReport } from '../../api/queries';
import type { UsageScope } from '../../api/types';
import { Credits } from '../../components/Credits';
import { EmptyState } from '../../components/EmptyState';
import { ErrorState } from '../../components/ErrorState';
import { Num } from '../../components/Num';
import { he } from '../../i18n/he';

export default function Blocked({
  scope,
  id,
  period,
}: {
  scope: UsageScope;
  id: string;
  period: string;
}) {
  const report = useBlockedReport(scope, id, period);
  if (report.isPending) return <Loader aria-label={he.loading} />;
  if (report.error || !report.data)
    return <ErrorState error={report.error} onRetry={report.refetch} />;
  const t = he.reports;
  return (
    <Stack>
      <Text size="sm" c="dimmed">
        {t.currentBlockedCaps}
      </Text>
      <Card withBorder data-testid="blocked-report">
        {report.data.rows.length === 0 ? (
          <EmptyState />
        ) : (
          <Table.ScrollContainer minWidth={800}>
            <Table>
              <Table.Thead>
                <Table.Tr>
                  {[
                    t.day,
                    t.employee,
                    t.capKind,
                    he.wallet.model,
                    t.cap,
                    t.periodSpend,
                    t.daySpend,
                  ].map((label) => (
                    <Table.Th key={label}>{label}</Table.Th>
                  ))}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {report.data.rows.map((row) => (
                  <Table.Tr key={`${row.day}:${row.upn}:${row.kind}:${row.model}`}>
                    <Table.Td>
                      <Num>{row.day}</Num>
                    </Table.Td>
                    <Table.Td>
                      {row.display_name}
                      <Text size="xs">
                        <Num>{row.upn}</Num>
                      </Text>
                    </Table.Td>
                    <Table.Td>{row.kind === 'base' ? t.baseCap : he.wallet.model}</Table.Td>
                    <Table.Td>
                      <Num>{row.model ?? '–'}</Num>
                    </Table.Td>
                    <Table.Td>
                      <Credits value={row.cap} />
                    </Table.Td>
                    <Table.Td>
                      <Credits value={row.period_spend_to_date} />
                    </Table.Td>
                    <Table.Td>
                      <Credits value={row.day_spend} />
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Card>
    </Stack>
  );
}
