// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react';
import { MantineProvider } from '@mantine/core';
import { describe, expect, it, vi } from 'vitest';
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
      kpis: { total: 1000, allocated: 600, used: 100, remaining: 900 },
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
  useHierarchyChange: () => ({ mutate, reset: vi.fn() }),
}));

describe('Department', () => {
  it('compares sections, keeps org systems separate and adds a group above', () => {
    render(
      <MantineProvider>
        <Department departmentId="1" />
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
});
