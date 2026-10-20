import {
  Alert,
  Button,
  Group,
  Modal,
  NumberInput,
  Stack,
  Table,
  Text,
  TextInput,
  Textarea,
} from '@mantine/core';
import { useRef, useState } from 'react';
import type { Grant } from '../api/types';
import { errorMessage } from '../i18n/errors';
import { he } from '../i18n/he';
import { date, todayIn, zonedInstant } from '../format';
import { Credits } from './Credits';

const t = he.pmo;
// Days are application-timezone days (NFR8), not the browser's.
const endIso = (d: string, tz: string) => zonedInstant(d, '23:59:59', tz);
const dayOf = (iso: string, tz: string) => todayIn(tz, new Date(iso));

export type GrantEditMode = 'extend' | 'increase' | 'close';

interface Props {
  grant: Grant;
  mode: GrantEditMode;
  /** Application timezone from /me. */
  timezone: string;
  /** Pool availability now, for the before/after effect. */
  available: number;
  onClose: () => void;
  /** `patch` is undefined for close early; the key stays the same while the user retries. */
  onConfirm: (
    patch: { amount?: number; expires_at?: string } | undefined,
    reason: string,
    key: string,
  ) => Promise<unknown>;
}

/** FR-P7 / FR-P6: extend the expiry, increase the amount, or close a grant early, with a reason. */
export function GrantEditDialog({ grant, mode, timezone, available, onClose, onConfirm }: Props) {
  const [expiry, setExpiry] = useState(dayOf(grant.expires_at, timezone));
  const [amount, setAmount] = useState<number>(grant.amount);
  const [reason, setReason] = useState('');
  // One key per submission: kept across retries of the same payload, new only when it changes.
  const attempt = useRef<{ sig: string; key: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const unused = Math.max(0, grant.amount - grant.used - grant.returned);
  const poolAfter =
    mode === 'increase'
      ? available - (amount - grant.amount)
      : mode === 'close'
        ? available + unused
        : available;
  const valid =
    mode === 'close' ||
    (mode === 'extend' ? expiry > dayOf(grant.expires_at, timezone) : amount > grant.amount);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      const patch =
        mode === 'extend'
          ? { expires_at: endIso(expiry, timezone) }
          : mode === 'increase'
            ? { amount }
            : undefined;
      const sig = JSON.stringify([patch ?? null, reason.trim()]);
      if (attempt.current?.sig !== sig) attempt.current = { sig, key: crypto.randomUUID() };
      await onConfirm(patch, reason.trim(), attempt.current.key);
      onClose();
    } catch (err) {
      setError(errorMessage(err));
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
            {t.closeWarning}: <Credits value={unused} />
          </Text>
        )}
        <Table data-testid="grant-edit-summary">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{he.summary.row}</Table.Th>
              <Table.Th>{he.summary.before}</Table.Th>
              <Table.Th>{he.summary.after}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {mode === 'extend' && (
              <Table.Tr>
                <Table.Td>{t.expiry}</Table.Td>
                <Table.Td data-testid="summary-before">{date(grant.expires_at, timezone)}</Table.Td>
                <Table.Td data-testid="summary-after">
                  {date(endIso(expiry, timezone), timezone)}
                </Table.Td>
              </Table.Tr>
            )}
            {mode === 'increase' && (
              <Table.Tr>
                <Table.Td>{t.amount}</Table.Td>
                <Table.Td data-testid="summary-before">
                  <Credits value={grant.amount} />
                </Table.Td>
                <Table.Td data-testid="summary-after">
                  <Credits value={amount} />
                </Table.Td>
              </Table.Tr>
            )}
            {mode === 'close' && (
              <Table.Tr>
                <Table.Td>{t.status}</Table.Td>
                <Table.Td>{t.statuses[grant.status]}</Table.Td>
                <Table.Td>{t.statuses.closed}</Table.Td>
              </Table.Tr>
            )}
            {mode !== 'extend' && (
              <Table.Tr>
                <Table.Td>{t.poolAvailable}</Table.Td>
                <Table.Td data-testid="pool-before">
                  <Credits value={available} />
                </Table.Td>
                <Table.Td data-testid="pool-after">
                  <Credits value={poolAfter} />
                </Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
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
