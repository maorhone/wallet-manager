import { Button, Table } from '@mantine/core';
import { useState } from 'react';
import {
  useDepartmentGroups,
  useDepartmentIds,
  usePendingPlacements,
  usePlace,
} from '../api/queries';
import type { PendingPlacement } from '../api/types';
import { he } from '../i18n/he';
import { DateText } from './DateText';
import { EmptyState } from './EmptyState';
import { ErrorState } from './ErrorState';
import { MemberActionDialog } from './MemberActionDialog';
import { Num } from './Num';
import { usePeriod } from './PeriodSelector';

const t = he.pending;

/** Pending employees of one department, or all of them for the platform admin ('all'). */
export function PendingPlacementsTable({
  scope,
  canPlace,
}: {
  scope: string | 'all';
  canPlace: boolean;
}) {
  const { period } = usePeriod();
  const list = usePendingPlacements(scope);
  const place = usePlace();
  const [picked, setPicked] = useState<PendingPlacement | null>(null);
  const rows = list.data ?? [];
  // A known department offers its own groups; an unknown one (admin) offers every department's.
  const unknownDept = picked !== null && picked.department_id === null;
  const all = useDepartmentIds(unknownDept);
  const deptIds = picked ? (unknownDept ? (all.data ?? []) : [picked.department_id!]) : [];
  const { groups } = useDepartmentGroups(deptIds, period);

  if (list.error) return <ErrorState error={list.error} onRetry={list.refetch} />;
  if (list.isSuccess && rows.length === 0) return <EmptyState message={t.empty} />;
  return (
    <>
      <Table data-testid="pending-table">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t.employee}</Table.Th>
            <Table.Th>{t.department}</Table.Th>
            <Table.Th>{t.reason}</Table.Th>
            <Table.Th>{t.since}</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((p) => (
            <Table.Tr key={p.upn} data-row={p.upn}>
              <Table.Td>
                {p.display_name} <Num>{p.upn}</Num>
              </Table.Td>
              <Table.Td>{p.department_name ?? t.unknownDepartment}</Table.Td>
              <Table.Td>{(p.reason && t.reasons[p.reason]) ?? p.reason}</Table.Td>
              <Table.Td>{p.pending_since && <DateText value={p.pending_since} />}</Table.Td>
              <Table.Td>
                {canPlace && (
                  <Button size="xs" data-testid="place" onClick={() => setPicked(p)}>
                    {he.memberActions.place}
                  </Button>
                )}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {picked && (
        <MemberActionDialog
          opened
          action="place"
          member={picked}
          targets={groups}
          onClose={() => setPicked(null)}
          onConfirm={(reason, key, extra) =>
            place.mutateAsync({ upn: picked.upn, groupId: extra!.to_group_id, reason, key })
          }
        />
      )}
    </>
  );
}
