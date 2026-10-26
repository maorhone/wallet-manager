import { credits } from '../../format';
import { KpiCard } from './KpiCard';

export interface BudgetKpiLabels {
  kpiTotal: string;
  kpiUnallocated: string;
  kpiAssignedUnused: string;
  kpiUsed: string;
}

/** D-054: period budget, unallocated, assigned-but-unused and charged Credits. */
export function BudgetKpis({
  labels,
  total,
  bar,
  used,
}: {
  labels: BudgetKpiLabels;
  total: number;
  bar: { unallocated: number; allocated_unused: number };
  used: number;
}) {
  return (
    <div className="v2-kpis">
      <KpiCard title={labels.kpiTotal} value={credits(total)} description={labels.kpiTotal} />
      <KpiCard
        title={labels.kpiUnallocated}
        value={credits(bar.unallocated)}
        description={labels.kpiUnallocated}
      />
      <KpiCard
        title={labels.kpiAssignedUnused}
        value={credits(bar.allocated_unused)}
        description={labels.kpiAssignedUnused}
      />
      <KpiCard title={labels.kpiUsed} value={credits(used)} description={labels.kpiUsed} />
    </div>
  );
}
