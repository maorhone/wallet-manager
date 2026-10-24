// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import OrgAllocation from './OrgAllocation';

const mutate = vi.fn();
vi.mock('../../api/queries', () => ({
  useOrgEnvelopes: () => ({
    isPending: false,
    data: {
      organization: { id: 1, name: 'O' },
      departments: {
        envelope: 1000,
        allocated: 600,
        rows: [{ id: 3, name: 'מחלקה א', budget: 600, previous_used: 50 }],
      },
      systems: {
        envelope: 200,
        allocated: 100,
        rows: [{ id: 9, name: 'מערכת ארגונית', budget: 100 }],
      },
    },
  }),
  useOrgAllocate: () => ({ mutate }),
  useCreateOrganization: () => ({ mutate: vi.fn() }),
}));

describe('OrgAllocation', () => {
  it('keeps two envelopes apart and allocates to a department and an org system', () => {
    render(<OrgAllocation organizationId={1} isAdmin />);
    expect(screen.getByText('מעטפת מחלקות')).toBeTruthy();
    expect(screen.getByText('מעטפת מערכות ארגוניות')).toBeTruthy();
    expect(screen.getByText('בהמשך')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('סיבת השינוי'), { target: { value: 'תכנון' } });
    fireEvent.change(screen.getByLabelText('הקצאה: מחלקה א'), { target: { value: '700' } });
    fireEvent.click(screen.getByRole('button', { name: 'שמירה' }));
    expect(mutate).toHaveBeenCalledWith({
      kind: 'department',
      id: 3,
      budget: 700,
      reason: 'תכנון',
    });
    fireEvent.click(screen.getByRole('tab', { name: 'מערכות ארגוניות' }));
    fireEvent.change(screen.getByLabelText('הקצאה: מערכת ארגונית'), { target: { value: '150' } });
    fireEvent.click(screen.getByRole('button', { name: 'שמירה' }));
    expect(mutate).toHaveBeenCalledWith({ kind: 'system', id: 9, budget: 150, reason: 'תכנון' });
    expect(screen.getByText('ארגון חדש')).toBeTruthy();
  });
});
