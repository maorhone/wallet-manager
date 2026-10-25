// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MantineProvider } from '@mantine/core';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TransferDrawer } from './TransferDrawer';

window.matchMedia ??= ((q: string) => ({
  matches: false,
  media: q,
  addEventListener() {},
  removeEventListener() {},
})) as unknown as typeof window.matchMedia;

const previewMutate = vi.fn();
const transferMutate = vi.fn();
vi.mock('../../api/queries', () => ({
  useTransferPreview: () => ({
    mutate: previewMutate,
    data: { source: { total: 100, after: 90 }, target: { total: 0, after: 10 } },
  }),
  useTransfer: () => ({ mutate: transferMutate }),
}));

const rows = [
  { kind: 'group' as const, id: '7', name: 'Group seven', total: 100, used: 0, percent: 0 },
  {
    kind: 'initiative' as const,
    id: '7',
    name: 'Initiative seven',
    total: 50,
    used: 0,
    percent: 0,
  },
];

function renderDrawer() {
  render(
    <MantineProvider>
      <TransferDrawer groupId={1} rows={rows} initialSource="group:7" onClose={() => {}} />
    </MantineProvider>,
  );
}

describe('TransferDrawer', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(cleanup);

  // review-p5 blocker 13: a group and an initiative may share a numeric id
  it('keeps the entity kind when ids collide', () => {
    renderDrawer();
    const [from, to] = screen.getAllByRole('combobox');
    fireEvent.change(from, { target: { value: 'initiative:7' } });
    fireEvent.change(to, { target: { value: 'group:7' } });
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '10' } });
    fireEvent.submit(from.closest('form')!);
    expect(previewMutate).toHaveBeenCalledWith({
      group_id: 1,
      source: { kind: 'initiative', id: '7' },
      target: { kind: 'group', id: '7' },
      amount: 10,
    });
  });

  it('confirms the previewed payload, not later edits', () => {
    renderDrawer();
    const [from, to] = screen.getAllByRole('combobox');
    fireEvent.change(from, { target: { value: 'group:7' } });
    fireEvent.change(to, { target: { value: 'initiative:7' } });
    const amount = screen.getByRole('spinbutton');
    fireEvent.change(amount, { target: { value: '100' } });
    fireEvent.submit(from.closest('form')!);
    fireEvent.change(amount, { target: { value: '1000' } });
    // edited inputs invalidate the preview: confirmation is no longer offered
    expect(screen.queryByText('אישור העברה')).toBeNull();
    expect(transferMutate).not.toHaveBeenCalled();
  });

  // review-p5-r2 blocker (F1): a second preview of the same payload keeps the Idempotency-Key
  it('reuses the idempotency key when the unchanged transfer is previewed again', () => {
    renderDrawer();
    const [from, to] = screen.getAllByRole('combobox');
    fireEvent.change(from, { target: { value: 'group:7' } });
    fireEvent.change(to, { target: { value: 'initiative:7' } });
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '10' } });
    const keys: string[] = [];
    for (let i = 0; i < 2; i++) {
      fireEvent.submit(from.closest('form')!);
      fireEvent.change(screen.getByRole('textbox'), { target: { value: 'why' } });
      fireEvent.click(screen.getByText('אישור העברה'));
      keys.push(transferMutate.mock.calls[i][0].key);
    }
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
  });
});
