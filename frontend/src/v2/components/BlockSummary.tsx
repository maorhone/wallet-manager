import { useBlockSummary } from '../../api/queries';
import { he } from '../../i18n/he';

const s = he.v2.blockSummary;

/** D-064: block reasons and consumers inside the manager's own scope; counts only. */
export function BlockSummary({
  scope,
  id,
  period,
}: {
  scope: 'group' | 'department';
  id: string;
  period: string;
}) {
  const q = useBlockSummary(scope, id, period);
  return (
    <section className="v2-card">
      <h2>{s.title}</h2>
      {q.isPending ? (
        <p role="status">{he.loading}</p>
      ) : q.error || !q.data ? (
        <p className="v2-muted">{he.v2.unavailable}</p>
      ) : q.data.by_reason.length === 0 ? (
        <p className="v2-muted">{s.none}</p>
      ) : (
        <>
          <table>
            <tbody>
              {q.data.by_reason.map((r) => (
                <tr key={`${r.reason}-${r.sub ?? ''}`}>
                  <td>{s.reasons[r.reason] ?? r.reason}</td>
                  <td>{r.sub ? (s.subs[r.sub] ?? r.sub) : ''}</td>
                  <td>
                    {s.count}: {r.count}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <details>
            <summary>{s.consumers}</summary>
            <ul>
              {q.data.by_consumer.map((c) => (
                <li key={`${c.reason}-${c.consumer ?? ''}`}>
                  <bdi dir="ltr">{c.consumer ?? '-'}</bdi> · {s.reasons[c.reason] ?? c.reason} ·{' '}
                  {c.count}
                </li>
              ))}
            </ul>
          </details>
          {q.data.truncated && <p className="v2-muted">{s.truncated}</p>}
        </>
      )}
    </section>
  );
}
