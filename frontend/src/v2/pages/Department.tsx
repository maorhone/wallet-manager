import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useDepartmentOverview, type DepartmentSystem } from '../../api/queries';
import { ErrorState } from '../../components/ErrorState';
import { credits } from '../../format';
import { he } from '../../i18n/he';
import { BlockSummary } from '../components/BlockSummary';
import { DistributionBar } from '../components/DistributionBar';
import { BudgetKpis } from '../components/BudgetKpis';
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
      <BudgetKpis labels={s} total={d.kpis.total} bar={d.bar} used={d.kpis.used} />
      <p className="v2-muted">
        {s.metered}:{' '}
        <bdi dir="ltr">
          {d.kpis.metered_value == null ? he.v2.unavailable : credits(d.kpis.metered_value)}
        </bdi>
      </p>
      <DistributionBar
        used={d.bar.used}
        assignedUnused={d.bar.allocated_unused}
        unallocated={d.bar.unallocated}
      />
      <BlockSummary scope="department" id={departmentId} period={d.period} />
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
                    <td>
                      <Link to={`/v2/group/${r.id}`}>{r.name}</Link>
                    </td>
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
