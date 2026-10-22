import { Alert, Button, Group, Loader, Modal, Stack, Textarea } from '@mantine/core';
import { useRef, useState } from 'react';
import { useServiceKeyWrite, useServiceKeys } from '../../api/queries';
import type { GroupSummary, ServiceKey } from '../../api/types';
import { ErrorState } from '../../components/ErrorState';
import { SecretOnceModal } from '../../components/SecretOnceModal';
import { ServiceKeyDialog } from '../../components/ServiceKeyDialog';
import { ServiceKeyList } from '../../components/ServiceKeyList';
import { errorMessage } from '../../i18n/errors';
import { he } from '../../i18n/he';

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

/** Tab "מפתחות שירות" (§12.5.2 tab 5): list, create (secret shown once), rotate and revoke. */
export default function ServiceKeys({
  groupId,
  summary,
}: {
  groupId: string;
  summary: GroupSummary;
}) {
  const ownerId = Number(groupId);
  const keys = useServiceKeys('group', ownerId);
  const write = useServiceKeyWrite();
  const [creating, setCreating] = useState(false);
  const [rotating, setRotating] = useState<ServiceKey | null>(null);
  const [revoking, setRevoking] = useState<ServiceKey | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const manage = summary.actions.service_keys.manage;

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
      <ServiceKeyList items={keys.data.items} onRotate={setRotating} onRevoke={setRevoking} />
      {creating && (
        <ServiceKeyDialog
          ownerKind="group"
          ownerId={ownerId}
          onClose={() => setCreating(false)}
          onConfirm={(body, reason, key) =>
            write.mutateAsync({ action: 'create', body, reason, key })
          }
          onCreated={setSecret}
        />
      )}
      {rotating && (
        <ChangeKeyModal
          mode="rotate"
          serviceKey={rotating}
          onClose={() => setRotating(null)}
          onConfirm={async (reason, key) => {
            const res = await write.mutateAsync({
              action: 'rotate',
              id: rotating.id,
              ownerKind: 'group',
              ownerId,
              reason,
              key,
            });
            if (res.secret) setSecret(res.secret);
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
              ownerKind: 'group',
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
