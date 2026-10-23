import { Alert, Button, Group, Loader, Modal, Stack, Textarea } from '@mantine/core';
import { useRef, useState } from 'react';
import { useServiceKeyWrite, useServiceKeys } from '../api/queries';
import type { ServiceKey } from '../api/types';
import { errorMessage } from '../i18n/errors';
import { he } from '../i18n/he';
import { ErrorState } from './ErrorState';
import { SecretOnceModal } from './SecretOnceModal';
import { ServiceKeyDialog } from './ServiceKeyDialog';
import { ServiceKeyList } from './ServiceKeyList';

const t = he.serviceKeys;

/** Rotate or revoke one key with a required reason (§12.5.2 tab 5). */
function ChangeKeyModal({
  mode,
  serviceKey,
  onClose,
  onConfirm,
}: {
  mode: 'rotate' | 'revoke';
  serviceKey: ServiceKey;
  onClose: () => void;
  onConfirm: (reason: string, key: string) => Promise<{ secret?: string } | void>;
}) {
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const key = useRef(crypto.randomUUID());
  return (
    <Modal
      opened
      onClose={onClose}
      title={`${mode === 'rotate' ? t.rotate : t.revoke}: ${serviceKey.alias}`}
    >
      <Stack data-testid={`service-key-${mode}-dialog`}>
        <Alert variant="light">{mode === 'rotate' ? t.rotateWarning : t.revokeWarning}</Alert>
        <Textarea
          label={he.summary.reason}
          data-testid={`service-key-${mode}-reason`}
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
            color={mode === 'revoke' ? 'red' : undefined}
            data-testid={`service-key-${mode}-confirm`}
            disabled={reason.trim() === ''}
            loading={busy}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await onConfirm(reason.trim(), key.current);
                onClose();
              } catch (err) {
                setError(errorMessage(err));
              } finally {
                setBusy(false);
              }
            }}
          >
            {he.summary.confirm}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

/** Keys of one owner (§12.5.2 tab 5, §12.5.4 tab 6): list, create (secret shown once), rotate, revoke. */
export function ServiceKeysPanel({
  ownerKind,
  ownerId,
  manage,
  parent,
}: {
  ownerKind: 'group' | 'system';
  ownerId: number;
  manage: boolean;
  /** Group wallet allocation before creating a key (shown in the change summary). */
  parent?: { allocated: number; base: number };
}) {
  const keys = useServiceKeys(ownerKind, ownerId);
  const [secret, setSecret] = useState<string | null>(null);
  const write = useServiceKeyWrite(setSecret);
  const [creating, setCreating] = useState(false);
  const [rotating, setRotating] = useState<ServiceKey | null>(null);
  const [revoking, setRevoking] = useState<ServiceKey | null>(null);

  if (keys.isPending) return <Loader aria-label={he.loading} />;
  if (keys.error || !keys.data) return <ErrorState error={keys.error} onRetry={keys.refetch} />;

  return (
    <Stack>
      {manage && (
        <Group justify="flex-end">
          <Button data-testid="create-service-key" onClick={() => setCreating(true)}>
            {t.create}
          </Button>
        </Group>
      )}
      <ServiceKeyList
        items={keys.data.items}
        manage={manage}
        onRotate={setRotating}
        onRevoke={setRevoking}
      />
      {creating && (
        <ServiceKeyDialog
          ownerKind={ownerKind}
          ownerId={ownerId}
          onClose={() => setCreating(false)}
          onConfirm={(body, reason, key) =>
            write.mutateAsync({ action: 'create', body, reason, key })
          }
          parent={parent}
        />
      )}
      {rotating && (
        <ChangeKeyModal
          mode="rotate"
          serviceKey={rotating}
          onClose={() => setRotating(null)}
          onConfirm={async (reason, key) => {
            await write.mutateAsync({
              action: 'rotate',
              id: rotating.id,
              ownerKind,
              ownerId,
              reason,
              key,
            });
          }}
        />
      )}
      {revoking && (
        <ChangeKeyModal
          mode="revoke"
          serviceKey={revoking}
          onClose={() => setRevoking(null)}
          onConfirm={(reason, key) =>
            write.mutateAsync({
              action: 'revoke',
              id: revoking.id,
              ownerKind,
              ownerId,
              reason,
              key,
            })
          }
        />
      )}
      {secret && <SecretOnceModal secret={secret} onClose={() => setSecret(null)} />}
    </Stack>
  );
}
