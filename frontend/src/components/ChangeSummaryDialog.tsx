import { Alert, Button, Group, Modal, Stack, Table, Text, Textarea } from '@mantine/core';
import { useState, type ReactNode } from 'react';
import { errorMessage } from '../i18n/errors';
import { he } from '../i18n/he';

export interface SummaryRow {
  key: string;
  label: ReactNode;
  before: ReactNode;
  after: ReactNode;
}

interface Props {
  opened: boolean;
  rows: SummaryRow[];
  /** Effect on the parent (e.g. allocated and remaining-to-allocate after the change). */
  effect?: ReactNode;
  onClose: () => void;
  /** One write. The key stays the same while the user retries the same confirm. */
  onConfirm: (reason: string, idempotencyKey: string) => Promise<unknown>;
  /** Called with the server error so the caller can mark rows (`details.rows`). */
  onError?: (err: unknown) => void;
}

/** §12.3 change summary: before and after per row, effect on the parent, required reason. */
export function ChangeSummaryDialog({ opened, rows, effect, onClose, onConfirm, onError }: Props) {
  const [reason, setReason] = useState('');
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await onConfirm(reason.trim(), key);
      setReason('');
      setKey(crypto.randomUUID());
      onClose();
    } catch (err) {
      setError(errorMessage(err));
      onError?.(err);
      setKey(crypto.randomUUID()); // a failed confirm is a new attempt with new numbers
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title={he.summary.title} size="lg">
      <Stack data-testid="change-summary">
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{he.summary.row}</Table.Th>
              <Table.Th>{he.summary.before}</Table.Th>
              <Table.Th>{he.summary.after}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((r) => (
              <Table.Tr key={r.key}>
                <Table.Td>{r.label}</Table.Td>
                <Table.Td>{r.before}</Table.Td>
                <Table.Td>{r.after}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        {effect && (
          <Text size="sm">
            {he.summary.effect}: {effect}
          </Text>
        )}
        <Textarea
          label={he.summary.reason}
          data-testid="change-reason"
          value={reason}
          onChange={(e) => setReason(e.currentTarget.value)}
          required
        />
        {error && (
          <Alert color="red" variant="light" role="alert" data-testid="summary-error">
            {error}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            {he.groupPage.cancel}
          </Button>
          <Button
            data-testid="confirm-change"
            disabled={reason.trim() === '' || rows.length === 0}
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
