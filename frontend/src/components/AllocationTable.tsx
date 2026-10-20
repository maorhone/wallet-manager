import { Box, Button, Group, NumberInput, Table, Text } from '@mantine/core';
import type { ReactNode } from 'react';
import { he } from '../i18n/he';
import { Credits } from './Credits';

export interface AllocRow {
  key: string;
  label: ReactNode;
  /** Current saved value. */
  value: number;
  editable: boolean;
  /** For read-only rows: who can change the value (§12.1). */
  owner?: string;
  /** Shown dimmed (e.g. suspended members). */
  muted?: boolean;
  /** Extra cells after the value column. */
  cells?: ReactNode[];
}

/** Edited values by row key; rows not listed keep their saved value. */
export type Edits = Record<string, number>;

const current = (r: AllocRow, edits: Edits) => edits[r.key] ?? r.value;

/** Footer math (client preview only; the server stays authoritative). */
export function summarize(rows: AllocRow[], edits: Edits, total: number) {
  const allocated = rows.reduce((s, r) => s + current(r, edits), 0);
  return { allocated, remaining: total - allocated, negative: allocated > total };
}

/** "Distribute evenly": (total − read-only rows) / editable rows, floored. */
export function distributeEvenly(rows: AllocRow[], total: number): Edits {
  const fixed = rows.filter((r) => !r.editable).reduce((s, r) => s + r.value, 0);
  const editable = rows.filter((r) => r.editable);
  const each = editable.length ? Math.max(0, Math.floor((total - fixed) / editable.length)) : 0;
  return Object.fromEntries(editable.map((r) => [r.key, each]));
}

export interface AllocationTableProps {
  rows: AllocRow[];
  edits?: Edits;
  /** Parent total the rows are allocated from. */
  total: number;
  totalLabel: string;
  nameHeader: string;
  valueHeader: string;
  extraHeaders?: string[];
  /** Row-level server errors by row key (from `details.rows`). */
  rowErrors?: Record<string, string>;
  /** Past periods: no inputs, no footer actions. */
  readOnly?: boolean;
  onChange?: (key: string, value: number | undefined) => void;
  onSave?: () => void;
  /** Helper buttons next to Save. */
  actions?: ReactNode;
}

export function AllocationTable(p: AllocationTableProps) {
  const edits = p.edits ?? {};
  const { allocated, remaining, negative } = summarize(p.rows, edits, p.total);
  const dirty = p.rows.some((r) => edits[r.key] !== undefined && edits[r.key] !== r.value);
  return (
    <Box data-testid="allocation-table">
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{p.nameHeader}</Table.Th>
            <Table.Th>{p.valueHeader}</Table.Th>
            {p.extraHeaders?.map((h) => (
              <Table.Th key={h}>{h}</Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {p.rows.map((r) => {
            const error = p.rowErrors?.[r.key];
            return (
              <Table.Tr
                key={r.key}
                data-row={r.key}
                data-muted={r.muted ? 'true' : undefined}
                style={r.muted ? { opacity: 0.6 } : undefined}
              >
                <Table.Td>{r.label}</Table.Td>
                <Table.Td>
                  {r.editable && !p.readOnly ? (
                    <NumberInput
                      aria-label={`${p.valueHeader} ${r.key}`}
                      data-testid={`alloc-input-${r.key}`}
                      hideControls
                      min={0}
                      allowNegative={false}
                      decimalScale={2}
                      w={120}
                      value={current(r, edits)}
                      error={error}
                      onChange={(v) => p.onChange?.(r.key, v === '' ? undefined : Number(v))}
                    />
                  ) : (
                    <>
                      <Credits value={current(r, edits)} />
                      {r.owner && (
                        <Text size="xs" c="dimmed" data-testid={`alloc-owner-${r.key}`}>
                          {he.groupPage.readOnlyOwner}: {r.owner}
                        </Text>
                      )}
                    </>
                  )}
                </Table.Td>
                {r.cells?.map((c, i) => (
                  <Table.Td key={i}>{c}</Table.Td>
                ))}
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
      <Box
        data-testid="alloc-footer"
        data-negative={negative}
        pos="sticky"
        bottom={0}
        p="sm"
        bg={negative ? 'red.0' : 'gray.0'}
        style={{ borderTop: `2px solid ${negative ? '#c92a2a' : '#dee2e6'}` }}
      >
        <Group justify="space-between">
          <Text fw={600} c={negative ? 'red.9' : undefined}>
            {p.totalLabel} <Credits value={p.total} /> · {he.wallet.allocated}{' '}
            <Credits value={allocated} /> · {he.groupPage.remainingToAllocate}{' '}
            <Credits value={remaining} />
            {negative && <span role="alert"> ▲ {he.groupPage.negativeRemainder}</span>}
          </Text>
          {!p.readOnly && (
            <Group>
              {p.actions}
              <Button data-testid="alloc-save" disabled={!dirty || negative} onClick={p.onSave}>
                {he.groupPage.review}
              </Button>
            </Group>
          )}
        </Group>
      </Box>
    </Box>
  );
}
