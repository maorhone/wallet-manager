import { Badge, Button, Loader, Stack, Text } from '@mantine/core';
import { useState } from 'react';
import { apiFetch, ApiError } from '../../api/client';
import { useGroupMembers, useGroupWrite, usePolicies } from '../../api/queries';
import type { GroupSummary, Member, MembersList } from '../../api/types';
import {
  AllocationTable,
  distributeEvenly,
  summarize,
  type AllocRow,
  type Edits,
} from '../../components/AllocationTable';
import { ChangeSummaryDialog } from '../../components/ChangeSummaryDialog';
import { Credits } from '../../components/Credits';
import { ErrorState } from '../../components/ErrorState';
import { MemberDefaultsDialog } from '../../components/MemberDefaultsDialog';
import { MemberSidePanel } from '../../components/MemberSidePanel';
import { Num } from '../../components/Num';
import { Pct } from '../../components/Pct';
import { API_STATE, StateBadge } from '../../components/StateBadge';
import { SyncBadge } from '../../components/SyncBadge';
import { credits } from '../../format';
import { he } from '../../i18n/he';

const t = he.groupPage;

function previousPeriod(period: string): string {
  const [y, m] = period.split('-').map(Number);
  const d = new Date(Date.UTC(y, m - 2, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

const ownerLabel = (m: Member) =>
  `${he.roles[m.owner.role] ?? m.owner.role}${
    m.owner.people.length ? `: ${m.owner.people.map((p) => p.display_name).join(', ')}` : ''
  }`;

interface Props {
  groupId: string;
  period: string;
  summary: GroupSummary;
}

export default function Members({ groupId, period, summary }: Props) {
  const members = useGroupMembers(groupId, period);
  const policies = usePolicies();
  const write = useGroupWrite(groupId, period);
  const [edits, setEdits] = useState<Edits>({});
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [review, setReview] = useState(false);
  const [panel, setPanel] = useState<Member | null>(null);
  const [defaults, setDefaults] = useState(false);
  const [copyMsg, setCopyMsg] = useState('');

  if (members.isPending) return <Loader aria-label={he.loading} />;
  if (members.error || !members.data)
    return <ErrorState error={members.error} onRetry={members.refetch} />;

  const readOnly = members.data.read_only || !summary.actions.edit_members;
  // Members who left are not part of the sums (server-side too).
  const active = members.data.members.filter((m) => m.status !== 'left');
  const rows: AllocRow[] = active.map((m) => ({
    key: m.upn,
    label: (
      <>
        {m.display_name} <Num>{m.upn}</Num>
      </>
    ),
    value: m.base_cap,
    editable: m.editable && !readOnly,
    owner: m.editable ? undefined : ownerLabel(m),
    cells: [
      <Credits key="s" value={m.shares_total} />,
      <Credits key="t" value={m.total} />,
      <Credits key="u" value={m.used} />,
      <Pct key="p" value={m.percent * 100} />,
      <Button key="mc" size="compact-xs" variant="subtle" onClick={() => setPanel(m)}>
        <Num>{Object.keys(m.model_caps).length}</Num> {t.edit}
      </Button>,
      <Num key="c">{m.max_parallel ?? '–'}</Num>,
      <Num key="r">{m.rpm_limit ?? '–'}</Num>,
      API_STATE[m.state] ? (
        <StateBadge key="st" state={API_STATE[m.state]!} />
      ) : (
        <Badge>{t.left}</Badge>
      ),
      <SyncBadge key="sy" status={m.sync_status} />,
    ],
  }));

  const changed = rows.filter((r) => edits[r.key] !== undefined && edits[r.key] !== r.value);
  const before = summarize(rows, {}, summary.wallet.base);
  const after = summarize(rows, edits, summary.wallet.base);

  const edit = (key: string, v: number | undefined) => {
    setRowErrors({});
    setEdits((e) => {
      const next = { ...e };
      if (v === undefined) delete next[key];
      else next[key] = v;
      return next;
    });
  };

  const fill = (e: Edits) => {
    setRowErrors({});
    setEdits(e);
  };

  const copyPrevious = async () => {
    try {
      const prev = await apiFetch<MembersList>(
        `/groups/${groupId}/members?period=${previousPeriod(period)}`,
      );
      const old = new Map(prev.members.map((m) => [m.upn, m.base_cap]));
      const e: Edits = {};
      for (const r of rows) if (r.editable && old.has(r.key)) e[r.key] = old.get(r.key)!;
      fill(e);
      setCopyMsg(Object.keys(e).length ? '' : t.copyEmpty);
    } catch {
      setCopyMsg(t.copyEmpty);
    }
  };

  return (
    <Stack>
      <AllocationTable
        rows={rows}
        edits={edits}
        total={summary.wallet.base}
        totalLabel={t.base}
        nameHeader={t.name}
        valueHeader={t.baseCap}
        extraHeaders={[
          t.shares,
          t.total,
          he.wallet.used,
          t.percent,
          t.modelCaps,
          t.concurrency,
          t.rpm,
          t.status,
          '',
        ]}
        rowErrors={rowErrors}
        readOnly={readOnly}
        onChange={edit}
        onSave={() => setReview(true)}
        actions={
          <>
            {summary.actions.edit_defaults && (
              <Button variant="default" onClick={() => setDefaults(true)}>
                {t.defaults}
              </Button>
            )}
            <Button
              variant="default"
              data-testid="distribute"
              onClick={() => fill(distributeEvenly(rows, summary.wallet.base))}
            >
              {t.distribute}
            </Button>
            <Button variant="default" data-testid="copy-previous" onClick={copyPrevious}>
              {t.copyPrevious}
            </Button>
          </>
        }
      />
      {copyMsg && <Text c="dimmed">{copyMsg}</Text>}
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
            path: `/groups/${groupId}/members/caps`,
            body: changed.map((r) => ({ upn: r.key, base_cap: edits[r.key] })),
            reason,
            key,
          });
          setEdits({});
          setRowErrors({});
        }}
        onError={(err) => {
          const rowsInfo = err instanceof ApiError ? err.envelope.details?.rows : undefined;
          if (Array.isArray(rowsInfo))
            setRowErrors(
              Object.fromEntries(rowsInfo.map((r: { upn?: string }) => [r.upn ?? '', t.rowError])),
            );
        }}
      />
      <MemberSidePanel
        member={panel}
        groupId={groupId}
        period={period}
        policies={policies.data}
        onClose={() => setPanel(null)}
      />
      <MemberDefaultsDialog
        opened={defaults}
        groupId={groupId}
        period={period}
        currentCap={summary.group.default_member_cap}
        onClose={() => setDefaults(false)}
      />
    </Stack>
  );
}
