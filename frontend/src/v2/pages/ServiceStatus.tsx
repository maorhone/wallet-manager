import { useServiceStatus } from '../../api/queries';
import { ErrorState } from '../../components/ErrorState';
import { freshness } from '../../format';
import { he } from '../../i18n/he';
import { StatusPill } from '../components/StatusPill';

const s = he.v2.svcPage;
const states = he.v2.personalPage.states;
const TONE = { ok: 'good', limited: 'warning', loaded: 'warning', unknown: 'neutral' } as const;

export default function ServiceStatus() {
  const q = useServiceStatus();
  if (q.isPending) return <p role="status">{he.loading}</p>;
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={q.refetch} />;
  const { models } = q.data;
  return (
    <>
      {models.length ? (
        <table className="v2-table">
          <thead>
            <tr>
              <th>{s.model}</th>
              <th>{s.state}</th>
              <th>{s.updated}</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={m.model}>
                <td>
                  <bdi dir="ltr">{m.model}</bdi>
                </td>
                <td>
                  <StatusPill tone={TONE[m.state as keyof typeof TONE] ?? 'neutral'}>
                    {states[m.state] ?? states.unknown}
                  </StatusPill>
                </td>
                <td>{freshness(m.checked_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="v2-muted">{s.empty}</p>
      )}
      <p className="v2-muted">{s.staleNote}</p>
      <section className="v2-card">
        <h2>{s.limitsTitle}</h2>
        <p>{s.limitsText}</p>
      </section>
      <section className="v2-card">
        <h2>{s.glossary}</h2>
        <dl>
          {Object.entries(he.v2.personalPage.reasons).map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      </section>
    </>
  );
}
