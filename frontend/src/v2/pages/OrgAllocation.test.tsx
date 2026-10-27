// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { he } from '../../i18n/he';
import OrgAllocation from './OrgAllocation';

const mutate = vi.fn();
let allocateError: Error | null = null;
vi.mock('../../api/queries', () => ({
  useOrgEnvelopes: () => ({
    isPending: false,
    data: {
      organization: { id: 1, name: 'O' },
      departments: {
        envelope: 1000,
        allocated: 600,
        rows: [{ id: 3, name: 'מחלקה א', budget: 600, previous_used: 50, budget_period: '7d' }],
      },
      systems: {
        envelope: 200,
        allocated: 100,
        rows: [{ id: 3, name: 'מערכת ארגונית', budget: 100 }],
      },
    },
  }),
  useOrgAllocate: () => ({ mutate, error: allocateError }),
  useCreateOrganization: () => ({ mutate: vi.fn() }),
}));

describe('OrgAllocation', () => {
  afterEach(cleanup);
  it('keeps two envelopes apart and allocates to a department and an org system', () => {
    render(<OrgAllocation organizationId={1} isAdmin />);
    expect(screen.getByText('מעטפת מחלקות')).toBeTruthy();
    expect(screen.getByText('מעטפת מערכות ארגוניות')).toBeTruthy();
    expect(screen.getByText('בהמשך')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('סיבת השינוי'), { target: { value: 'תכנון' } });
    fireEvent.change(screen.getByLabelText('הקצאה: מחלקה א'), { target: { value: '700' } });
    fireEvent.click(screen.getByRole('button', { name: 'שמירה' }));
    expect(mutate).toHaveBeenCalledWith(
      {
        kind: 'department',
        id: 3,
        budget: 700,
        budgetPeriod: '7d',
        reason: 'תכנון',
      },
      expect.anything(),
    );
    fireEvent.click(screen.getByRole('tab', { name: 'מערכות ארגוניות' }));
    fireEvent.change(screen.getByLabelText('הקצאה: מערכת ארגונית'), { target: { value: '150' } });
    fireEvent.click(screen.getByRole('button', { name: 'שמירה' }));
    expect(mutate).toHaveBeenCalledWith(
      { kind: 'system', id: 3, budget: 150, reason: 'תכנון' },
      expect.anything(),
    );
    expect(screen.getByText('ארגון חדש')).toBeTruthy();
  });

  it('does not carry a department draft into a system with the same id', () => {
    render(<OrgAllocation organizationId={1} isAdmin />);
    fireEvent.change(screen.getByLabelText('הקצאה: מחלקה א'), { target: { value: '999' } });
    fireEvent.click(screen.getByRole('tab', { name: 'מערכות ארגוניות' }));
    expect((screen.getByLabelText('הקצאה: מערכת ארגונית') as HTMLInputElement).value).toBe('100');
  });

  // review-p5-r12 major F1: a pending write keeps its status after switching tabs and back
  it('keeps the synchronization status of a saved allocation across tab changes', () => {
    const result = {
      request_id: 'r1',
      status: 'pending',
      entities: [{ entity: 'department', id: '3', sync_status: 'pending', values: {} }],
    };
    mutate.mockImplementationOnce((_v, opts) =>
      opts.onSuccess({ result, replay: () => Promise.resolve(result) }),
    );
    render(
      <QueryClientProvider client={new QueryClient()}>
        <OrgAllocation organizationId={1} isAdmin />
      </QueryClientProvider>,
    );
    fireEvent.change(screen.getByLabelText('סיבת השינוי'), { target: { value: 'תכנון' } });
    fireEvent.change(screen.getByLabelText('הקצאה: מחלקה א'), { target: { value: '700' } });
    fireEvent.click(screen.getByRole('button', { name: 'שמירה' }));
    expect(screen.getByText(he.v2.writeOutcome.pending)).toBeTruthy();
    fireEvent.click(screen.getByRole('tab', { name: 'מערכות ארגוניות' }));
    expect(screen.queryByText(he.v2.writeOutcome.pending)).toBeNull(); // the other tab's table
    fireEvent.click(screen.getByRole('tab', { name: 'מחלקות' }));
    expect(screen.getByText(he.v2.writeOutcome.pending)).toBeTruthy();
  });

  // review-p5-r13 major F1: the response may arrive after the table was unmounted by a tab switch
  it('records an allocation outcome that arrives after switching tabs', async () => {
    const result = {
      request_id: 'r2',
      status: 'pending',
      entities: [{ entity: 'department', id: '3', sync_status: 'pending', values: {} }],
    };
    let done: (() => void) | undefined;
    mutate.mockImplementationOnce((_v, opts) => {
      done = () => opts.onSuccess({ result, replay: () => Promise.resolve(result) });
    });
    render(
      <QueryClientProvider client={new QueryClient()}>
        <OrgAllocation organizationId={1} isAdmin />
      </QueryClientProvider>,
    );
    fireEvent.change(screen.getByLabelText('סיבת השינוי'), { target: { value: 'תכנון' } });
    fireEvent.change(screen.getByLabelText('הקצאה: מחלקה א'), { target: { value: '700' } });
    fireEvent.click(screen.getByRole('button', { name: 'שמירה' }));
    fireEvent.click(screen.getByRole('tab', { name: 'מערכות ארגוניות' }));
    await act(async () => done?.());
    fireEvent.click(screen.getByRole('tab', { name: 'מחלקות' }));
    expect(screen.getByText(he.v2.writeOutcome.pending)).toBeTruthy();
  });

  it('shows a rejected allocation', () => {
    allocateError = new Error('exceeds envelope');
    render(<OrgAllocation organizationId={1} isAdmin />);
    expect(screen.getByRole('alert').textContent).toContain('exceeds envelope');
    allocateError = null;
  });
});
