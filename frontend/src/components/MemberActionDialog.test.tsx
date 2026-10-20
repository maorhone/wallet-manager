// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { createElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemberActionDialog, type MoveTarget } from './MemberActionDialog';

// Mantine needs these in jsdom.
window.matchMedia ??= ((q: string) => ({
  matches: false,
  media: q,
  addEventListener() {},
  removeEventListener() {},
})) as unknown as typeof window.matchMedia;
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

const UPN = 'dani@corp.example';
const targets: MoveTarget[] = [
  { group_id: 2, name: 'Group A2', department_id: 1, department_name: 'Department D1' },
  { group_id: 3, name: 'Group C1', department_id: 2, department_name: 'Department D2' },
];

function open(action: 'kick' | 'restore' | 'move' | 'delete', onConfirm = vi.fn()) {
  render(
    createElement(
      MantineProvider,
      null,
      createElement(MemberActionDialog, {
        opened: true,
        action,
        member: { upn: UPN, display_name: 'Dani Cohen' },
        targets,
        onClose: () => {},
        onConfirm,
      }),
    ),
  );
  return onConfirm;
}

const confirmBtn = () => screen.getByTestId('confirm-action') as HTMLButtonElement;
const type = (id: string, value: string) =>
  fireEvent.change(screen.getByTestId(id), { target: { value } });

afterEach(cleanup);

describe('MemberActionDialog', () => {
  it('reason required', () => {
    const onConfirm = open('kick');
    expect(confirmBtn().disabled).toBe(true);
    type('action-reason', '   ');
    expect(confirmBtn().disabled).toBe(true);
    type('action-reason', 'עזב את הפרויקט');
    expect(confirmBtn().disabled).toBe(false);
    fireEvent.click(confirmBtn());
    expect(onConfirm).toHaveBeenCalledWith('עזב את הפרויקט', expect.any(String), undefined);
  });

  it('delete confirm disabled until exact upn', () => {
    open('delete');
    type('action-reason', 'סגירת משתמש');
    expect(confirmBtn().disabled).toBe(true);
    type('confirm-upn', 'dani@corp.exampl');
    expect(confirmBtn().disabled).toBe(true);
    type('confirm-upn', 'DANI@corp.example');
    expect(confirmBtn().disabled).toBe(true);
    type('confirm-upn', UPN);
    expect(confirmBtn().disabled).toBe(false);
  });

  it('move lists targets', () => {
    const onConfirm = open('move');
    type('action-reason', 'מעבר צוות');
    expect(confirmBtn().disabled).toBe(true); // no target chosen yet
    fireEvent.click(screen.getByTestId('move-target'));
    expect(screen.getByText(/Group A2/)).toBeTruthy();
    expect(screen.getByText(/Group C1/)).toBeTruthy();
    fireEvent.click(screen.getByText(/Group C1/));
    expect(confirmBtn().disabled).toBe(false);
    fireEvent.click(confirmBtn());
    expect(onConfirm).toHaveBeenCalledWith('מעבר צוות', expect.any(String), { to_group_id: 3 });
  });
});
