import { useState } from 'react';
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
  const [key] = useState(() => crypto.randomUUID());
  const preview = useTransferPreview();
  const transfer = useTransfer();
  const find = (id: string) => rows.find((r) => r.id === id);
  const body = () => ({
    group_id: groupId,
    source: { kind: find(source)?.kind ?? 'employee', id: source },
    target: { kind: find(target)?.kind ?? 'employee', id: target },
    amount: Number(amount),
  });
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
            preview.mutate(body());
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
                  <option key={r.id} value={r.id}>
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
          {p && (
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
          {p && (
            <>
              <label>
                {s.reason}
                <input required value={reason} onChange={(e) => setReason(e.target.value)} />
              </label>
              <button
                type="button"
                disabled={!reason || transfer.isPending}
                onClick={() => transfer.mutate({ body: body(), reason, key })}
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
