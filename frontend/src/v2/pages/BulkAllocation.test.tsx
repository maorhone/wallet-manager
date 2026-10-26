// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MantineProvider } from '@mantine/core';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { BulkAllocation } from './BulkAllocation';

window.matchMedia ??= ((q: string) => ({
  matches: false,
  media: q,
  addEventListener() {},
  removeEventListener() {},
})) as unknown as typeof window.matchMedia;

const mutate = vi.fn();
vi.mock('../../api/queries', () => ({
  useBulkPreview: () => ({
    mutate,
    data: {
      rows: [{ upn: 'a@corp.example', before: 100, after: 500 }],
      remaining_allocatable: 7000,
      over_allocation_warning: false,
      exceeds_limit: false,
    },
  }),
  useBulkAllocation: () => ({ mutate: vi.fn() }),
}));

describe('BulkAllocation', () => {
  afterEach(cleanup);
  it('previews a uniform amount with one override and shows remaining', () => {
    render(
      <MantineProvider>
        <BulkAllocation
          groupId={1}
          rows={[
            { kind: 'employee', id: 'a@corp.example', name: 'Avi', total: 1, used: 0, percent: 0 },
          ]}
          onClose={() => {}}
        />
      </MantineProvider>,
    );
    fireEvent.change(screen.getByLabelText('סכום אחיד לכל החברים'), { target: { value: '500' } });
    fireEvent.change(screen.getByLabelText('חריגה לעובד אחד'), {
      target: { value: 'a@corp.example' },
    });
    fireEvent.change(screen.getByLabelText('סכום החריגה'), { target: { value: '900' } });
    fireEvent.submit(screen.getByLabelText('סכום אחיד לכל החברים').closest('form')!);
    expect(mutate).toHaveBeenCalledWith({
      all: true,
      amount: 500,
      overrides: { 'a@corp.example': 900 },
    });
    expect(screen.getByText(/נשאר להקצאה/)).toBeTruthy();
  });

  // review-p5-r4 blocker F1: a preview made for group 1 never confirms against group 2
  it('drops the preview when the group changes', () => {
    const rows = [
      {
        kind: 'employee' as const,
        id: 'a@corp.example',
        name: 'Avi',
        total: 1,
        used: 0,
        percent: 0,
      },
    ];
    const ui = (groupId: number) => (
      <MantineProvider>
        <BulkAllocation groupId={groupId} rows={rows} onClose={() => {}} />
      </MantineProvider>
    );
    const { rerender } = render(ui(1));
    fireEvent.change(screen.getByLabelText('סכום אחיד לכל החברים'), { target: { value: '500' } });
    fireEvent.submit(screen.getByLabelText('סכום אחיד לכל החברים').closest('form')!);
    expect(screen.queryByText(/נשאר להקצאה/)).toBeTruthy();
    rerender(ui(2));
    expect(screen.queryByText(/נשאר להקצאה/)).toBeNull();
  });
});
