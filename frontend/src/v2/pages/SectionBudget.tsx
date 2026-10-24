import { useState } from 'react';
import { useAttention, useBudgetOverview } from '../../api/queries';
import { ErrorState } from '../../components/ErrorState';
import { credits } from '../../format';
import { he } from '../../i18n/he';
import { AttentionList, type AttentionItem } from '../components/AttentionList';
import { DistributionBar } from '../components/DistributionBar';
import { KpiCard } from '../components/KpiCard';
import { BulkAllocation } from './BulkAllocation';
import { TransferDrawer } from './TransferDrawer';

const s = he.v2.sectionPage;

export default function SectionBudget({ groupId }: { groupId: string }) {
  const overview = useBudgetOverview(groupId);
  const attention = useAttention(groupId);
  const [transferFrom, setTransferFrom] = useState<string | null>(null);
  const [bulkOpen, setBulkOpen] = useState(false);
  if (overview.isPending) return <p role="status">{he.loading}</p>;
  if (overview.error || !overview.data)
    return <ErrorState error={overview.error} onRetry={overview.refetch} />;
  const { kpis, bar, rows, period } = overview.data;
  const a = attention.data;
  const items: AttentionItem[] = [
    ...(a?.requests ?? []).map((r) => ({
      id: `req-${r.id}`,
      title: `${s.requestsItem}: ${r.type}`,
      description: r.requester_upn,
      href: '/v2/requests',
    })),
    ...(a?.shortage_risks ?? []).map((r) => ({
      id: `risk-${r.kind}-${r.id}`,
      title: `${s.risksItem}: ${r.name}`,
      description: `${Math.round(r.percent * 100)}%`,
    })),
    ...(a?.reallocation_opportunities ?? []).map((o) => ({
      id: `opp-${o.grant_id}`,
      title: s.opportunityItem,
      description: credits(o.unused),
    })),
  ];
  return (
    <>
      <p className="v2-muted">
        {s.period}: <bdi dir="ltr">{period}</bdi>
      </p>
      <div className="v2-kpis">
        <KpiCard title={s.kpiTotal} value={credits(kpis.total)} description={s.kpiTotal} />
        <KpiCard
          title={s.kpiAllocated}
          value={credits(kpis.allocated)}
          description={s.kpiAllocated}
        />
        <KpiCard title={s.kpiUsed} value={credits(kpis.used)} description={s.metered + ': '} />
        <KpiCard
          title={s.kpiRemaining}
          value={credits(kpis.remaining)}
          description={s.kpiRemaining}
        />
      </div>
      <p className="v2-muted">
        {s.metered}:{' '}
        <bdi dir="ltr">
          {kpis.metered_value == null ? he.v2.unavailable : credits(kpis.metered_value)}
        </bdi>
      </p>
      <DistributionBar
        used={bar.used}
        assignedUnused={bar.allocated_unused}
        unallocated={bar.unallocated}
      />
      <AttentionList items={items} />
      <section className="v2-card">
        <h2>{s.rows}</h2>
        <button type="button" onClick={() => setTransferFrom('')}>
          {s.transfer}
        </button>{' '}
        <button type="button" onClick={() => setBulkOpen(true)}>
          {s.bulk}
        </button>
        <table>
          <thead>
            <tr>
              <th>{s.name}</th>
              <th>{s.total}</th>
              <th>{s.used}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.kind}-${r.id}`}>
                <td>{r.name}</td>
                <td>{credits(r.total)}</td>
                <td>{credits(r.used)}</td>
                <td>
                  <button type="button" onClick={() => setTransferFrom(r.id)}>
                    {s.transfer}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <details className="v2-card">
        <summary>{s.advanced}</summary>
        <p>{s.advancedText}</p>
      </details>
      {bulkOpen && (
        <BulkAllocation groupId={Number(groupId)} rows={rows} onClose={() => setBulkOpen(false)} />
      )}
      {transferFrom !== null && (
        <TransferDrawer
          groupId={Number(groupId)}
          rows={rows}
          initialSource={transferFrom}
          onClose={() => setTransferFrom(null)}
        />
      )}
    </>
  );
}
