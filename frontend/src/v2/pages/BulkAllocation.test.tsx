// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react';
import { MantineProvider } from '@mantine/core';
import { describe, expect, it, vi } from 'vitest';
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
});
