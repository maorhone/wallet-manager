// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { MantineProvider } from '@mantine/core';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SectionBudget from './SectionBudget';

window.matchMedia ??= ((q: string) => ({
  matches: false,
  media: q,
  addEventListener() {},
  removeEventListener() {},
})) as unknown as typeof window.matchMedia;

const flags = vi.hoisted(() => ({ edit: true }));
vi.mock('../../api/queries', () => ({
  useBudgetOverview: () => ({
    isPending: false,
    data: {
      period: '2026-10',
      kpis: { total: 250000, allocated: 45000, used: 1000, remaining: 249000, metered_value: null },
      bar: { used: 1000, allocated_unused: 44000, unallocated: 205000, total: 250000 },
      previous_period: { period: '2026-09', used: 700 },
      rows: [
        { kind: 'employee', id: 'a@corp.example', name: 'Avi', total: 15000, used: 0, percent: 0 },
      ],
      actions: { edit_members: flags.edit },
    },
  }),
  useBlockSummary: () => ({
    isPending: false,
    data: {
      by_reason: [{ reason: 'RATE_LIMIT', sub: 'concurrency', count: 2 }],
      by_consumer: [],
      truncated: false,
    },
  }),
  useAttention: () => ({
    data: {
      requests: [{ id: 1, type: 'credits', status: 'submitted', requester_upn: 'a' }],
      shortage_risks: [],
      reallocation_opportunities: [],
    },
  }),
  useTransferPreview: () => ({ mutate: vi.fn() }),
  useTransfer: () => ({ mutate: vi.fn() }),
  useTransferStatus: () => ({ data: undefined }),
}));

describe('SectionBudget', () => {
  afterEach(cleanup);
  it('shows unavailable metered value and an attention item', () => {
    render(
      <MantineProvider>
        <MemoryRouter>
          <SectionBudget groupId="1" />
        </MemoryRouter>
      </MantineProvider>,
    );
    expect(screen.getByText('לא זמין')).toBeTruthy();
    expect(screen.getByText(/בקשה ממתינה/)).toBeTruthy();
  });

  // review-p5-r5 major: read-only users get no write controls; KPIs follow D-054
  it('hides allocation controls for viewers and shows unallocated / assigned-unused', () => {
    flags.edit = false;
    render(
      <MantineProvider>
        <MemoryRouter>
          <SectionBudget groupId="1" />
        </MemoryRouter>
      </MantineProvider>,
    );
    flags.edit = true;
    expect(screen.queryByRole('button', { name: 'העברה' })).toBeNull();
    expect(screen.getAllByText('טרם הוקצה').length).toBeGreaterThan(0);
    expect(screen.getAllByText('הוקצה ולא נוצל').length).toBeGreaterThan(0);
  });

  // review-p5-r10 major F1: D-058 period comparison and D-064 block reasons for section heads
  it('shows the previous period and the scoped block summary', () => {
    render(
      <MantineProvider>
        <MemoryRouter>
          <SectionBudget groupId="1" />
        </MemoryRouter>
      </MantineProvider>,
    );
    expect(screen.getByText(/השוואת תקופות/)).toBeTruthy();
    expect(screen.getByText('2026-09')).toBeTruthy();
    expect(screen.getByText('מגבלת קצב או מקביליות')).toBeTruthy();
  });
});
