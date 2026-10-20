import { Button, Group, NumberInput, Stack } from '@mantine/core';
import { useState } from 'react';
import { useDepartmentWrite } from '../../api/queries';
import type { DepartmentSummary } from '../../api/types';
import {
  AllocationTable,
  summarize,
  type AllocRow,
  type Edits,
} from '../../components/AllocationTable';
import { ChangeSummaryDialog } from '../../components/ChangeSummaryDialog';
import { Credits } from '../../components/Credits';
import { usePeriod } from '../../components/PeriodSelector';
import { SyncBadge } from '../../components/SyncBadge';
import { credits } from '../../format';
import { he } from '../../i18n/he';

const t = he.departmentPage;

/** Tab 2 (F1): every group, the PMO pool and the reserve are rows of one allocation table. */
export default function Split({ summary: s }: { summary: DepartmentSummary }) {
  const write = useDepartmentWrite();
  const { readOnly: pastPeriod } = usePeriod();
  const [edits, setEdits] = useState<Edits>({});
  const [review, setReview] = useState(false);
  const [budget, setBudget] = useState<number | undefined>();
  const [budgetReview, setBudgetReview] = useState(false);

  const readOnly = s.read_only || pastPeriod || !s.actions.edit_split;
  const sync = s.split.pmo_reserve_sync_status;
  const syncByGroup = new Map(s.groups.map((g) => [g.id, g.sync_status]));
  const rows: AllocRow[] = [
    ...s.split.groups.map((g) => ({
      key: `g:${g.id}`,
      label: g.name,
      value: g.base,
      editable: !readOnly,
      cells: [<SyncBadge key="s" status={syncByGroup.get(g.id) ?? 'applied'} />],
    })),
    {
      key: 'pmo',
      label: t.pmoPool,
      value: s.split.pmo_pool,
      editable: !readOnly,
      cells: [<SyncBadge key="s" status={sync} />],
    },
    {
      key: 'reserve',
      label: t.reserve,
      value: s.split.reserve,
      editable: !readOnly,
      cells: [<SyncBadge key="s" status={sync} />],
    },
  ];
  const changed = rows.filter((r) => edits[r.key] !== undefined && edits[r.key] !== r.value);
  const before = summarize(rows, {}, s.wallet.total);
  const after = summarize(rows, edits, s.wallet.total);
  const val = (key: string) => edits[key] ?? rows.find((r) => r.key === key)!.value;

  const edit = (key: string, v: number | undefined) =>
    setEdits((e) => {
      const next = { ...e };
      if (v === undefined) delete next[key];
      else next[key] = v;
      return next;
    });

  const canBudget = s.actions.edit_budget && !s.read_only && !pastPeriod;
  return (
    <Stack>
      {canBudget && (
        <Group align="flex-end" data-testid="budget-edit">
          <NumberInput
            label={t.budget}
            data-testid="budget-input"
            hideControls
            min={0}
            allowNegative={false}
            decimalScale={2}
            thousandSeparator=","
            value={budget ?? s.wallet.total}
            onChange={(v) => setBudget(v === '' ? undefined : Number(v))}
          />
          <Button
            data-testid="budget-save"
            variant="default"
            disabled={budget === undefined || budget === s.wallet.total}
            onClick={() => setBudgetReview(true)}
          >
            {t.editBudget}
          </Button>
        </Group>
      )}
      <AllocationTable
        rows={rows}
        edits={edits}
        total={s.wallet.total}
        totalLabel={t.budget}
        nameHeader={he.groupPage.name}
        valueHeader={t.base}
        extraHeaders={['']}
        readOnly={readOnly}
        onChange={edit}
        onSave={() => setReview(true)}
      />
      <ChangeSummaryDialog
        opened={review}
        rows={changed.map((r) => ({
          key: r.key,
          label: r.label,
          before: <Credits value={r.value} />,
          after: <Credits value={edits[r.key]} />,
        }))}
        effect={he.summary.parentEffect(
          `${credits(before.allocated)} → ${credits(after.allocated)}`,
          credits(after.remaining),
        )}
        onClose={() => setReview(false)}
        onConfirm={async (reason, key) => {
          await write.mutateAsync({
            path: `/departments/${s.department.id}/split`,
            body: {
              groups: s.split.groups.map((g) => ({ group_id: g.id, base: val(`g:${g.id}`) })),
              pmo_pool: val('pmo'),
              reserve: val('reserve'),
            },
            reason,
            key,
          });
          setEdits({});
        }}
      />
      <ChangeSummaryDialog
        opened={budgetReview}
        rows={[
          {
            key: 'budget',
            label: t.budget,
            before: <Credits value={s.wallet.total} />,
            after: <Credits value={budget ?? s.wallet.total} />,
          },
        ]}
        onClose={() => setBudgetReview(false)}
        onConfirm={async (reason, key) => {
          await write.mutateAsync({
            path: `/departments/${s.department.id}/budget`,
            body: { budget, budget_period: '1mo' },
            reason,
            key,
          });
          setBudget(undefined);
        }}
      />
    </Stack>
  );
}
