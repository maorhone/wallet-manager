import { useState } from 'react';
import { useDepartmentOverview, type DepartmentSystem } from '../../api/queries';
import { ErrorState } from '../../components/ErrorState';
import { credits } from '../../format';
import { he } from '../../i18n/he';
import { DistributionBar } from '../components/DistributionBar';
import { KpiCard } from '../components/KpiCard';
import { HierarchyEditor } from './HierarchyEditor';

const s = he.v2.deptPage;

function Systems({ title, rows }: { title: string; rows: DepartmentSystem[] }) {
  return (
    <section className="v2-card">
      <h2>{title}</h2>
      {rows.length ? (
        <table>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{r.name}</td>
                <td>{credits(r.budget)}</td>
                <td>{credits(r.used)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="v2-muted">{s.none}</p>
      )}
    </section>
  );
}

export default function Department({ departmentId }: { departmentId: string }) {
  const overview = useDepartmentOverview(departmentId);
  const [tab, setTab] = useState<'budget' | 'systems'>('budget');
  if (overview.isPending) return <p role="status">{he.loading}</p>;
  if (overview.error || !overview.data)
    return <ErrorState error={overview.error} onRetry={overview.refetch} />;
  const d = overview.data;
  return (
    <>
      <div className="v2-kpis">
        <KpiCard title={s.kpiTotal} value={credits(d.kpis.total)} description={s.kpiTotal} />
        <KpiCard
          title={s.kpiAllocated}
          value={credits(d.kpis.allocated)}
          description={s.kpiAllocated}
        />
        <KpiCard title={s.kpiUsed} value={credits(d.kpis.used)} description={s.kpiUsed} />
        <KpiCard
          title={s.kpiRemaining}
          value={credits(d.kpis.remaining)}
          description={s.kpiRemaining}
        />
      </div>
      <DistributionBar
        used={d.bar.used}
        assignedUnused={d.bar.allocated_unused}
        unallocated={d.bar.unallocated}
      />
      <div role="tablist">
        <button role="tab" aria-selected={tab === 'budget'} onClick={() => setTab('budget')}>
          {s.tabBudget}
        </button>
        <button role="tab" aria-selected={tab === 'systems'} onClick={() => setTab('systems')}>
          {s.tabSystems}
        </button>
      </div>
      {tab === 'budget' ? (
        <>
          <section className="v2-card">
            <h2>{s.sections}</h2>
            <table>
              <thead>
                <tr>
                  <th>{s.section}</th>
                  <th>{s.budget}</th>
                  <th>{s.used}</th>
                  <th>{s.members}</th>
                  <th>{s.perMember}</th>
                  <th>{s.previous}</th>
                </tr>
              </thead>
              <tbody>
                {d.sections.map((r) => (
                  <tr key={r.id}>
                    <td>{r.name}</td>
                    <td>{credits(r.total)}</td>
                    <td>{credits(r.used)}</td>
                    <td>{r.members}</td>
                    <td>
                      {r.used_per_member == null ? he.v2.unavailable : credits(r.used_per_member)}
                    </td>
                    <td>{credits(r.previous_used)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p>
              {s.periodCompare}: <bdi dir="ltr">{d.previous_period.period}</bdi>{' '}
              {credits(d.previous_period.used)} → <bdi dir="ltr">{d.period}</bdi>{' '}
              {credits(d.kpis.used)}
            </p>
          </section>
          <HierarchyEditor
            departmentId={Number(departmentId)}
            tree={d.tree}
            editable={d.actions.edit_hierarchy}
          />
        </>
      ) : (
        <>
          <Systems title={s.internal} rows={d.internal_systems} />
          <Systems title={s.org} rows={d.org_systems} />
          <p className="v2-muted">{s.orgNote}</p>
        </>
      )}
    </>
  );
}
