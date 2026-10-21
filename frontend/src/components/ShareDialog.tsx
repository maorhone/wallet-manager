import {
  Alert,
  Button,
  Checkbox,
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
import type { Grant, GrantShare, Member, Owner } from '../api/types';
import { errorMessage } from '../i18n/errors';
import { he } from '../i18n/he';
import { todayIn, zonedInstant } from '../format';
import { Credits } from './Credits';

const t = he.shares;
const dayOf = (iso: string, tz: string) => todayIn(tz, new Date(iso));

export interface ShareRowIn {
  upn: string;
  amount: number;
  expires_at: string;
}

interface Props {
  grant: Grant;
  /** Candidates to share to (create mode). */
  members: (Pick<Member, 'upn' | 'display_name'> & { owner?: Owner | null })[];
  /** The actor: their own row is disabled (DR9). */
  selfUpn: string;
  /** Members that already have an active share on this grant. */
  taken: string[];
  /** Application timezone from /me. */
  timezone: string;
  /** Change mode: the one share being edited. */
  share?: GrantShare;
  onClose: () => void;
  /** The key stays the same while the user retries the same payload. */
  onConfirm: (rows: ShareRowIn[], reason: string, key: string) => Promise<unknown>;
}

interface Row {
  on: boolean;
  amount: number;
  day: string;
}

/** F3: choose members, amounts and expiry (default and maximum: the grant's expiry), see the remainder. */
export function ShareDialog({
  grant,
  members,
  selfUpn,
  taken,
  timezone,
  share,
  onClose,
  onConfirm,
}: Props) {
  const grantDay = dayOf(grant.expires_at, timezone);
  const people = share
    ? [{ upn: share.upn, display_name: share.display_name, owner: null }]
    : members;
  const [rows, setRows] = useState<Record<string, Row>>(
    share
      ? {
          [share.upn]: {
            on: true,
            amount: share.amount,
            day: dayOf(share.expires_at, timezone),
          },
        }
      : {},
  );
  const [reason, setReason] = useState('');
  const attempt = useRef<{ sig: string; key: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const row = (upn: string): Row => rows[upn] ?? { on: false, amount: 0, day: grantDay };
  const patch = (upn: string, p: Partial<Row>) =>
    setRows({ ...rows, [upn]: { ...row(upn), ...p } });
  const chosen = people.filter((p) => row(p.upn).on);
  const budget = share ? grant.unshared + share.amount : grant.unshared;
  const remainder = budget - chosen.reduce((s, p) => s + row(p.upn).amount, 0);
  const late = (r: Row) => r.day === '' || r.day > grantDay;
  const bad = chosen.some((p) => row(p.upn).amount <= 0 || late(row(p.upn)));
  const valid = chosen.length > 0 && remainder >= 0 && !bad && reason.trim() !== '';

  // The grant's own expiry instant is the cap, so the grant's day maps to it exactly.
  const instant = (r: Row) =>
    r.day === grantDay
      ? grant.expires_at
      : share && r.day === dayOf(share.expires_at, timezone)
        ? share.expires_at
        : zonedInstant(r.day, '23:59:59', timezone);

  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      const out = chosen.map((p) => ({
        upn: p.upn,
        amount: row(p.upn).amount,
        expires_at: new Date(instant(row(p.upn))).toISOString(),
      }));
      const sig = JSON.stringify([out, reason.trim()]);
      if (attempt.current?.sig !== sig) attempt.current = { sig, key: crypto.randomUUID() };
      await onConfirm(out, reason.trim(), attempt.current.key);
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
      size="lg"
      title={`${share ? t.change : t.share}: ${grant.project_ref}`}
    >
      <Stack data-testid="share-dialog">
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t.employee}</Table.Th>
              <Table.Th>{he.pmo.amount}</Table.Th>
              <Table.Th>{he.pmo.expiry}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {people.map((p) => {
              const r = row(p.upn);
              const self = p.upn === selfUpn;
              const exists = taken.includes(p.upn);
              const off = self || exists;
              return (
                <Table.Tr key={p.upn} data-testid={`share-row-${p.upn}`}>
                  <Table.Td>
                    <Checkbox
                      label={p.display_name}
                      data-testid={`share-check-${p.upn}`}
                      checked={r.on}
                      disabled={off || !!share}
                      onChange={(e) => patch(p.upn, { on: e.currentTarget.checked })}
                    />
                    {self && p.owner && (
                      <Text size="xs" c="dimmed">
                        {t.ownRow(he.roles[p.owner.role] ?? p.owner.role)}
                      </Text>
                    )}
                    {exists && (
                      <Text size="xs" c="dimmed">
                        {t.alreadyShared}
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <NumberInput
                      aria-label={he.pmo.amount}
                      data-testid={`share-amount-${p.upn}`}
                      min={0}
                      disabled={!r.on}
                      value={r.amount}
                      onChange={(v) => patch(p.upn, { amount: Number(v) })}
                    />
                  </Table.Td>
                  <Table.Td>
                    <TextInput
                      type="date"
                      aria-label={he.pmo.expiry}
                      data-testid={`share-expiry-${p.upn}`}
                      disabled={!r.on}
                      max={grantDay}
                      value={r.day}
                      error={r.on && late(r) ? t.expiryAfterGrant : undefined}
                      onChange={(e) => patch(p.upn, { day: e.currentTarget.value })}
                    />
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
        <Group>
          <Text>{t.remainder}:</Text>
          <Text fw={700} c={remainder < 0 ? 'red' : undefined} data-testid="share-remainder">
            <Credits value={remainder} />
          </Text>
        </Group>
        {remainder < 0 && (
          <Alert color="red" variant="light" data-testid="share-over">
            {t.over}
          </Alert>
        )}
        <Textarea
          label={he.summary.reason}
          data-testid="share-reason"
          value={reason}
          onChange={(e) => setReason(e.currentTarget.value)}
          required
        />
        {error && (
          <Alert color="red" variant="light" role="alert" data-testid="share-error">
            {error}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            {he.groupPage.cancel}
          </Button>
          <Button data-testid="share-confirm" disabled={!valid} loading={busy} onClick={confirm}>
            {he.summary.confirm}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
