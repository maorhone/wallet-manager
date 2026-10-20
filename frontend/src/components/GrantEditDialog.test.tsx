// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Grant } from '../api/types';
import { GrantEditDialog, type GrantEditMode } from './GrantEditDialog';

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

const grant = {
  id: 1,
  project_ref: 'P',
  amount: 50000,
  used: 1000,
  returned: 2000,
  status: 'active',
  expires_at: '2026-11-15T21:59:59Z', // 23:59:59 on the 15th in Asia/Jerusalem
} as unknown as Grant;

function open(
  mode: GrantEditMode,
  onConfirm = vi.fn().mockResolvedValue(undefined),
  tz = 'Asia/Jerusalem',
) {
  render(
    createElement(
      MantineProvider,
      null,
      createElement(GrantEditDialog, {
        grant,
        mode,
        timezone: tz,
        available: 10000,
        onClose: () => {},
        onConfirm,
      }),
    ),
  );
  return onConfirm;
}
const change = (id: string, value: string) =>
  fireEvent.change(screen.getByTestId(id), { target: { value } });
const confirm = () => fireEvent.click(screen.getByTestId('grant-edit-confirm'));

afterEach(cleanup);

describe('GrantEditDialog', () => {
  it('close preview is amount - used - returned and shows the pool before/after', () => {
    open('close');
    expect(screen.getByTestId('grant-edit').textContent).toContain('47,000');
    expect(screen.getByTestId('pool-before').textContent).toContain('10,000');
    expect(screen.getByTestId('pool-after').textContent).toContain('57,000');
  });

  it('increase shows old and new amount and the pool effect', () => {
    open('increase');
    change('grant-edit-amount', '51000');
    expect(screen.getByTestId('summary-before').textContent).toContain('50,000');
    expect(screen.getByTestId('summary-after').textContent).toContain('51,000');
    expect(screen.getByTestId('pool-after').textContent).toContain('9,000');
  });

  it('extend submits the end of the day in the application timezone', async () => {
    const onConfirm = open('extend');
    change('grant-edit-expiry', '2026-11-30');
    change('grant-edit-reason', 'why');
    confirm();
    await waitFor(() => expect(onConfirm).toHaveBeenCalled());
    // Asia/Jerusalem is UTC+2 in late November; the browser zone is irrelevant.
    expect(onConfirm.mock.calls[0][0]).toEqual({ expires_at: '2026-11-30T21:59:59.000Z' });
  });

  it('keeps the idempotency key across retries of the same payload, new key when it changes', async () => {
    const onConfirm = vi.fn().mockRejectedValue(new Error('network'));
    open('close', onConfirm);
    change('grant-edit-reason', 'a');
    confirm();
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByTestId('grant-edit-error')).toBeTruthy());
    confirm();
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(2));
    expect(onConfirm.mock.calls[1][2]).toBe(onConfirm.mock.calls[0][2]);
    change('grant-edit-reason', 'b');
    confirm();
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(3));
    expect(onConfirm.mock.calls[2][2]).not.toBe(onConfirm.mock.calls[0][2]);
  });
});
