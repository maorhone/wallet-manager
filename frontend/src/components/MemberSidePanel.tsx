import { Button, Drawer, Group, NumberInput, Stack, Text, TextInput, Title } from '@mantine/core';
import { useState } from 'react';
import { useGroupWrite, useMemberAction, useMoveTargets } from '../api/queries';
import type { Member, MemberActionKind, Policies } from '../api/types';
import { he } from '../i18n/he';
import { ChangeSummaryDialog, type SummaryRow } from './ChangeSummaryDialog';
import { Credits } from './Credits';
import { MemberActionDialog } from './MemberActionDialog';
import { Num } from './Num';

const ACTIONS: MemberActionKind[] = ['kick', 'restore', 'move', 'delete'];

/** A group member row, or a group head row (which also edits the base cap here, DR9). */
type PanelMember = Pick<
  Member,
  'upn' | 'display_name' | 'model_caps' | 'max_parallel' | 'rpm_limit' | 'model_tpm_limits'
> & { actions?: Member['actions']; base_cap?: number };

interface Props {
  member: PanelMember | null;
  /** Show the base cap field (Group heads tab); a single-row `members/caps` write. */
  withBaseCap?: boolean;
  groupId: string;
  period: string;
  policies: Policies | undefined;
  onClose: () => void;
}

const range = (p?: { min?: number; max?: number }) =>
  p ? `${he.groupPage.range} ${p.min ?? '–'}–${p.max ?? '–'}` : undefined;

/** Model caps and limits of one member, with the allowed ranges from /policies (FR-G2, FR-G4). */
export function MemberSidePanel({
  member,
  withBaseCap,
  groupId,
  period,
  policies,
  onClose,
}: Props) {
  return (
    <Drawer
      opened={member !== null}
      onClose={onClose}
      position="left"
      title={he.groupPage.sidePanel}
      data-testid="member-panel"
    >
      {/* Keyed by member so the form state resets for each person. */}
      {member && (
        <PanelBody
          key={member.upn}
          member={member}
          withBaseCap={withBaseCap}
          groupId={groupId}
          period={period}
          policies={policies}
          onClose={onClose}
        />
      )}
    </Drawer>
  );
}

function PanelBody({
  member,
  withBaseCap,
  groupId,
  period,
  policies,
  onClose,
}: Props & { member: PanelMember }) {
  const [baseCap, setBaseCap] = useState<number>(member.base_cap ?? 0);
  const [caps, setCaps] = useState<Record<string, number>>(member.model_caps);
  const [parallel, setParallel] = useState<number | null>(member.max_parallel);
  const [rpm, setRpm] = useState<number | null>(member.rpm_limit);
  const [newModel, setNewModel] = useState('');
  const [open, setOpen] = useState(false);
  const write = useGroupWrite(groupId, period);
  const act = useMemberAction(groupId, period);
  const [action, setAction] = useState<MemberActionKind | null>(null);
  const targets = useMoveTargets(groupId, member.upn, action === 'move');
  const available = ACTIONS.filter((a) => member.actions?.[a]);

  const base = `/groups/${groupId}/members/${encodeURIComponent(member.upn)}`;
  const capsChanged = JSON.stringify(caps) !== JSON.stringify(member.model_caps);
  const limitsChanged = parallel !== member.max_parallel || rpm !== member.rpm_limit;
  const baseChanged = !!withBaseCap && baseCap !== member.base_cap;

  const rows: SummaryRow[] = [
    ...(baseChanged
      ? [
          {
            key: 'base',
            label: he.groupPage.baseCap,
            before: <Credits value={member.base_cap ?? 0} />,
            after: <Credits value={baseCap} />,
          },
        ]
      : []),
    ...Object.keys({ ...member.model_caps, ...caps })
      .filter((m) => (caps[m] ?? 0) !== (member.model_caps[m] ?? 0))
      .map((m) => ({
        key: `cap-${m}`,
        label: <Num>{m}</Num>,
        before: <Credits value={member.model_caps[m] ?? 0} />,
        after: <Credits value={caps[m] ?? 0} />,
      })),
    ...(parallel !== member.max_parallel
      ? [
          {
            key: 'par',
            label: he.wallet.concurrent,
            before: member.max_parallel ?? '–',
            after: parallel ?? '–',
          },
        ]
      : []),
    ...(rpm !== member.rpm_limit
      ? [{ key: 'rpm', label: he.wallet.rpm, before: member.rpm_limit ?? '–', after: rpm ?? '–' }]
      : []),
  ];

  const confirm = async (reason: string, key: string) => {
    if (baseChanged)
      await write.mutateAsync({
        path: `/groups/${groupId}/members/caps`,
        body: [{ upn: member.upn, base_cap: baseCap }],
        reason,
        key: `${key}:base`,
      });
    if (limitsChanged)
      await write.mutateAsync({
        path: `${base}/limits`,
        body: { max_parallel: parallel, rpm_limit: rpm, model_tpm_limits: member.model_tpm_limits },
        reason,
        key: `${key}:limits`,
      });
    if (capsChanged)
      await write.mutateAsync({
        path: `${base}/model-caps`,
        body: caps,
        reason,
        key: `${key}:caps`,
      });
    onClose();
  };

  return (
    <Stack>
      <Title order={5}>{member.display_name}</Title>
      <Text size="sm" c="dimmed">
        <Num>{member.upn}</Num>
      </Text>
      {withBaseCap && (
        <NumberInput
          label={he.groupPage.baseCap}
          data-testid="panel-base-cap"
          hideControls
          min={0}
          value={baseCap}
          onChange={(v) => setBaseCap(Number(v) || 0)}
        />
      )}
      <Title order={6}>{he.groupPage.modelCaps}</Title>
      {Object.entries(caps).map(([m, v]) => (
        <NumberInput
          key={m}
          label={<Num>{m}</Num>}
          aria-label={`cap ${m}`}
          hideControls
          min={0}
          value={v}
          onChange={(x) => setCaps({ ...caps, [m]: Number(x) || 0 })}
        />
      ))}
      <Group align="flex-end">
        <TextInput
          label={he.groupPage.modelName}
          data-testid="new-model"
          value={newModel}
          onChange={(e) => setNewModel(e.currentTarget.value)}
        />
        <Button
          variant="default"
          disabled={newModel.trim() === '' || newModel.trim() in caps}
          onClick={() => {
            setCaps({ ...caps, [newModel.trim()]: 0 });
            setNewModel('');
          }}
        >
          {he.groupPage.addModel}
        </Button>
      </Group>
      <Title order={6}>{he.wallet.limits}</Title>
      <NumberInput
        label={he.wallet.concurrent}
        description={range(policies?.concurrency)}
        data-testid="limit-parallel"
        hideControls
        min={0}
        value={parallel ?? ''}
        onChange={(v) => setParallel(v === '' ? null : Number(v))}
      />
      <NumberInput
        label={he.wallet.rpm}
        description={range(policies?.rpm)}
        data-testid="limit-rpm"
        hideControls
        min={0}
        value={rpm ?? ''}
        onChange={(v) => setRpm(v === '' ? null : Number(v))}
      />
      <Button disabled={rows.length === 0} onClick={() => setOpen(true)} data-testid="panel-review">
        {he.groupPage.review}
      </Button>
      {available.length > 0 && (
        <Stack data-testid="member-actions">
          <Title order={6}>{he.memberActions.section}</Title>
          <Group>
            {available.map((a) => (
              <Button
                key={a}
                variant="default"
                color={a === 'delete' ? 'red' : undefined}
                data-testid={`action-${a}`}
                onClick={() => setAction(a)}
              >
                {he.memberActions[a]}
              </Button>
            ))}
          </Group>
        </Stack>
      )}
      {action && (
        <MemberActionDialog
          opened
          action={action}
          member={member}
          targets={targets.data}
          onClose={() => setAction(null)}
          onConfirm={async (reason, key, body) => {
            // The dialog only enables Delete once the typed text equals the upn.
            const payload = action === 'delete' ? { confirm_upn: member.upn } : body;
            await act.mutateAsync({ action, upn: member.upn, body: payload, reason, key });
            onClose();
          }}
        />
      )}
      <ChangeSummaryDialog
        opened={open}
        rows={rows}
        onClose={() => setOpen(false)}
        onConfirm={confirm}
      />
    </Stack>
  );
}
