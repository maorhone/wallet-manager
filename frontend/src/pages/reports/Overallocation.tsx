import { Card, Loader, Stack, Table, Text } from '@mantine/core';
import { useOverallocationReport } from '../../api/queries';
import { Credits } from '../../components/Credits';
import { EmptyState } from '../../components/EmptyState';
import { ErrorState } from '../../components/ErrorState';
import { Num } from '../../components/Num';
import { StateBadge } from '../../components/StateBadge';
import { he } from '../../i18n/he';

export default function Overallocation({ department }: { department?: string }) {
  const report = useOverallocationReport(department);
  if (report.isPending) return <Loader aria-label={he.loading} />;
  if (report.error || !report.data)
    return <ErrorState error={report.error} onRetry={report.refetch} />;
  const t = he.reports;
  return (
    <Stack>
      <Text size="sm" c="dimmed">
        {t.currentFindings}
      </Text>
      <Card withBorder data-testid="overallocation-table">
        {report.data.length === 0 ? (
          <EmptyState />
        ) : (
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t.source}</Table.Th>
                <Table.Th>{t.level}</Table.Th>
                <Table.Th>{t.entity}</Table.Th>
                <Table.Th>{he.wallet.model}</Table.Th>
                <Table.Th>{he.wallet.allocated}</Table.Th>
                <Table.Th>{t.ceiling}</Table.Th>
                <Table.Th>{he.groupPage.status}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {report.data.map((f) => (
                <Table.Tr key={`${f.source}:${f.level}:${f.entity_id}:${f.model}`}>
                  <Table.Td>
                    {f.source === 'litellm' ? <Num>{t.sources.litellm}</Num> : t.sources.wm}
                  </Table.Td>
                  <Table.Td>{t.levels[f.level]}</Table.Td>
                  <Table.Td>{f.name ?? <Num>{f.entity_id}</Num>}</Table.Td>
                  <Table.Td>
                    <Num>{f.model ?? '–'}</Num>
                  </Table.Td>
                  <Table.Td>
                    <Credits value={f.allocated} />
                  </Table.Td>
                  <Table.Td>
                    <Credits value={f.total} />
                  </Table.Td>
                  <Table.Td>
                    <StateBadge state="overAllocated" />
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Card>
    </Stack>
  );
}
