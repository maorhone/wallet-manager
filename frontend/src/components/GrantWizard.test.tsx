// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { createElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { zonedInstant } from '../format';
import { GrantWizard } from './GrantWizard';

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

const groups = [
  {
    id: 2,
    name: 'Group A1',
    base: 100,
    total: 100,
    used: 10,
    percent: 0.1,
    state: 'healthy' as const,
  },
];

function open() {
  render(
    createElement(
      MantineProvider,
      null,
      createElement(GrantWizard, {
        opened: true,
        available: 5000,
        groups,
        models: [],
        timezone: 'Asia/Jerusalem',
        onClose: () => {},
        onSubmit: vi.fn(),
      }),
    ),
  );
}

const type = (id: string, value: string) =>
  fireEvent.change(screen.getByTestId(id), { target: { value } });
const next = () => screen.getByTestId('wizard-next') as HTMLButtonElement;

function toAmount() {
  type('wizard-project', 'PRJ-1');
  type('wizard-purpose', 'Pilot');
  fireEvent.click(next());
  type('wizard-group', '2');
  fireEvent.click(next());
}

afterEach(cleanup);

describe('GrantWizard', () => {
  it('blocks amount above pool', () => {
    open();
    toAmount();
    type('wizard-amount', '5001');
    expect(next().disabled).toBe(true);
    expect(screen.getByTestId('wizard-amount-error')).toBeTruthy();
    type('wizard-amount', '5000');
    expect(next().disabled).toBe(false);
  });

  it('expiry before start invalid', () => {
    open();
    toAmount();
    type('wizard-amount', '100');
    fireEvent.click(next());
    type('wizard-start', '2099-05-10');
    type('wizard-expiry', '2099-05-09');
    expect(next().disabled).toBe(true);
    expect(screen.getByTestId('wizard-dates-error')).toBeTruthy();
    type('wizard-expiry', '2099-05-11');
    expect(next().disabled).toBe(false);
  });

  it('converts calendar days in the application timezone, not the browser one', () => {
    // Asia/Jerusalem is UTC+3 on 20/10/2026 (DST ended 25/10).
    expect(zonedInstant('2026-10-20', '23:59:59', 'Asia/Jerusalem')).toBe(
      '2026-10-20T20:59:59.000Z',
    );
    expect(zonedInstant('2026-10-20', '00:00:00', 'Asia/Jerusalem')).toBe(
      '2026-10-19T21:00:00.000Z',
    );
    expect(zonedInstant('2026-12-01', '00:00:00', 'Asia/Jerusalem')).toBe(
      '2026-11-30T22:00:00.000Z',
    );
  });
});
