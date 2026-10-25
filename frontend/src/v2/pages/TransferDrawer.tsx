import { useRef, useState } from 'react';
import { useTransfer, useTransferPreview, type BudgetRow } from '../../api/queries';
import { credits } from '../../format';
import { he } from '../../i18n/he';
import { Drawer } from '../components/Drawer';

const s = he.v2.sectionPage;

export function TransferDrawer({
  groupId,
  rows,
  initialSource,
  onClose,
}: {
  groupId: number;
  rows: BudgetRow[];
  initialSource: string;
  onClose: () => void;
}) {
  const [source, setSource] = useState(initialSource);
  const [target, setTarget] = useState('');
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const preview = useTransferPreview();
  const transfer = useTransfer();
  // Endpoints are "kind:id": a group and an initiative may share a numeric id.
  const endpoint = (ref: string) => {
    const i = ref.indexOf(':');
    return { kind: ref.slice(0, i), id: ref.slice(i + 1) };
  };
  const body = () => ({
    group_id: groupId,
    source: endpoint(source),
    target: endpoint(target),
    amount: Number(amount),
  });
  // The confirmation is bound to the exact payload that was previewed (D-066/D-069).
  const [previewed, setPreviewed] = useState<{ body: ReturnType<typeof body>; key: string } | null>(
    null,
  );
  // One Idempotency-Key per payload until its outcome is resolved: previewing again after a
  // lost response must replay the original transfer, not run a second one.
  const keyed = useRef<{ json: string; key: string } | null>(null);
  const current = JSON.stringify(body());
  const fresh =
    previewed !== null && !preview.isPending && JSON.stringify(previewed.body) === current;
  const ready = source && target && source !== target && Number(amount) > 0;
  const p = preview.data;
  return (
    <Drawer opened onClose={onClose} title={s.drawerTitle}>
      {transfer.isSuccess ? (
        <p role="status">{s.done}</p>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const snapshot = body();
            const json = JSON.stringify(snapshot);
            if (keyed.current?.json !== json) keyed.current = { json, key: crypto.randomUUID() };
            setPreviewed({ body: snapshot, key: keyed.current.key });
            preview.mutate(snapshot);
          }}
        >
          {(['from', 'to'] as const).map((side) => (
            <label key={side}>
              {s[side]}
              <select
                required
                value={side === 'from' ? source : target}
                onChange={(e) => (side === 'from' ? setSource : setTarget)(e.target.value)}
              >
                <option value="" />
                {rows.map((r) => (
                  <option key={`${r.kind}:${r.id}`} value={`${r.kind}:${r.id}`}>
                    {r.name}
                  </option>
                ))}
              </select>
            </label>
          ))}
          <label>
            {s.amount}
            <input
              type="number"
              min={1}
              required
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          </label>
          <button type="submit" disabled={!ready || preview.isPending}>
            {s.preview}
          </button>
          {preview.isError && <p role="alert">{(preview.error as Error).message}</p>}
          {p && fresh && (
            <table>
              <thead>
                <tr>
                  <th />
                  <th>{s.before}</th>
                  <th>{s.after}</th>
                </tr>
              </thead>
              <tbody>
                {(
                  [
                    [s.from, p.source],
                    [s.to, p.target],
                  ] as const
                ).map(([label, side]) => (
                  <tr key={label}>
                    <td>{label}</td>
                    <td>{credits(side.total)}</td>
                    <td>{credits(side.after)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {p && fresh && previewed && (
            <>
              <label>
                {s.reason}
                <input required value={reason} onChange={(e) => setReason(e.target.value)} />
              </label>
              <button
                type="button"
                disabled={!reason || transfer.isPending || preview.isPending}
                onClick={() =>
                  transfer.mutate({ body: previewed.body, reason, key: previewed.key })
                }
              >
                {s.confirm}
              </button>
              {transfer.isError && <p role="alert">{(transfer.error as Error).message}</p>}
            </>
          )}
        </form>
      )}
    </Drawer>
  );
}
