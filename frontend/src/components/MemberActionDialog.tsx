import {
  Alert,
  Button,
  Group,
  Modal,
  Select,
  Stack,
  Text,
  TextInput,
  Textarea,
} from '@mantine/core';
import { useState } from 'react';
import type { MemberActionKind, MoveTarget } from '../api/types';
import { errorMessage } from '../i18n/errors';
import { he } from '../i18n/he';
import { Num } from './Num';

export type { MoveTarget };

interface Props {
  opened: boolean;
  action: MemberActionKind;
  member: { upn: string; display_name: string };
  /** Move only: groups the actor may move the member to. */
  targets?: MoveTarget[];
  onClose: () => void;
  /** `extra` is the request body for move; the key stays the same while the user retries. */
  onConfirm: (
    reason: string,
    idempotencyKey: string,
    extra?: { to_group_id: number },
  ) => Promise<unknown>;
}

const t = he.memberActions;

/** Reason (always), target group (move) and typed upn (delete) before a member action. */
export function MemberActionDialog({ opened, action, member, targets, onClose, onConfirm }: Props) {
  const [reason, setReason] = useState('');
  const [target, setTarget] = useState<string | null>(null);
  const [typed, setTyped] = useState('');
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const ready =
    reason.trim() !== '' &&
    (action !== 'move' || target !== null) &&
    (action !== 'delete' || typed === member.upn);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await onConfirm(
        reason.trim(),
        key,
        action === 'move' ? { to_group_id: Number(target) } : undefined,
      );
      setReason('');
      setTarget(null);
      setTyped('');
      setKey(crypto.randomUUID());
      onClose();
    } catch (err) {
      setError(errorMessage(err));
      setKey(crypto.randomUUID());
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title={`${t[action]}: ${member.display_name}`}>
      <Stack data-testid="member-action-dialog">
        <Text size="sm">{t.descriptions[action]}</Text>
        {action === 'move' && (
          <Select
            label={t.target}
            data-testid="move-target"
            placeholder={targets?.length === 0 ? t.noTargets : undefined}
            data={(targets ?? []).map((g) => ({
              value: String(g.group_id),
              label: `${g.name} (${g.department_name})`,
            }))}
            value={target}
            onChange={setTarget}
          />
        )}
        {action === 'delete' && (
          <>
            <Alert color="red" variant="light">
              {t.deleteWarning}
            </Alert>
            <TextInput
              label={t.confirmUpn}
              description={<Num>{member.upn}</Num>}
              data-testid="confirm-upn"
              value={typed}
              onChange={(e) => setTyped(e.currentTarget.value)}
            />
          </>
        )}
        <Textarea
          label={t.reason}
          data-testid="action-reason"
          value={reason}
          onChange={(e) => setReason(e.currentTarget.value)}
          required
        />
        {error && (
          <Alert color="red" variant="light" role="alert" data-testid="action-error">
            {error}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            {he.groupPage.cancel}
          </Button>
          <Button
            data-testid="confirm-action"
            color={action === 'delete' ? 'red' : undefined}
            disabled={!ready}
            loading={busy}
            onClick={confirm}
          >
            {t.confirm}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
