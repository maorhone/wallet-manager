// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AllocationReport, ModelAllocation } from '../../api/types';
import { credits } from '../../format';
import { he } from '../../i18n/he';
import Allocation from './Allocation';

window.matchMedia ??= ((q: string) => ({
  matches: false,
  media: q,
  addEventListener() {},
  removeEventListener() {},
})) as unknown as typeof window.matchMedia;

const models: ModelAllocation[] = [
  { model: 'overspent', allocation: 100, spend: 125, pct: 1.25 },
  { model: 'at-cap', allocation: 100, spend: 100, pct: 1 },
  { model: 'under-cap', allocation: 100, spend: 75, pct: 0.75 },
  { model: 'zero-cap', allocation: 0, spend: 5, pct: null },
  { model: 'uncapped', allocation: null, spend: 125, pct: null },
];
const values = {
  total: 1000,
  allocated: 500,
  spend: 430,
  remaining: 570,
  pct: 0.43,
  state: 'healthy' as const,
};
const report: AllocationReport = {
  ...values,
  level: 'group',
  id: 1,
  name: 'Group',
  period: '2026-10',
  spend_taken_at: null,
  models,
  rows: [
    {
      ...values,
      id: 'member@corp.example',
      name: 'Member',
      kind: 'employee',
      base: null,
      grants: null,
      base_cap: 1000,
      shares_total: 0,
      model_caps: null,
      models,
    },
  ],
};

vi.mock('../../api/queries', () => ({
  useAllocationReport: () => ({ isPending: false, error: null, data: report }),
  useMe: () => ({ data: { timezone: 'Asia/Jerusalem' } }),
}));

afterEach(cleanup);

describe('Allocation model remaining', () => {
  it.each([
    ['report', 0],
    ['child', 1],
  ] as const)('shows signed remaining in the %s model table', (_location, index) => {
    render(
      <MantineProvider>
        <Allocation level="group" id="1" period="2026-10" />
      </MantineProvider>,
    );
    const table = within(screen.getAllByTestId('model-table')[index]);
    for (const [model, remaining] of [
      ['overspent', credits(-25)],
      ['at-cap', credits(0)],
      ['under-cap', credits(25)],
      ['zero-cap', credits(-5)],
      ['uncapped', '–'],
    ]) {
      const cells = within(table.getByRole('row', { name: new RegExp(model) })).getAllByRole(
        'cell',
      );
      expect(cells[3].textContent).toBe(remaining);
      if (model === 'uncapped') expect(cells[1].textContent).toBe(he.wallet.noCap);
    }
  });
});
