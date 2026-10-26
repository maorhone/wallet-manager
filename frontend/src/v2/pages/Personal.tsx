import { useRef, useState } from 'react';
import {
  useMe,
  useMyStatus,
  useMyTagUsage,
  useMyUsage,
  useMyWallet,
  useServiceStatus,
  useSubmitRequest,
} from '../../api/queries';
import { ErrorState } from '../../components/ErrorState';
import { credits } from '../../format';
import { he } from '../../i18n/he';
import { KpiCard } from '../components/KpiCard';
import { StatusPill } from '../components/StatusPill';
import { explainBlock, type RequestKind } from '../personal';

const p = he.v2.personalPage;
const TONE = { ok: 'good', limited: 'warning', loaded: 'warning', unknown: 'neutral' } as const;

function lastDay(period: string): string {
  const [y, m] = period.split('-').map(Number);
  return `${period}-${String(new Date(Date.UTC(y, m, 0)).getUTCDate()).padStart(2, '0')}`;
}

/**
 * Telemetry must never read as "nothing happened" while it is loading or unreadable (D-062).
 * A failed refresh wins over the data TanStack Query keeps from an earlier fetch (stale).
 */
const tele = (q: { data?: unknown; error: unknown; isFetching: boolean }) =>
  q.error ? 'unavailable' : q.data !== undefined ? 'ok' : !q.isFetching ? 'unavailable' : 'loading';

function RequestForm({ kind, onDone }: { kind: RequestKind; onDone: () => void }) {
  const [need, setNeed] = useState('');
  const [parallel, setParallel] = useState('');
  const submit = useSubmitRequest();
  // One key per distinct payload: a retry after a lost response replays, never duplicates.
  const sent = useRef<{ sig: string; key: string } | null>(null);
  if (submit.isSuccess)
    return (
      <>
        <p role="status">{p.sent}</p>
        <button type="button" onClick={onDone}>
          {p.close}
        </button>
      </>
    );
  return (
    <form
      className="v2-card"
      onSubmit={(e) => {
        e.preventDefault();
        // D-044/D-045: no numbers are required; an empty field is omitted
        const maxParallel = kind === 'throughput' && parallel !== '' ? Number(parallel) : undefined;
        const sig = JSON.stringify({ kind, need, maxParallel });
        if (sent.current?.sig !== sig) sent.current = { sig, key: crypto.randomUUID() };
        submit.mutate({ type: kind, need, maxParallel, key: sent.current.key });
      }}
    >
      <h3>{kind === 'credits' ? p.requestCredits : p.requestThroughput}</h3>
      <label>
        {p.need}
        <textarea
          required
          maxLength={1000}
          value={need}
          onChange={(e) => setNeed(e.target.value)}
        />
      </label>
      {kind === 'throughput' && (
        <label>
          {p.maxParallel}
          <input
            type="number"
            min={1}
            value={parallel}
            onChange={(e) => setParallel(e.target.value)}
          />
        </label>
      )}
      {submit.error && <ErrorState error={submit.error} />}
      <button type="submit" disabled={submit.isPending}>
        {p.send}
      </button>{' '}
      <button type="button" onClick={onDone}>
        {p.cancel}
      </button>
    </form>
  );
}

export default function Personal() {
  const me = useMe();
  const period = me.data?.current_period ?? '';
  const wallet = useMyWallet(period);
  const status = useMyStatus();
  const service = useServiceStatus();
  const from = period ? `${period}-01` : '';
  const to = period ? lastDay(period) : '';
  const usage = useMyUsage(from, to);
  const tags = useMyTagUsage(me.data?.upn ?? '', from, to);
  const [form, setForm] = useState<RequestKind | null>(null);

  if (wallet.isPending) return <p role="status">{he.loading}</p>;
  if (wallet.error || !wallet.data)
    return <ErrorState error={wallet.error} onRetry={wallet.refetch} />;
  const w = wallet.data.wallet;
  const byModel = new Map<string, number>();
  for (const r of tele(usage) === 'ok' ? (usage.data ?? []) : [])
    byModel.set(r.model, (byModel.get(r.model) ?? 0) + r.spend);
  const block = tele(status) === 'ok' ? status.data?.last_blocks[0] : undefined;
  const explained = block ? explainBlock(block) : null;
  const overall = (tele(service) === 'ok' && service.data?.state) || 'unknown';
  const limits = status.data?.limits ?? wallet.data.limits;

  return (
    <div className="v2-stack">
      <section aria-label={p.wallet}>
        <div className="v2-kpis">
          <KpiCard
            title={p.remaining}
            value={credits(w.effective_remaining)}
            description={p.wallet}
          />
          <KpiCard title={p.usedLabel} value={credits(w.used)} description={wallet.data.period} />
        </div>
      </section>
      <section className="v2-card" aria-label={p.service}>
        <h3>{p.service}</h3>
        <StatusPill tone={TONE[overall as keyof typeof TONE] ?? 'neutral'}>
          {p.states[overall] ?? p.states.unknown}
        </StatusPill>
      </section>
      <section className="v2-card" aria-label={p.lastBlock}>
        <h3>{p.lastBlock}</h3>
        {tele(status) === 'loading' ? (
          <p role="status">{he.loading}</p>
        ) : tele(status) === 'unavailable' || status.data?.blocks_available === false ? (
          <p className="v2-muted">{he.v2.unavailable}</p>
        ) : explained ? (
          <p data-testid="last-block">{explained.text}</p>
        ) : (
          <p className="v2-muted">{p.noBlock}</p>
        )}
      </section>
      <section className="v2-card" aria-label={p.byModel}>
        <h3>{p.byModel}</h3>
        {tele(usage) === 'loading' ? (
          <p role="status">{he.loading}</p>
        ) : tele(usage) === 'unavailable' ? (
          <p className="v2-muted">{he.v2.unavailable}</p>
        ) : (
          byModel.size === 0 && <p className="v2-muted">{p.noUsage}</p>
        )}
        <ul>
          {[...byModel].map(([model, spend]) => (
            <li key={model}>
              <bdi dir="ltr">{model}</bdi>: {credits(spend)}
            </li>
          ))}
        </ul>
      </section>
      <section className="v2-card" aria-label={p.byProject}>
        <h3>{p.byProject}</h3>
        {tele(tags) === 'loading' ? (
          <p role="status">{he.loading}</p>
        ) : tele(tags) === 'unavailable' ? (
          <p className="v2-muted">{he.v2.unavailable}</p>
        ) : (
          tags.data?.series.length === 0 && <p className="v2-muted">{p.noUsage}</p>
        )}
        <ul>
          {(tele(tags) === 'ok' ? tags.data?.series : [])?.map((r) => (
            <li key={`${r.bucket}${r.key}`}>
              <bdi dir="ltr">{r.key}</bdi>:{' '}
              {r.spend === null ? he.v2.unavailable : credits(r.spend)}
            </li>
          ))}
        </ul>
      </section>
      <details className="v2-card">
        <summary>{p.showLimits}</summary>
        <p>
          {p.maxParallel}: <bdi dir="ltr">{limits.max_parallel ?? he.v2.unavailable}</bdi>
        </p>
        <p>
          {p.rpm}: <bdi dir="ltr">{limits.rpm_limit ?? he.v2.unavailable}</bdi>
        </p>
      </details>
      <div>
        <button type="button" onClick={() => setForm('credits')}>
          {p.requestCredits}
        </button>{' '}
        <button type="button" onClick={() => setForm('throughput')}>
          {p.requestThroughput}
        </button>
        {explained?.cta && (
          <p className="v2-muted" data-testid="block-cta">
            {explained.cta === 'throughput' ? p.requestThroughput : p.requestCredits}
          </p>
        )}
      </div>
      {form && <RequestForm key={form} kind={form} onDone={() => setForm(null)} />}
    </div>
  );
}
