// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Grant, Member } from '../api/types';
import { ShareDialog } from './ShareDialog';

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
  id: 3,
  project_ref: 'PRJ',
  amount: 2000,
  unshared: 1000,
  status: 'active',
  expires_at: '2026-11-15T21:59:59Z', // 23:59:59 on the 15th in Asia/Jerusalem
} as unknown as Grant;
const member = (upn: string, role = 'נציג המחלקה') =>
  ({
    upn,
    display_name: upn,
    owner: { role, people: [] },
  }) as unknown as Member;
const members = [member('dana@x.example', 'נציג המחלקה'), member('avi@x.example')];

function open(onConfirm = vi.fn().mockResolvedValue(undefined)) {
  render(
    createElement(
      MantineProvider,
      null,
      createElement(ShareDialog, {
        grant,
        members,
        selfUpn: 'dana@x.example',
        taken: [],
        timezone: 'Asia/Jerusalem',
        onClose: () => {},
        onConfirm,
      }),
    ),
  );
  return onConfirm;
}
const change = (id: string, value: string) =>
  fireEvent.change(screen.getByTestId(id), { target: { value } });
const pick = (upn: string) => fireEvent.click(screen.getByTestId(`share-check-${upn}`));

afterEach(cleanup);

describe('ShareDialog', () => {
  it('remainder updates and blocks above grant', () => {
    open();
    expect(screen.getByTestId('share-remainder').textContent).toContain('1,000');
    pick('avi@x.example');
    change('share-amount-avi@x.example', '400');
    expect(screen.getByTestId('share-remainder').textContent).toContain('600');
    expect((screen.getByTestId('share-confirm') as HTMLButtonElement).disabled).toBe(true);
    change('share-reason', 'why');
    expect((screen.getByTestId('share-confirm') as HTMLButtonElement).disabled).toBe(false);
    change('share-amount-avi@x.example', '1500');
    expect(screen.getByTestId('share-remainder').textContent).toContain('-500');
    expect(screen.getByTestId('share-over')).toBeTruthy();
    expect((screen.getByTestId('share-confirm') as HTMLButtonElement).disabled).toBe(true);
  });

  it('expiry capped at grant expiry', async () => {
    const onConfirm = open();
    pick('avi@x.example');
    const expiry = screen.getByTestId('share-expiry-avi@x.example') as HTMLInputElement;
    expect(expiry.value).toBe('2026-11-15');
    expect(expiry.max).toBe('2026-11-15');
    change('share-amount-avi@x.example', '100');
    change('share-reason', 'why');
    change('share-expiry-avi@x.example', '2026-11-16');
    expect((screen.getByTestId('share-confirm') as HTMLButtonElement).disabled).toBe(true);
    change('share-expiry-avi@x.example', '2026-11-15');
    fireEvent.click(screen.getByTestId('share-confirm'));
    await waitFor(() => expect(onConfirm).toHaveBeenCalled());
    // The default day is the grant's own expiry instant, never later.
    expect(onConfirm.mock.calls[0][0]).toEqual([
      { upn: 'avi@x.example', amount: 100, expires_at: '2026-11-15T21:59:59.000Z' },
    ]);
  });

  it('own row disabled', () => {
    open();
    expect((screen.getByTestId('share-check-dana@x.example') as HTMLInputElement).disabled).toBe(
      true,
    );
    expect(screen.getByTestId('share-row-dana@x.example').textContent).toContain('נציג המחלקה');
  });
});
