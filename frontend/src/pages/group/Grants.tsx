import {
  Alert,
  Button,
  Card,
  Group,
  Loader,
  Modal,
  Stack,
  Table,
  Textarea,
  Title,
} from '@mantine/core';
import { useRef, useState } from 'react';
import {
  useGrantShares,
  useGrants,
  useGroupMembers,
  useMe,
  useShareWrite,
} from '../../api/queries';
import type { Grant, GrantShare, GroupSummary } from '../../api/types';
import { Credits } from '../../components/Credits';
import { DateText } from '../../components/DateText';
import { EmptyState } from '../../components/EmptyState';
import { ErrorState } from '../../components/ErrorState';
import { GrantList } from '../../components/GrantList';
import { ShareDialog } from '../../components/ShareDialog';
import { errorMessage } from '../../i18n/errors';
import { he } from '../../i18n/he';

const t = he.shares;

/** Close one share with a reason; the key is kept across retries. */
function CloseShare({
  share,
  onClose,
  onConfirm,
}: {
  share: GrantShare;
  onClose: () => void;
  onConfirm: (reason: string, key: string) => Promise<unknown>;
}) {
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const key = useRef(crypto.randomUUID());
  return (
    <Modal opened onClose={onClose} title={`${t.close}: ${share.display_name}`}>
      <Stack data-testid="share-close">
        <Alert variant="light">{t.closeWarning}</Alert>
        <Textarea
          label={he.summary.reason}
          data-testid="share-close-reason"
          value={reason}
          onChange={(e) => setReason(e.currentTarget.value)}
          required
        />
        {error && (
          <Alert color="red" variant="light" role="alert">
            {error}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            {he.groupPage.cancel}
          </Button>
          <Button
            color="red"
            data-testid="share-close-confirm"
            disabled={reason.trim() === ''}
            onClick={() =>
              onConfirm(reason.trim(), key.current).then(onClose, (e) => setError(errorMessage(e)))
            }
          >
            {he.summary.confirm}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

/** Tab "מענקים" (§12.5.2 tab 3): grants received, and share, change or close from the server `actions`. */
export default function Grants({
  groupId,
  period,
  summary,
}: {
  groupId: string;
  period: string;
  summary: GroupSummary;
}) {
  const grants = useGrants({ group: groupId });
  const { data: me } = useMe();
  const write = useShareWrite();
  const [open, setOpen] = useState<Grant | null>(null);
  const [sharing, setSharing] = useState<Grant | null>(null);
  const [edit, setEdit] = useState<GrantShare | null>(null);
  const [closing, setClosing] = useState<GrantShare | null>(null);
  const shares = useGrantShares(open?.id ?? 0, !!open);
  const members = useGroupMembers(groupId, period, !!sharing);
  const ro = summary.read_only;
  const grant = open && grants.data?.find((g) => g.id === open.id);

  if (grants.isPending) return <Loader aria-label={he.loading} />;
  if (grants.error || !grants.data)
    return <ErrorState error={grants.error} onRetry={grants.refetch} />;
  const tz = me?.timezone ?? 'Asia/Jerusalem';
  const can = !!grant?.actions.share && !ro;
  const active = (shares.data ?? []).filter((s) => s.status === 'active');
  return (
    <Stack>
      <GrantList
        grants={grants.data}
        actions={(g) => (
          <Group gap={4} wrap="nowrap">
            <Button
              size="compact-xs"
              variant="default"
              data-testid={`shares-${g.id}`}
              onClick={() => setOpen(g)}
            >
              {t.manage}
            </Button>
            {g.actions.share && !ro && g.status === 'active' && (
              <Button size="compact-xs" data-testid={`share-${g.id}`} onClick={() => setSharing(g)}>
                {t.share}
              </Button>
            )}
          </Group>
        )}
      />
      {grant && (
        <Card withBorder data-testid="grant-shares">
          <Title order={5}>
            {t.manage}: {grant.project_ref}
          </Title>
          {shares.isPending ? (
            <Loader aria-label={he.loading} />
          ) : shares.data && shares.data.length > 0 ? (
            <Table>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>{t.employee}</Table.Th>
                  <Table.Th>{he.pmo.amount}</Table.Th>
                  <Table.Th>{he.pmo.used}</Table.Th>
                  <Table.Th>{he.pmo.returned}</Table.Th>
                  <Table.Th>{he.pmo.expiry}</Table.Th>
                  <Table.Th>{he.pmo.status}</Table.Th>
                  {can && <Table.Th />}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {shares.data.map((s) => (
                  <Table.Tr key={s.id} data-testid={`share-item-${s.upn}`}>
                    <Table.Td>{s.display_name}</Table.Td>
                    <Table.Td>
                      <Credits value={s.amount} />
                    </Table.Td>
                    <Table.Td>
                      <Credits value={s.used} />
                    </Table.Td>
                    <Table.Td>
                      <Credits value={s.returned} />
                    </Table.Td>
                    <Table.Td>
                      <DateText value={s.expires_at} />
                    </Table.Td>
                    <Table.Td>{he.pmo.statuses[s.status] ?? s.status}</Table.Td>
                    {can && (
                      <Table.Td>
                        {s.status === 'active' && (
                          <Group gap={4} wrap="nowrap">
                            <Button
                              size="compact-xs"
                              data-testid={`share-edit-${s.upn}`}
                              onClick={() => setEdit(s)}
                            >
                              {t.change}
                            </Button>
                            <Button
                              size="compact-xs"
                              color="red"
                              data-testid={`share-close-${s.upn}`}
                              onClick={() => setClosing(s)}
                            >
                              {t.close}
                            </Button>
                          </Group>
                        )}
                      </Table.Td>
                    )}
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          ) : (
            <EmptyState message={t.noShares} />
          )}
        </Card>
      )}
      {sharing && members.isPending && <Loader aria-label={he.loading} />}
      {sharing && members.data && (
        <ShareDialog
          grant={sharing}
          members={members.data.members.filter((m) => m.status !== 'kicked')}
          selfUpn={me?.upn ?? ''}
          taken={active.map((s) => s.upn)}
          timezone={tz}
          onClose={() => setSharing(null)}
          onConfirm={(create, reason, key) =>
            write.mutateAsync({ grantId: sharing.id, create, reason, key })
          }
        />
      )}
      {edit && grant && (
        <ShareDialog
          grant={grant}
          members={[]}
          selfUpn={me?.upn ?? ''}
          taken={[]}
          timezone={tz}
          share={edit}
          onClose={() => setEdit(null)}
          onConfirm={([r], reason, key) =>
            write.mutateAsync({
              grantId: grant.id,
              shareId: edit.id,
              patch: { amount: r.amount, expires_at: r.expires_at },
              reason,
              key,
            })
          }
        />
      )}
      {closing && grant && (
        <CloseShare
          share={closing}
          onClose={() => setClosing(null)}
          onConfirm={(reason, key) =>
            write.mutateAsync({ grantId: grant.id, shareId: closing.id, reason, key })
          }
        />
      )}
    </Stack>
  );
}
