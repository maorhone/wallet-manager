import { Button, Group, Loader, Modal, Stack, Table } from '@mantine/core';
import { useState } from 'react';
import { useGrantShares, useGrants, usePolicies, useMe, useShareWrite } from '../../api/queries';
import type { DepartmentSummary, Grant, GroupHead } from '../../api/types';
import { Credits } from '../../components/Credits';
import { EmptyState } from '../../components/EmptyState';
import { MemberSidePanel } from '../../components/MemberSidePanel';
import { Num } from '../../components/Num';
import { ShareDialog } from '../../components/ShareDialog';
import { SyncBadge } from '../../components/SyncBadge';
import { he } from '../../i18n/he';

const t = he.groupHeads;

/** The head's group's active grants only; picking one opens the share dialog for that head. */
function ShareHead({
  head,
  timezone,
  selfUpn,
  onClose,
}: {
  head: GroupHead;
  timezone: string;
  selfUpn: string;
  onClose: () => void;
}) {
  const grants = useGrants({ group: String(head.group_id) });
  const write = useShareWrite();
  const [grant, setGrant] = useState<Grant | null>(null);
  const shares = useGrantShares(grant?.id ?? 0, !!grant);
  const active = (grants.data ?? []).filter((g) => g.status === 'active');
  if (grant && shares.data)
    return (
      <ShareDialog
        grant={grant}
        members={[head]}
        selfUpn={selfUpn}
        taken={shares.data.filter((s) => s.status === 'active').map((s) => s.upn)}
        timezone={timezone}
        onClose={onClose}
        onConfirm={(create, reason, key) =>
          write.mutateAsync({ grantId: grant.id, create, reason, key })
        }
      />
    );
  return (
    <Modal opened onClose={onClose} title={`${he.shares.share}: ${head.display_name}`}>
      <Stack data-testid="share-grant-choice">
        {grants.isPending || grant ? (
          <Loader aria-label={he.loading} />
        ) : active.length === 0 ? (
          <EmptyState message={he.groupPage.noGrants} />
        ) : (
          active.map((g) => (
            <Button key={g.id} variant="default" onClick={() => setGrant(g)}>
              {g.project_ref}
            </Button>
          ))
        )}
      </Stack>
    </Modal>
  );
}

/** Tab "ראשי קבוצות" (§12.5.4 tab 4): personal values of each group head, editable here only (DR9). */
export default function GroupHeads({
  summary,
  period,
}: {
  summary: DepartmentSummary;
  period: string;
}) {
  const policies = usePolicies();
  const { data: me } = useMe();
  const [panel, setPanel] = useState<GroupHead | null>(null);
  const [sharing, setSharing] = useState<GroupHead | null>(null);
  const heads = summary.group_heads;
  if (heads.length === 0) return <EmptyState message={t.empty} />;
  return (
    <Stack>
      <Table data-testid="group-heads-table">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t.head}</Table.Th>
            <Table.Th>{t.group}</Table.Th>
            <Table.Th>{he.groupPage.baseCap}</Table.Th>
            <Table.Th>{he.groupPage.shares}</Table.Th>
            <Table.Th>{he.groupPage.modelCaps}</Table.Th>
            <Table.Th>{he.groupPage.concurrency}</Table.Th>
            <Table.Th>{he.groupPage.rpm}</Table.Th>
            <Table.Th>{he.groupPage.status}</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {heads.map((h) => (
            <Table.Tr key={h.upn} data-testid={`head-row-${h.upn}`}>
              <Table.Td>
                {h.display_name} <Num>{h.upn}</Num>
              </Table.Td>
              <Table.Td>{h.group_name}</Table.Td>
              <Table.Td>
                <Credits value={h.base_cap} />
              </Table.Td>
              <Table.Td>
                <Credits value={h.shares} />
              </Table.Td>
              <Table.Td>
                <Num>{Object.keys(h.model_caps).length}</Num>
              </Table.Td>
              <Table.Td>
                <Num>{h.max_parallel ?? '–'}</Num>
              </Table.Td>
              <Table.Td>
                <Num>{h.rpm_limit ?? '–'}</Num>
              </Table.Td>
              <Table.Td>
                <SyncBadge status={h.sync_status} />
              </Table.Td>
              <Table.Td>
                {h.actions.edit ? (
                  <Group gap={4} wrap="nowrap">
                    <Button
                      size="compact-xs"
                      variant="default"
                      data-testid={`head-edit-${h.upn}`}
                      disabled={summary.read_only}
                      onClick={() => setPanel(h)}
                    >
                      {he.groupPage.edit}
                    </Button>
                    {h.actions.share && (
                      <Button
                        size="compact-xs"
                        data-testid={`head-share-${h.upn}`}
                        disabled={summary.read_only}
                        onClick={() => setSharing(h)}
                      >
                        {he.shares.share}
                      </Button>
                    )}
                  </Group>
                ) : (
                  <span data-testid={`head-readonly-${h.upn}`}>{t.readOnly}</span>
                )}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      <MemberSidePanel
        member={panel && { ...panel, actions: undefined }}
        withBaseCap
        groupId={String(panel?.group_id ?? '')}
        period={period}
        policies={policies.data}
        onClose={() => setPanel(null)}
      />
      {sharing && (
        <ShareHead
          head={sharing}
          timezone={me?.timezone ?? 'Asia/Jerusalem'}
          selfUpn={me?.upn ?? ''}
          onClose={() => setSharing(null)}
        />
      )}
    </Stack>
  );
}
