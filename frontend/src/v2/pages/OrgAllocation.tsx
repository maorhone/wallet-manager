import { useState } from 'react';
import {
  useCreateOrganization,
  useOrgAllocate,
  useOrgEnvelopes,
  type OrgEnvelopeRow,
} from '../../api/queries';
import { ErrorState } from '../../components/ErrorState';
import { credits } from '../../format';
import { he } from '../../i18n/he';
import { KpiCard } from '../components/KpiCard';

const s = he.v2.orgAlloc;

function Table({
  rows,
  kind,
  orgId,
  reason,
}: {
  rows: OrgEnvelopeRow[];
  kind: 'department' | 'system';
  orgId: number;
  reason: string;
}) {
  const allocate = useOrgAllocate(orgId);
  const [edit, setEdit] = useState<Record<number, string>>({});
  if (!rows.length) return <p className="v2-muted">{s.none}</p>;
  return (
    <>
      {allocate.error && <p role="alert">{(allocate.error as Error).message}</p>}
      <table>
        <thead>
          <tr>
            <th>{s.name}</th>
            <th>{s.budget}</th>
            {kind === 'department' && <th>{s.previousUsed}</th>}
            {kind === 'department' && <th>{s.recommendation}</th>}
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{r.name}</td>
              <td>
                <input
                  type="number"
                  min={0}
                  aria-label={`${s.budget}: ${r.name}`}
                  value={edit[r.id] ?? String(r.budget)}
                  onChange={(e) => setEdit({ ...edit, [r.id]: e.target.value })}
                />
              </td>
              {kind === 'department' && <td>{credits(r.previous_used ?? 0)}</td>}
              {kind === 'department' && <td className="v2-muted">{s.later}</td>}
              <td>
                <button
                  type="button"
                  disabled={!reason || edit[r.id] === undefined || allocate.isPending}
                  onClick={() =>
                    allocate.mutate(
                      {
                        kind,
                        id: r.id,
                        budget: Number(edit[r.id]),
                        budgetPeriod: r.budget_period,
                        reason,
                      },
                      {
                        // Reconcile: after a persisted write the server value replaces the draft.
                        onSuccess: () =>
                          setEdit((d) =>
                            Object.fromEntries(
                              Object.entries(d).filter(([k]) => k !== String(r.id)),
                            ),
                          ),
                      },
                    )
                  }
                >
                  {s.save}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export default function OrgAllocation({
  organizationId,
  isAdmin,
}: {
  organizationId: number;
  isAdmin: boolean;
}) {
  const env = useOrgEnvelopes(organizationId);
  const create = useCreateOrganization();
  const [tab, setTab] = useState<'depts' | 'systems'>('depts');
  const [reason, setReason] = useState('');
  const [newName, setNewName] = useState('');
  if (env.isPending) return <p role="status">{he.loading}</p>;
  if (env.error || !env.data) return <ErrorState error={env.error} onRetry={env.refetch} />;
  const { departments, systems } = env.data;
  return (
    <>
      <div className="v2-kpis">
        <KpiCard
          title={s.deptEnvelope}
          value={credits(departments.envelope)}
          description={`${s.allocated}: ${credits(departments.allocated)}`}
        />
        <KpiCard
          title={s.sysEnvelope}
          value={credits(systems.envelope)}
          description={`${s.allocated}: ${credits(systems.allocated)}`}
        />
      </div>
      <p className="v2-muted">{s.envelopeNote}</p>
      <label>
        {s.reason}
        <input value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      <div role="tablist">
        <button role="tab" aria-selected={tab === 'depts'} onClick={() => setTab('depts')}>
          {s.tabDepts}
        </button>
        <button role="tab" aria-selected={tab === 'systems'} onClick={() => setTab('systems')}>
          {s.tabSystems}
        </button>
      </div>
      <section className="v2-card">
        {tab === 'depts' ? (
          <Table
            key="department"
            rows={departments.rows}
            kind="department"
            orgId={organizationId}
            reason={reason}
          />
        ) : (
          <Table
            key="system"
            rows={systems.rows}
            kind="system"
            orgId={organizationId}
            reason={reason}
          />
        )}
      </section>
      {isAdmin && (
        <form
          className="v2-card"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate({ name: newName, reason: reason || s.newOrg });
          }}
        >
          <h2>{s.newOrg}</h2>
          <label>
            {s.orgName}
            <input required value={newName} onChange={(e) => setNewName(e.target.value)} />
          </label>
          <button type="submit">{s.create}</button>
        </form>
      )}
    </>
  );
}
