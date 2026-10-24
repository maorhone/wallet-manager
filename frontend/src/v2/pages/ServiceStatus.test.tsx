// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import Guide from './Guide';
import ServiceStatus from './ServiceStatus';

vi.mock('../../api/queries', () => ({
  useServiceStatus: () => ({
    isPending: false,
    data: {
      state: 'unknown',
      taken_at: '2026-10-24T10:00:00Z',
      models: [
        { model: 'gpt-x', state: 'unknown', checked_at: '2026-10-24T09:00:00Z' },
        { model: 'claude-y', state: 'ok', checked_at: '2026-10-24T09:59:00Z' },
      ],
    },
  }),
}));

describe('ServiceStatus', () => {
  it('shows unknown for a stale model and the glossary', () => {
    render(<ServiceStatus />);
    expect(screen.getByText('gpt-x').closest('tr')?.textContent).toContain('לא ידוע');
    expect(screen.getByText('claude-y').closest('tr')?.textContent).toContain('תקין');
    expect(screen.getByText('CREDITS_EXHAUSTED')).toBeTruthy();
  });
});

describe('Guide', () => {
  it('switches role tabs and links to requests', () => {
    render(
      <MemoryRouter>
        <Guide />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole('tab', { name: 'בעלים של מערכת' }));
    expect(screen.getByRole('tabpanel').textContent).toContain('בעלים');
    expect(screen.getByRole('link', { name: 'למרכז הבקשות' }).getAttribute('href')).toBe(
      '/v2/requests',
    );
  });
});
