import { useState } from 'react';
import { useMoveKey, useServiceKeys } from '../../api/queries';
import { ErrorState } from '../../components/ErrorState';
import { credits } from '../../format';
import { he } from '../../i18n/he';

const s = he.v2.initPage;

export interface DetailSubject {
  kind: 'initiative' | 'system';
  id: number;
  name: string;
  budget: number;
  owner?: string;
  tech?: keyof typeof s.tech;
}

/** Read-only budget, consumption, limits and key aliases (never secrets); allocators may move keys. */
export function SystemDetail({ subject, canMove }: { subject: DetailSubject; canMove: boolean }) {
  const keys = useServiceKeys(subject.kind, subject.id);
  const move = useMoveKey();
  const [target, setTarget] = useState('');
  // One key per move: retries replay, a later distinct move gets a fresh key.
  const [rowKey, setRowKey] = useState(() => crypto.randomUUID());
  if (keys.isPending) return <p role="status">{he.loading}</p>;
  if (keys.error || !keys.data) return <ErrorState error={keys.error} onRetry={keys.refetch} />;
  const items = keys.data.items;
  const used = items.reduce((sum, k) => sum + k.used, 0);
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
      {canMove && subject.kind === 'initiative' && items.length > 0 && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            move.mutate(
              { keyId: items[0].id, targetSystemId: Number(target), key: rowKey },
              { onSuccess: () => setRowKey(crypto.randomUUID()) },
            );
          }}
        >
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
          {move.isSuccess && (
            <p role="status">{move.data?.status === 'applied' ? s.moved : s.movePending}</p>
          )}
          {move.isError && <p role="alert">{(move.error as Error).message}</p>}
        </form>
      )}
    </section>
  );
}
