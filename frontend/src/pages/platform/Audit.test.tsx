// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import Audit from './Audit';

window.matchMedia ??= ((media: string) => ({
  matches: false,
  media,
  addEventListener() {},
  removeEventListener() {},
})) as unknown as typeof window.matchMedia;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('builds the query from the filters', async () => {
  const fetch = vi.fn().mockImplementation(
    async () =>
      new Response(
        JSON.stringify([
          {
            id: 1,
            at: '2026-10-20T10:00:00Z',
            actor_upn: 'admin+audit@corp.example',
            actor_role: 'platform_admin',
            entity: 'policy',
            entity_id: 'concurrency',
            before: { max: 8 },
            after: { max: 6 },
            reason: 'Policy review',
            request_id: null,
            litellm_calls: [],
          },
        ]),
      ),
  );
  vi.stubGlobal('fetch', fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MantineProvider>
        <Audit />
      </MantineProvider>
    </QueryClientProvider>,
  );
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('ישות'), { target: { value: ' policy ' } });
  fireEvent.change(screen.getByLabelText('מזהה ישות'), { target: { value: ' concurrency ' } });
  fireEvent.change(screen.getByLabelText('מבצע'), {
    target: { value: ' admin+audit@corp.example ' },
  });
  fireEvent.change(screen.getByLabelText('מתאריך'), { target: { value: '2026-10-01T09:00' } });
  fireEvent.change(screen.getByLabelText('עד תאריך'), { target: { value: '2026-10-31T18:00' } });
  fireEvent.click(screen.getByRole('button', { name: 'חיפוש' }));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  const url = new URL(fetch.mock.calls[0][0], 'http://localhost');
  expect(url.pathname).toBe('/audit');
  expect(Object.fromEntries(url.searchParams)).toEqual({
    entity: 'policy',
    id: 'concurrency',
    actor: 'admin+audit@corp.example',
    from: new Date('2026-10-01T09:00').toISOString(),
    to: new Date('2026-10-31T18:00').toISOString(),
  });
  await screen.findByTestId('history-timeline');
  expect(screen.getByTestId('history-timeline').textContent).toContain('Policy review');
  expect(screen.getByTestId('history-timeline').textContent).toContain('8 → 6');

  // An empty search searches everything, without empty query parameters.
  for (const label of ['ישות', 'מזהה ישות', 'מבצע', 'מתאריך', 'עד תאריך'])
    fireEvent.change(screen.getByLabelText(label), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: 'חיפוש' }));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(fetch.mock.calls[1][0]).toBe('/audit');
  client.clear();
});
