import { useState } from 'react';
import {
  useDecideRequest,
  useRequestDetail,
  useRequests,
  type RequestItem,
} from '../../api/queries';
import { ErrorState } from '../../components/ErrorState';
import { he } from '../../i18n/he';
import { RequestForm } from './RequestForm';

const s = he.v2.reqPage;

/** Decisions the backend accepts per type (D-065: heads only forward throughput). */
export function actionsFor(r: Pick<RequestItem, 'type' | 'status' | 'handler_role'>): string[] {
  if (r.status !== 'submitted' && r.status !== 'forwarded') return [];
  if (r.type !== 'throughput') return ['approve', 'reject'];
  return r.handler_role === 'ai_team' ? ['apply', 'reject'] : ['forward'];
}

/** Approving credits/initiatives needs a positive amount: the requested one or the handler's own. */
export function approvalBlocked(
  decision: string,
  requested: number | string | null,
  input: string,
): boolean {
  if (decision !== 'approve') return false;
  const value = input !== '' ? Number(input) : Number(requested ?? 0);
  return !(value > 0);
}

function Detail({ id, canAct }: { id: number; canAct: boolean }) {
  const detail = useRequestDetail(id);
  const decide = useDecideRequest();
  const [comment, setComment] = useState('');
  const [amount, setAmount] = useState('');
  if (detail.isPending) return <p role="status">{he.loading}</p>;
  // After forwarding, the request may leave this user's scope and the refetch 404s.
  if (decide.isSuccess && (detail.error || !detail.data)) return <p role="status">{s.decided}</p>;
  if (detail.error || !detail.data) return <ErrorState error={detail.error} />;
  const r = detail.data;
  return (
    <section className="v2-card" aria-label={`${s.types[r.type]} #${r.id}`}>
      <h3>
        {s.types[r.type]} #{r.id} · {s.statuses[r.status] ?? r.status}
      </h3>
      <p>{String(r.payload.need ?? '')}</p>
      {r.amount != null && (
        <p>
          {s.requestedAmount}: <bdi dir="ltr">{String(r.amount)}</bdi>
        </p>
      )}
      <h4>{s.history}</h4>
      <ul>
        {(r.events ?? []).map((e, i) => (
          <li key={i}>
            {e.action} · <bdi dir="ltr">{e.actor_upn}</bdi>
            {e.comment ? ` · ${e.comment}` : ''}
          </li>
        ))}
      </ul>
      {canAct && actionsFor(r).length > 0 && (
        <>
          <label>
            {s.comment}
            <input value={comment} onChange={(e) => setComment(e.target.value)} />
          </label>
          {actionsFor(r).includes('approve') && (
            <label>
              {s.approvedAmount}
              <input
                type="number"
                min={1}
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
              />
            </label>
          )}
          {actionsFor(r).map((d) => (
            <button
              key={d}
              type="button"
              disabled={decide.isPending || approvalBlocked(d, r.amount, amount)}
              onClick={() =>
                decide.mutate({
                  id: r.id,
                  decision: d,
                  comment,
                  ...(d === 'approve' && amount !== '' && { amount: Number(amount) }),
                })
              }
            >
              {s[d as 'approve' | 'reject' | 'forward' | 'apply']}
            </button>
          ))}
        </>
      )}
      {decide.isSuccess && <p role="status">{s.decided}</p>}
      {decide.error && <ErrorState error={decide.error} />}
    </section>
  );
}

export default function Requests() {
  const [box, setBox] = useState<'mine' | 'to_handle'>('mine');
  const [type, setType] = useState('');
  const [status, setStatus] = useState('');
  const [open, setOpen] = useState<number | null>(null);
  const list = useRequests(box, type, status);
  return (
    <>
      <div role="tablist">
        {(['mine', 'to_handle'] as const).map((b) => (
          <button
            key={b}
            role="tab"
            aria-selected={box === b}
            onClick={() => {
              setBox(b);
              setOpen(null);
            }}
          >
            {b === 'mine' ? s.mine : s.toHandle}
          </button>
        ))}
      </div>
      <label>
        {s.type}
        <select value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">{s.all}</option>
          {Object.entries(s.types).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </label>
      <label>
        {s.status}
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">{s.all}</option>
          {Object.entries(s.statuses).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </label>
      {list.isPending ? (
        <p role="status">{he.loading}</p>
      ) : list.error || !list.data ? (
        <ErrorState error={list.error} onRetry={list.refetch} />
      ) : list.data.items.length === 0 ? (
        <p className="v2-muted">{s.none}</p>
      ) : (
        <table>
          <tbody>
            {list.data.items.map((r) => (
              <tr key={r.id}>
                <td>
                  <button type="button" onClick={() => setOpen(r.id)}>
                    #{r.id} {s.types[r.type]}
                  </button>
                </td>
                <td>{s.statuses[r.status] ?? r.status}</td>
                <td>
                  <bdi dir="ltr">{r.requester_upn}</bdi>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {open !== null && <Detail key={open} id={open} canAct={box === 'to_handle'} />}
      {box === 'mine' && <RequestForm />}
    </>
  );
}
