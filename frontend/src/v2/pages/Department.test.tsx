// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MantineProvider } from '@mantine/core';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Department from './Department';

window.matchMedia ??= ((q: string) => ({
  matches: false,
  media: q,
  addEventListener() {},
  removeEventListener() {},
})) as unknown as typeof window.matchMedia;

const mutate = vi.fn();
vi.mock('../../api/queries', () => ({
  useDepartmentOverview: () => ({
    isPending: false,
    data: {
      period: '2026-10',
      department: 'D',
      kpis: { total: 1000, allocated: 600, used: 100, remaining: 900, metered_value: 42 },
      bar: { used: 100, allocated_unused: 500, unallocated: 400, total: 1000 },
      previous_period: { period: '2026-09', used: 50 },
      sections: [
        {
          id: 1,
          name: 'מדור א',
          total: 600,
          used: 100,
          percent: 0.16,
          members: 4,
          used_per_member: 25,
          previous_used: 50,
        },
      ],
      tree: [{ id: 1, name: 'מדור א', parent_group_id: null }],
      internal_systems: [{ id: 7, name: 'פנימית', budget: 10, used: 1 }],
      org_systems: [{ id: 8, name: 'ארגונית', budget: 20, used: 2 }],
      actions: { edit_hierarchy: true },
    },
  }),
  useBlockSummary: () => ({
    isPending: false,
    data: {
      by_reason: [{ reason: 'SERVICE_ERROR', sub: null, count: 3 }],
      by_consumer: [],
      truncated: false,
    },
  }),
  useHierarchyChange: () => ({ mutate, reset: vi.fn() }),
}));

describe('Department', () => {
  afterEach(cleanup);
  it('compares sections, keeps org systems separate and adds a group above', () => {
    render(
      <MantineProvider>
        <MemoryRouter>
          <Department departmentId="1" />
        </MemoryRouter>
      </MantineProvider>,
    );
    expect(screen.getByText('השוואת מדורים')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'הוסף קבוצה מעל: מדור א' }));
    fireEvent.change(screen.getByLabelText('שם הקבוצה החדשה'), { target: { value: 'חדשה' } });
    fireEvent.change(screen.getByLabelText('סיבת השינוי'), { target: { value: 'ארגון' } });
    fireEvent.submit(screen.getByLabelText('שם הקבוצה החדשה').closest('form')!);
    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({ change: { kind: 'above', groupId: 1, name: 'חדשה' } }),
    );
    fireEvent.click(screen.getByRole('tab', { name: 'מערכות באחריותנו' }));
    expect(screen.getByText('פנימית')).toBeTruthy();
    expect(screen.getByText('ארגונית')).toBeTruthy();
    expect(screen.getByText('מערכות ארגוניות אינן נכללות בסכומי המחלקה.')).toBeTruthy();
  });

  // review-p5-r10 major F1: metered usage and block reasons are shown to the department head
  it('shows the metered value and the scoped block summary', () => {
    render(
      <MantineProvider>
        <MemoryRouter>
          <Department departmentId="1" />
        </MemoryRouter>
      </MantineProvider>,
    );
    expect(screen.getByText(/שווי שימוש מחושב/)).toBeTruthy();
    expect(screen.getByText('תקלת שירות')).toBeTruthy();
  });

  // review-p5-r10 major F1: an edited resubmission after a lost response gets a new key
  it('reuses the key for an unchanged retry and renews it when the name was edited', () => {
    mutate.mockClear();
    render(
      <MantineProvider>
        <MemoryRouter>
          <Department departmentId="1" />
        </MemoryRouter>
      </MantineProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'הוסף קבוצה מעל: מדור א' }));
    const name = screen.getByLabelText('שם הקבוצה החדשה');
    fireEvent.change(name, { target: { value: 'א' } });
    fireEvent.change(screen.getByLabelText('סיבת השינוי'), { target: { value: 'ארגון' } });
    fireEvent.submit(name.closest('form')!);
    fireEvent.submit(name.closest('form')!);
    fireEvent.change(name, { target: { value: 'ב' } });
    fireEvent.submit(name.closest('form')!);
    const keys = mutate.mock.calls.map((c) => c[0].key);
    expect(keys[0]).toBe(keys[1]);
    expect(keys[2]).not.toBe(keys[0]);
  });
});
