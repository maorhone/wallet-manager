import { useState } from 'react';
import { useMoveKey, useServiceKeys, useSubjectTelemetry } from '../../api/queries';
import { ErrorState } from '../../components/ErrorState';
import { credits } from '../../format';
import { he } from '../../i18n/he';

const s = he.v2.initPage;

export interface DetailSubject {
  kind: 'initiative' | 'system';
  id: number;
  name: string;
  budget: number;
  /** Subject-level consumption (historical attribution); the key sum is only a fallback. */
  used?: number;
  owner?: string;
  funding?: 'department' | 'organization';
  tech?: keyof typeof s.tech;
}

/** Read-only budget, consumption, limits and key aliases (never secrets); allocators may move keys. */
export function SystemDetail({ subject, canMove }: { subject: DetailSubject; canMove: boolean }) {
  const keys = useServiceKeys(subject.kind, subject.id);
  const move = useMoveKey();
  const health = useSubjectTelemetry(subject.kind, subject.id);
  const [target, setTarget] = useState('');
  const [chosen, setChosen] = useState('');
  // The idempotency key belongs to one submitted payload: a retry of the same move replays,
  // any other key/destination gets its own, so a lost response never swallows a different move.
  const [attempt, setAttempt] = useState<{ keyId: number; target: number; idem: string } | null>(
    null,
  );
  const [moved, setMoved] = useState<{ keyId: number; target: number; applied: boolean } | null>(
    null,
  );
  if (keys.isPending) return <p role="status">{he.loading}</p>;
  if (keys.error || !keys.data) return <ErrorState error={keys.error} onRetry={keys.refetch} />;
  const items = keys.data.items;
  const used = subject.used ?? items.reduce((sum, k) => sum + k.used, 0);
  const movable = items.filter((k) => k.status === 'active');
  const keyId = movable.some((k) => String(k.id) === chosen) ? Number(chosen) : movable[0]?.id;
  return (
    <section className="v2-card" aria-label={subject.name}>
      <h2>{subject.name}</h2>
      <dl>
        <dt>{s.budget}</dt>
        <dd>{credits(subject.budget)}</dd>
        <dt>{s.used}</dt>
        <dd>{credits(used)}</dd>
        <dt>{s.balance}</dt>
        <dd>{credits(subject.budget - used)}</dd>
        {subject.owner && (
          <>
            <dt>{s.owner}</dt>
            <dd>
              <bdi dir="ltr">{subject.owner}</bdi>
            </dd>
          </>
        )}
        {subject.funding && (
          <>
            <dt>{s.funding}</dt>
            <dd>
              {subject.funding === 'organization' ? s.fundingOrganization : s.fundingDepartment}
            </dd>
          </>
        )}
        {subject.tech && (
          <>
            <dt>{s.connection}</dt>
            <dd>{s.tech[subject.tech]}</dd>
          </>
        )}
      </dl>
      <h3>{s.keys}</h3>
      {items.length ? (
        <table>
          <thead>
            <tr>
              <th>{s.keys}</th>
              <th>{s.used}</th>
              <th>{s.parallel}</th>
              <th>{s.rpm}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((k) => (
              <tr key={k.id}>
                <td>
                  <bdi dir="ltr">{k.alias}</bdi>
                </td>
                <td>{credits(k.used)}</td>
                <td>{k.max_parallel}</td>
                <td>{k.rpm_limit}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="v2-muted">{s.noKeys}</p>
      )}
      <h3>{s.health}</h3>
      {health.isPending ? (
        <p role="status">{he.loading}</p>
      ) : health.error || !health.data?.available ? (
        <p role="alert">{s.healthUnavailable}</p>
      ) : health.data.by_reason.length ? (
        <ul>
          {health.data.by_reason.map((r) => (
            <li key={`${r.reason}:${r.sub ?? ''}`}>
              {he.v2.blockSummary.reasons[r.reason] ?? r.reason}
              {r.sub && ` (${he.v2.blockSummary.subs[r.sub] ?? r.sub})`}: {s.healthCount} {r.count}
            </li>
          ))}
        </ul>
      ) : (
        <p className="v2-muted">{s.healthNone}</p>
      )}
      {canMove && subject.kind === 'initiative' && movable.length > 0 && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const body = { keyId: keyId as number, target: Number(target) };
            const same = attempt?.keyId === body.keyId && attempt.target === body.target;
            const next = { ...body, idem: same ? attempt.idem : crypto.randomUUID() };
            setAttempt(next);
            setMoved(null);
            move.mutate(
              { keyId: next.keyId, targetSystemId: next.target, key: next.idem },
              {
                onSuccess: (data) => {
                  setAttempt(null);
                  setMoved({
                    keyId: next.keyId,
                    target: next.target,
                    applied: data?.status === 'applied',
                  });
                },
              },
            );
          }}
        >
          <label>
            {s.keyToMove}
            <select value={keyId} onChange={(e) => setChosen(e.target.value)}>
              {movable.map((k) => (
                <option key={k.id} value={k.id}>
                  {k.alias}
                </option>
              ))}
            </select>
          </label>
          <label>
            {s.targetSystem}
            <input
              type="number"
              min="1"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              required
            />
          </label>
          <button type="submit" disabled={move.isPending}>
            {s.moveKey}
          </button>
          {move.isError && <p role="alert">{(move.error as Error).message}</p>}
        </form>
      )}
      {moved && <MoveFeedback {...moved} />}
    </section>
  );
}

/** Outcome of the last move; it survives the source losing its last key and follows the credential. */
function MoveFeedback({
  keyId,
  target,
  applied,
}: {
  keyId: number;
  target: number;
  applied: boolean;
}) {
  const dest = useServiceKeys('system', target);
  const synced = dest.data?.items.find((k) => k.id === keyId && k.status === 'active');
  const state = applied ? 'applied' : (synced?.sync_status ?? 'pending');
  return (
    <p role="status">
      {state === 'applied' ? s.moved : state === 'failed' ? he.sync.failed : s.movePending}
    </p>
  );
}
