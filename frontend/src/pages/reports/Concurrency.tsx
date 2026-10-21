import { Card, Loader, Stack, Table, Text, Title } from '@mantine/core';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useConcurrencyPeaks } from '../../api/queries';
import type { UsageScope } from '../../api/types';
import { EmptyState } from '../../components/EmptyState';
import { ErrorState } from '../../components/ErrorState';
import { Num } from '../../components/Num';
import { he } from '../../i18n/he';

export default function Concurrency({
  scope,
  id,
  period,
  canViewMembers,
}: {
  scope: UsageScope;
  id: string;
  period: string;
  canViewMembers: boolean;
}) {
  const report = useConcurrencyPeaks(scope, id, period);
  if (report.isPending) return <Loader aria-label={he.loading} />;
  if (report.error || !report.data)
    return <ErrorState error={report.error} onRetry={report.refetch} />;
  const t = he.reports;
  return (
    <Stack>
      <Text size="sm" c="dimmed">
        {t.livePeaks}
      </Text>
      <Card withBorder data-testid="concurrency-chart">
        <Title order={5}>{t.dailyPeak}</Title>
        {report.data.daily.length === 0 ? (
          <EmptyState />
        ) : (
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={report.data.daily} margin={{ top: 24, right: 24, left: 12 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="day" tickFormatter={(day: string) => day.slice(5)} />
              <YAxis allowDecimals={false} orientation="right" domain={[0, 'auto']} />
              <Tooltip />
              <Line
                type="linear"
                dataKey="peak"
                name={t.peak}
                stroke="#1c7ed6"
                label={{ position: 'top' }}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </Card>
      {canViewMembers && (
        <Card withBorder data-testid="concurrency-members">
          <Title order={5}>{t.topEmployees}</Title>
          {report.data.members.length === 0 ? (
            <EmptyState />
          ) : (
            <Table.ScrollContainer minWidth={700}>
              <Table>
                <Table.Thead>
                  <Table.Tr>
                    {[t.employee, t.peak, t.maxParallel, t.peakAtUtc].map((label) => (
                      <Table.Th key={label}>{label}</Table.Th>
                    ))}
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {report.data.members.map((row) => (
                    <Table.Tr key={row.upn}>
                      <Table.Td>
                        {row.display_name}
                        <Text size="xs">
                          <Num>{row.upn}</Num>
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Num>{row.peak}</Num>
                      </Table.Td>
                      <Table.Td>
                        <Num>{row.max_parallel ?? '–'}</Num>
                      </Table.Td>
                      <Table.Td>
                        <Num>{row.at}</Num>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          )}
        </Card>
      )}
    </Stack>
  );
}
