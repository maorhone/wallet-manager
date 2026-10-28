import { useRef, useState, type MutableRefObject } from 'react';
import {
  useCreateOrganization,
  useOrgAllocate,
  useOrgEnvelopes,
  type OrgEnvelopeRow,
} from '../../api/queries';
import { ErrorState } from '../../components/ErrorState';
import { credits } from '../../format';
import { he } from '../../i18n/he';
import type { WriteResponse } from '../../api/types';
import { WriteOutcome } from '../components/WriteOutcome';
import { KpiCard } from '../components/KpiCard';

const s = he.v2.orgAlloc;

type Outcome = {
  kind: 'department' | 'system';
  name: string;
  result: WriteResponse;
  replay: () => Promise<WriteResponse>;
};

function Table({
  rows,
  kind,
  allocate,
  reason,
  outcomes,
  onSaved,
  sent,
}: {
  rows: OrgEnvelopeRow[];
  kind: 'department' | 'system';
  // Owned by the page, so a save still reports its outcome after a tab switch unmounts the table.
  allocate: ReturnType<typeof useOrgAllocate>;
  reason: string;
  // Held above the tabs, so switching tabs neither drops a pending write nor its poller (D-071).
  outcomes: Outcome[];
  onSaved: (o: Outcome) => void;
  // Idempotency key per envelope row and payload, held above the tabs: a retry after a lost
  // response replays the first write instead of executing it again (review-p5-r17 F2).
  sent: MutableRefObject<Map<string, { sig: string; key: string }>>;
}) {
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
                  onClick={() => {
                    const v = {
                      kind,
                      id: r.id,
                      budget: Number(edit[r.id]),
                      budgetPeriod: r.budget_period,
                      reason,
                    };
                    const slot = `${kind}:${r.id}`;
                    const sig = JSON.stringify(v);
                    const prior = sent.current.get(slot);
                    const key = prior?.sig === sig ? prior.key : crypto.randomUUID();
                    sent.current.set(slot, { sig, key });
                    allocate.mutate(
                      { ...v, key },
                      {
                        // Reconcile: after a persisted write the server value replaces the draft.
                        onSuccess: ({ result, replay }) => {
                          sent.current.delete(slot);
                          onSaved({ kind, name: r.name, result, replay });
                          setEdit((d) =>
                            Object.fromEntries(
                              Object.entries(d).filter(([k]) => k !== String(r.id)),
                            ),
                          );
                        },
                      },
                    );
                  }}
                >
                  {s.save}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {outcomes.map((o) => (
        <div key={o.result.request_id}>
          <strong>{o.name}</strong>
          <WriteOutcome result={o.result} replay={o.replay} done={he.v2.writeOutcome.applied} />
        </div>
      ))}
    </>
  );
}

export default function OrgAllocation({
  organizationId,
  isAdmin,
  onCreated,
}: {
  organizationId: number;
  isAdmin: boolean;
  onCreated?: (id: number) => void;
}) {
  const env = useOrgEnvelopes(organizationId);
  const create = useCreateOrganization();
  const allocate = useOrgAllocate(organizationId);
  const [tab, setTab] = useState<'depts' | 'systems'>('depts');
  const [reason, setReason] = useState('');
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);
  const [newName, setNewName] = useState('');
  // One key per distinct organization submission: a retry replays, a new name does not.
  const sent = useRef<{ name: string; key: string } | null>(null);
  const allocKeys = useRef(new Map<string, { sig: string; key: string }>());
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
            allocate={allocate}
            sent={allocKeys}
            reason={reason}
            outcomes={outcomes.filter((o) => o.kind === 'department')}
            onSaved={(o) => setOutcomes((all) => [...all, o])}
          />
        ) : (
          <Table
            key="system"
            rows={systems.rows}
            kind="system"
            allocate={allocate}
            sent={allocKeys}
            reason={reason}
            outcomes={outcomes.filter((o) => o.kind === 'system')}
            onSaved={(o) => setOutcomes((all) => [...all, o])}
          />
        )}
      </section>
      {isAdmin && (
        <form
          className="v2-card"
          onSubmit={(e) => {
            e.preventDefault();
            if (sent.current?.name !== newName)
              sent.current = { name: newName, key: crypto.randomUUID() };
            create.mutate(
              { name: newName, reason: reason || s.newOrg, key: sent.current.key },
              {
                onSuccess: (res) => {
                  sent.current = null;
                  const id = res.entities?.find((x) => x.entity === 'organization')?.id;
                  if (id) onCreated?.(Number(id));
                },
              },
            );
          }}
        >
          <h2>{s.newOrg}</h2>
          <label>
            {s.orgName}
            <input required value={newName} onChange={(e) => setNewName(e.target.value)} />
          </label>
          <button type="submit" disabled={create.isPending}>
            {s.create}
          </button>
        </form>
      )}
    </>
  );
}
