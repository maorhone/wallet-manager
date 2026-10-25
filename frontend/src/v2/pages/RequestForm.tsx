import { useRef, useState } from 'react';
import { useSubmitRequest } from '../../api/queries';
import { he } from '../../i18n/he';

const s = he.v2.reqPage;

export function RequestForm() {
  const submit = useSubmitRequest();
  const [type, setType] = useState<'credits' | 'initiative' | 'throughput'>('throughput');
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
          ...(type !== 'throughput' && { amount: Number(amount) }),
          ...(type === 'initiative' && { name }),
          ...(type === 'throughput' && { maxParallel: Number(parallel) }),
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
      <label>
        {s.type}
        <select value={type} onChange={(e) => setType(e.target.value as typeof type)}>
          {Object.entries(s.types).map(([k, v]) => (
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
          <input
            type="number"
            min="1"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            required
          />
        </label>
      ) : (
        <label>
          {s.maxParallel}
          <input
            type="number"
            min="1"
            value={parallel}
            onChange={(e) => setParallel(e.target.value)}
            required
          />
        </label>
      )}
      <label>
        {s.need}
        <textarea value={need} onChange={(e) => setNeed(e.target.value)} required />
      </label>
      <button type="submit" disabled={submit.isPending}>
        {s.submit}
      </button>
      {submit.isSuccess && <p role="status">{s.sent}</p>}
    </form>
  );
}
