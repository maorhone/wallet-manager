import { PendingPlacementsTable } from '../../components/PendingPlacementsTable';
import type { DepartmentSummary } from '../../api/types';

/** Tab "ממתינים לשיבוץ" (§12.5.4 tab 5). */
export default function Pending({ summary }: { summary: DepartmentSummary }) {
  return (
    <PendingPlacementsTable
      scope={String(summary.department.id)}
      canPlace={summary.actions.place}
    />
  );
}
