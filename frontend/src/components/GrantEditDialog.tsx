import {
  Alert,
  Button,
  Group,
  Modal,
  NumberInput,
  Stack,
  Text,
  TextInput,
  Textarea,
} from '@mantine/core';
import { useState } from 'react';
import type { Grant } from '../api/types';
import { errorMessage } from '../i18n/errors';
import { he } from '../i18n/he';
import { Credits } from './Credits';

const t = he.pmo;
const endIso = (d: string) => new Date(`${d}T23:59:59`).toISOString();
const dayOf = (iso: string) => new Date(iso).toLocaleDateString('en-CA');

export type GrantEditMode = 'extend' | 'increase' | 'close';

interface Props {
  grant: Grant;
  mode: GrantEditMode;
  onClose: () => void;
  /** `patch` is undefined for close early; the key stays the same while the user retries. */
  onConfirm: (
    patch: { amount?: number; expires_at?: string } | undefined,
    reason: string,
    key: string,
  ) => Promise<unknown>;
}

/** FR-P7 / FR-P6: extend the expiry, increase the amount, or close a grant early, with a reason. */
export function GrantEditDialog({ grant, mode, onClose, onConfirm }: Props) {
  const [expiry, setExpiry] = useState(dayOf(grant.expires_at));
  const [amount, setAmount] = useState<number>(grant.amount);
  const [reason, setReason] = useState('');
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const valid =
    mode === 'close' ||
    (mode === 'extend' ? expiry > dayOf(grant.expires_at) : amount > grant.amount);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      const patch =
        mode === 'extend'
          ? { expires_at: endIso(expiry) }
          : mode === 'increase'
            ? { amount }
            : undefined;
      await onConfirm(patch, reason.trim(), key);
      onClose();
    } catch (err) {
      setError(errorMessage(err));
      setKey(crypto.randomUUID());
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      opened
      onClose={onClose}
      title={`${mode === 'extend' ? t.extend : mode === 'increase' ? t.increase : t.closeEarly}: ${grant.project_ref}`}
    >
      <Stack data-testid="grant-edit">
        {mode === 'extend' && (
          <TextInput
            type="date"
            label={t.newExpiry}
            data-testid="grant-edit-expiry"
            value={expiry}
            onChange={(e) => setExpiry(e.currentTarget.value)}
          />
        )}
        {mode === 'increase' && (
          <NumberInput
            label={t.newAmount}
            data-testid="grant-edit-amount"
            value={amount}
            min={grant.amount}
            onChange={(v) => setAmount(Number(v))}
          />
        )}
        {mode === 'close' && (
          <Text size="sm">
            {t.closeWarning}: <Credits value={grant.amount - grant.used} />
          </Text>
        )}
        <Textarea
          label={he.summary.reason}
          data-testid="grant-edit-reason"
          value={reason}
          onChange={(e) => setReason(e.currentTarget.value)}
          required
        />
        {error && (
          <Alert color="red" variant="light" role="alert" data-testid="grant-edit-error">
            {error}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            {he.groupPage.cancel}
          </Button>
          <Button
            data-testid="grant-edit-confirm"
            disabled={reason.trim() === '' || !valid}
            loading={busy}
            onClick={confirm}
          >
            {he.summary.confirm}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
