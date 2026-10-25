// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RequestForm } from './RequestForm';

const mutate = vi.fn();
vi.mock('../../api/queries', () => ({
  useSubmitRequest: () => ({ mutate, isPending: false, isSuccess: false }),
}));

describe('RequestForm', () => {
  afterEach(cleanup);
  // review-p5-r2 blocker (F1): a change request for an initiative submits its top-level target
  it('submits the selected initiative as the request target', () => {
    render(
      <MemoryRouter
        initialEntries={['/v2/requests?target_kind=initiative&target_id=7&target_name=Pilot']}
      >
        <RequestForm />
      </MemoryRouter>,
    );
    expect(screen.getByText('Pilot')).toBeTruthy();
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '100' } });
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'more' } });
    fireEvent.submit(screen.getByRole('textbox').closest('form')!);
    expect(mutate.mock.calls[0][0]).toMatchObject({
      type: 'credits',
      amount: 100,
      target: { kind: 'initiative', id: 7 },
    });
  });
});
