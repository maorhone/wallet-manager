import { Card, Loader, Stack, Table, Text } from '@mantine/core';
import { useGrantsReport } from '../../api/queries';
import type { GrantTotals } from '../../api/types';
import { Credits } from '../../components/Credits';
import { EmptyState } from '../../components/EmptyState';
import { ErrorState } from '../../components/ErrorState';
import { Num } from '../../components/Num';
import { he } from '../../i18n/he';

const counts = ['departments', 'groups', 'grants'] as const;
const money = ['amount', 'shared', 'unshared', 'used', 'returned'] as const;
function Values({ row }: { row: GrantTotals }) {
  return (
    <>
      {counts.map((key) => (
        <Table.Td key={key}>
          <Num>{row[key]}</Num>
        </Table.Td>
      ))}
      {money.map((key) => (
        <Table.Td key={key}>
          <Credits value={row[key]} />
        </Table.Td>
      ))}
    </>
  );
}

export default function Grants({ department }: { department?: string }) {
  const report = useGrantsReport(department);
  if (report.isPending) return <Loader aria-label={he.loading} />;
  if (report.error || !report.data)
    return <ErrorState error={report.error} onRetry={report.refetch} />;
  return (
    <Stack>
      <Text size="sm" c="dimmed">
        {he.reports.currentGrants}
      </Text>
      <Card withBorder data-testid="grants-report">
        {report.data.rows.length === 0 ? (
          <EmptyState />
        ) : (
          <Table.ScrollContainer minWidth={800}>
            <Table>
              <Table.Thead>
                <Table.Tr>
                  {[
                    he.pmo.project,
                    he.reports.departments,
                    he.departmentPage.groups,
                    he.reports.grantsCount,
                    he.pmo.amount,
                    he.pmo.shared,
                    he.shares.unshared,
                    he.pmo.used,
                    he.pmo.returned,
                  ].map((label) => (
                    <Table.Th key={label}>{label}</Table.Th>
                  ))}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {report.data.rows.map((row) => (
                  <Table.Tr key={row.project_ref ?? ''}>
                    <Table.Td>
                      {row.project_ref ? <Num>{row.project_ref}</Num> : he.reports.noProject}
                    </Table.Td>
                    <Values row={row} />
                  </Table.Tr>
                ))}
              </Table.Tbody>
              <Table.Tfoot>
                <Table.Tr>
                  <Table.Th>{he.groupPage.total}</Table.Th>
                  <Values row={report.data.totals} />
                </Table.Tr>
              </Table.Tfoot>
            </Table>
          </Table.ScrollContainer>
        )}
      </Card>
    </Stack>
  );
}
