import { useState } from 'react';
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

function RequestForm({ kind, onDone }: { kind: RequestKind; onDone: () => void }) {
  const [need, setNeed] = useState('');
  const [parallel, setParallel] = useState('');
  const submit = useSubmitRequest();
  if (submit.isSuccess) return <p role="status">{p.sent}</p>;
  return (
    <form
      className="v2-card"
      onSubmit={(e) => {
        e.preventDefault();
        submit.mutate({
          type: kind,
          need,
          maxParallel: kind === 'throughput' ? Number(parallel) : undefined,
        });
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
            required
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
  for (const r of usage.data ?? []) byModel.set(r.model, (byModel.get(r.model) ?? 0) + r.spend);
  const block = status.data?.last_blocks[0];
  const explained = block ? explainBlock(block) : null;
  const overall = service.data?.state ?? 'unknown';
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
        {explained ? (
          <p data-testid="last-block">{explained.text}</p>
        ) : (
          <p className="v2-muted">{p.noBlock}</p>
        )}
      </section>
      <section className="v2-card" aria-label={p.byModel}>
        <h3>{p.byModel}</h3>
        {byModel.size === 0 && <p className="v2-muted">{p.noUsage}</p>}
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
        {(tags.data?.series.length ?? 0) === 0 && <p className="v2-muted">{p.noUsage}</p>}
        <ul>
          {tags.data?.series.map((r) => (
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
      {form && <RequestForm kind={form} onDone={() => setForm(null)} />}
    </div>
  );
}
