import { useRef, useState } from 'react';
import { ErrorState } from '../../components/ErrorState';
import { useSearchParams } from 'react-router-dom';
import { useSubmitRequest } from '../../api/queries';
import { he } from '../../i18n/he';

const s = he.v2.reqPage;

export function RequestForm() {
  const submit = useSubmitRequest();
  // A change request for an existing initiative/system carries that subject as its target.
  const [params] = useSearchParams();
  const tk = params.get('target_kind');
  const ti = Number(params.get('target_id'));
  const target =
    (tk === 'initiative' || tk === 'system') && Number.isInteger(ti) && ti > 0
      ? {
          kind: tk as 'initiative' | 'system',
          id: ti,
          name: params.get('target_name') ?? String(ti),
        }
      : undefined;
  const [type, setType] = useState<'credits' | 'initiative' | 'throughput'>(
    target ? 'credits' : 'throughput',
  );
  const [need, setNeed] = useState('');
  const [amount, setAmount] = useState('');
  const [name, setName] = useState('');
  const [parallel, setParallel] = useState('');
  // One key per distinct submission: a retry of the same payload replays, a new one does not.
  const sent = useRef<{ sig: string; key: string } | null>(null);
  return (
    <form
      className="v2-card"
      onSubmit={(e) => {
        e.preventDefault();
        const body = {
          type,
          need,
          // a new initiative never carries an existing target
          ...(target && type !== 'initiative' && { target: { kind: target.kind, id: target.id } }),
          // D-044/D-045: the numbers are optional; an empty field is omitted, never sent as 0
          ...(type !== 'throughput' && amount !== '' && { amount: Number(amount) }),
          ...(type === 'initiative' && { name }),
          ...(type === 'throughput' && parallel !== '' && { maxParallel: Number(parallel) }),
        };
        const sig = JSON.stringify(body);
        if (sent.current?.sig !== sig) sent.current = { sig, key: crypto.randomUUID() };
        submit.mutate(
          { ...body, key: sent.current.key },
          {
            onSuccess: () => {
              sent.current = null;
            },
          },
        );
      }}
    >
      <h2>{s.newRequest}</h2>
      {target && (
        <p>
          {s.target}: <bdi>{target.name}</bdi>
        </p>
      )}
      <label>
        {s.type}
        <select value={type} onChange={(e) => setType(e.target.value as typeof type)}>
          {Object.entries(s.types)
            .filter(([k]) => !target || k !== 'initiative')
            .map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
        </select>
      </label>
      {type === 'initiative' && (
        <label>
          {s.initiativeName}
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
      )}
      {type !== 'throughput' ? (
        <label>
          {s.amount}
          <input type="number" min="1" value={amount} onChange={(e) => setAmount(e.target.value)} />
        </label>
      ) : (
        <label>
          {s.maxParallel}
          <input
            type="number"
            min="1"
            value={parallel}
            onChange={(e) => setParallel(e.target.value)}
          />
        </label>
      )}
      <label>
        {s.need}
        <textarea
          value={need}
          maxLength={1000}
          onChange={(e) => setNeed(e.target.value)}
          required
        />
      </label>
      <button type="submit" disabled={submit.isPending}>
        {s.submit}
      </button>
      {submit.isSuccess && <p role="status">{s.sent}</p>}
      {submit.error && <ErrorState error={submit.error} />}
    </form>
  );
}
