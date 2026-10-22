import { Card, Loader, Stack, Table } from '@mantine/core';
import { Fragment } from 'react';
import { useAllocationReport, useDepartmentIds, useMe } from '../../api/queries';
import type { AllocationLevel, ModelAllocation } from '../../api/types';
import { Credits } from '../../components/Credits';
import { EmptyState } from '../../components/EmptyState';
import { ErrorState } from '../../components/ErrorState';
import { ModelTable } from '../../components/ModelTable';
import { Pct } from '../../components/Pct';
import { API_STATE, StateBadge } from '../../components/StateBadge';
import { WalletCard } from '../../components/WalletCard';
import { zonedInstant } from '../../format';
import { he } from '../../i18n/he';

const modelsForTable = (models: ModelAllocation[]) =>
  models.map((m) => ({
    model: m.model,
    cap: m.allocation,
    used: m.spend,
    remaining: m.allocation === null ? null : m.allocation - m.spend,
  }));

export default function Allocation({
  level,
  id,
  period,
}: {
  level: AllocationLevel;
  id: string;
  period: string;
}) {
  const report = useAllocationReport(level, id, period);
  const { data: me } = useMe();
  if (report.isPending) return <Loader aria-label={he.loading} />;
  if (report.error || !report.data)
    return <ErrorState error={report.error} onRetry={report.refetch} />;
  const r = report.data;
  const [year, month] = r.period.split('-').map(Number);
  const nextMonth = new Date(Date.UTC(year, month, 1)).toISOString().slice(0, 10);
  return (
    <Stack>
      <WalletCard
        title={r.name}
        total={r.total}
        used={r.spend}
        remaining={r.remaining}
        percent={r.pct}
        state={r.state}
        takenAt={r.spend_taken_at}
        resetDate={zonedInstant(nextMonth, '00:00:00', me?.timezone ?? 'Asia/Jerusalem')}
        allocated={level === 'employee' ? undefined : r.allocated}
        unallocated={level === 'employee' ? undefined : r.total - r.allocated}
      />
      <ModelTable models={modelsForTable(r.models)} />
      {level !== 'employee' && (
        <Card withBorder data-testid="report-allocation-rows">
          {r.rows.length === 0 ? (
            <EmptyState />
          ) : (
            <Table>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>{he.groupPage.name}</Table.Th>
                  <Table.Th>
                    {level === 'department' ? he.departmentPage.base : he.wallet.cap}
                  </Table.Th>
                  <Table.Th>
                    {level === 'department' ? he.departmentPage.grants : he.wallet.shares}
                  </Table.Th>
                  <Table.Th>{he.groupPage.total}</Table.Th>
                  <Table.Th>{he.wallet.used}</Table.Th>
                  <Table.Th>{he.wallet.remaining}</Table.Th>
                  <Table.Th>{he.groupPage.percent}</Table.Th>
                  <Table.Th>{he.groupPage.status}</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {r.rows.map((row) => {
                  const badge = API_STATE[row.state];
                  const base = row.base ?? row.base_cap;
                  const extra = row.grants ?? row.shares_total;
                  const name =
                    row.kind === 'pmo_pool'
                      ? he.reports.pmoPool
                      : row.kind === 'reserve'
                        ? he.reports.reserve
                        : row.name;
                  return (
                    <Fragment key={row.id}>
                      <Table.Tr>
                        <Table.Td>{name}</Table.Td>
                        <Table.Td>{base == null ? '–' : <Credits value={base} />}</Table.Td>
                        <Table.Td>{extra == null ? '–' : <Credits value={extra} />}</Table.Td>
                        <Table.Td>
                          <Credits value={row.total} />
                        </Table.Td>
                        <Table.Td>
                          <Credits value={row.spend} />
                        </Table.Td>
                        <Table.Td>
                          <Credits value={row.remaining} />
                        </Table.Td>
                        <Table.Td>
                          <Pct value={row.pct * 100} />
                        </Table.Td>
                        <Table.Td>{badge && <StateBadge state={badge} />}</Table.Td>
                      </Table.Tr>
                      {row.models.length > 0 && (
                        <Table.Tr>
                          <Table.Td colSpan={8}>
                            <ModelTable models={modelsForTable(row.models)} />
                          </Table.Td>
                        </Table.Tr>
                      )}
                    </Fragment>
                  );
                })}
              </Table.Tbody>
            </Table>
          )}
        </Card>
      )}
    </Stack>
  );
}

/** Reuse the department reports: the existing allocation API has no global level. */
export function GlobalAllocation({ period }: { period: string }) {
  const departments = useDepartmentIds(true);
  if (departments.isPending) return <Loader aria-label={he.loading} />;
  if (departments.error || !departments.data)
    return <ErrorState error={departments.error} onRetry={departments.refetch} />;
  if (departments.data.length === 0) return <EmptyState />;
  return (
    <Stack>
      {departments.data.map((id) => (
        <Allocation key={id} level="department" id={String(id)} period={period} />
      ))}
    </Stack>
  );
}
