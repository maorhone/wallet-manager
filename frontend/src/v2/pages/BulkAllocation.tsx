import { useState } from 'react';
import { useBulkAllocation, useBulkPreview, type BudgetRow } from '../../api/queries';
import { credits } from '../../format';
import { he } from '../../i18n/he';
import { apiPost } from '../../api/client';
import type { WriteResponse } from '../../api/types';
import { Drawer } from '../components/Drawer';
import { WriteOutcome } from '../components/WriteOutcome';

const s = he.v2.bulk;

export function BulkAllocation({
  groupId,
  rows,
  onClose,
}: {
  groupId: number;
  rows: BudgetRow[];
  onClose: () => void;
}) {
  const [amount, setAmount] = useState('');
  const [override, setOverride] = useState('');
  const [overrideAmount, setOverrideAmount] = useState('');
  // D-066: any number of individual exceptions; all of them go into preview and confirmation.
  const [extra, setExtra] = useState<Record<string, number>>({});
  const [reason, setReason] = useState('');
  const preview = useBulkPreview(groupId);
  const commit = useBulkAllocation(groupId);
  const employees = rows.filter((r) => r.kind === 'employee');
  const body = () => ({
    all: true,
    amount: Number(amount),
    overrides: {
      ...extra,
      ...(override && overrideAmount ? { [override]: Number(overrideAmount) } : {}),
    },
  });
  // Confirm exactly what was previewed; any later edit invalidates the preview (D-066/D-069).
  const [previewed, setPreviewed] = useState<{
    body: ReturnType<typeof body>;
    key: string;
    groupId: number;
  } | null>(null);
  // The preview is bound to the group it was made for (cached group routes reuse this drawer).
  const fresh =
    previewed !== null &&
    previewed.groupId === groupId &&
    JSON.stringify(previewed.body) === JSON.stringify(body());
  const p = fresh && !preview.isPending ? preview.data : undefined;
  return (
    <Drawer opened onClose={onClose} title={s.title}>
      {commit.isSuccess && commit.variables ? (
        <WriteOutcome
          result={commit.data}
          done={s.done}
          replay={() => {
            const v = commit.variables;
            return apiPost<WriteResponse>(
              `/groups/${v.groupId ?? groupId}/bulk-allocation`,
              v.body,
              {
                reason: v.reason,
                idempotencyKey: v.key,
              },
            );
          }}
        />
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const snapshot = body();
            setPreviewed({ body: snapshot, key: crypto.randomUUID(), groupId });
            preview.mutate(snapshot);
          }}
        >
          <label>
            {s.amount}
            <input
              type="number"
              min={0}
              required
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          </label>
          <label>
            {s.override}
            <select value={override} onChange={(e) => setOverride(e.target.value)}>
              <option value="" />
              {employees.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            {s.overrideAmount}
            <input
              type="number"
              min={0}
              value={overrideAmount}
              onChange={(e) => setOverrideAmount(e.target.value)}
            />
          </label>
          <button
            type="button"
            disabled={!override || overrideAmount === ''}
            onClick={() => {
              setExtra({ ...extra, [override]: Number(overrideAmount) });
              setOverride('');
              setOverrideAmount('');
            }}
          >
            {s.addException}
          </button>
          <ul>
            {Object.entries(extra).map(([upn, v]) => (
              <li key={upn}>
                <bdi dir="ltr">{upn}</bdi>: {credits(v)}{' '}
                <button
                  type="button"
                  onClick={() =>
                    setExtra(Object.fromEntries(Object.entries(extra).filter(([k]) => k !== upn)))
                  }
                >
                  {s.removeException}
                </button>
              </li>
            ))}
          </ul>
          <button type="submit" disabled={preview.isPending}>
            {s.preview}
          </button>
          {preview.isError && <p role="alert">{(preview.error as Error).message}</p>}
          {p && (
            <>
              <table>
                <thead>
                  <tr>
                    <th>{s.member}</th>
                    <th>{he.v2.sectionPage.before}</th>
                    <th>{he.v2.sectionPage.after}</th>
                  </tr>
                </thead>
                <tbody>
                  {p.rows.map((r) => (
                    <tr key={r.upn}>
                      <td>
                        <bdi dir="ltr">{r.upn}</bdi>
                      </td>
                      <td>{credits(r.before)}</td>
                      <td>{credits(r.after)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p>
                {s.remaining}: {credits(p.remaining_allocatable)}
              </p>
              {p.over_allocation_warning && <p role="alert">{s.overWarning}</p>}
              <label>
                {he.v2.sectionPage.reason}
                <input required value={reason} onChange={(e) => setReason(e.target.value)} />
              </label>
              <button
                type="button"
                disabled={!reason || p.exceeds_limit || commit.isPending || preview.isPending}
                onClick={() =>
                  previewed &&
                  commit.mutate({
                    body: previewed.body,
                    reason,
                    key: previewed.key,
                    groupId: previewed.groupId,
                  })
                }
              >
                {s.confirm}
              </button>
              {commit.isError && <p role="alert">{(commit.error as Error).message}</p>}
            </>
          )}
        </form>
      )}
    </Drawer>
  );
}
