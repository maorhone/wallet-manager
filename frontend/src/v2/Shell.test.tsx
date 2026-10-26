// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import type { Me } from '../api/types';
import { areaStorageKey } from './navigation';
import { Shell } from './Shell';

const h = vi.hoisted(() => ({ me: null as unknown, overviewError: null as unknown }));
vi.mock('../api/queries', () => ({
  useMe: () => ({ data: h.me, isPending: false }),
  useBudgetOverview: () => ({ error: h.overviewError }),
  useDepartmentOverview: () => ({ error: null }),
}));
vi.mock('./pages/Personal', () => ({ default: () => <p>personal-page</p> }));
vi.mock('./pages/SectionBudget', () => ({ default: () => <p>group-page</p> }));
vi.mock('./pages/Department', () => ({ default: () => null }));
vi.mock('./pages/OrgAllocation', () => ({ default: () => <p>org-page</p> }));
vi.mock('./pages/Initiative', () => ({
  default: ({ organizationId }: { organizationId: number }) => <p>systems-org-{organizationId}</p>,
}));
vi.mock('./pages/Requests', () => ({ default: () => null }));
vi.mock('./pages/ServiceStatus', () => ({ default: () => null }));
vi.mock('./pages/Guide', () => ({ default: () => null }));

const me = {
  upn: 'rep@corp.example',
  display_name: 'Rep',
  roles: [
    { role: 'org_allocator', scope_kind: 'organization', scope_id: 4, scope_name: 'A' },
    { role: 'org_allocator', scope_kind: 'organization', scope_id: 9, scope_name: 'B' },
  ],
  scopes: [{ kind: 'self' }],
} as unknown as Me;

const Where = () => <i data-testid="where">{useLocation().pathname + useLocation().search}</i>;
const mount = (url: string) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/v2/*" element={<Shell />} />
      </Routes>
      <Where />
    </MemoryRouter>,
  );

describe('Shell (review-p5-r6 majors)', () => {
  beforeEach(() => {
    h.me = me;
    h.overviewError = null;
  });
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it('reopens a remembered inherited group', () => {
    localStorage.setItem(areaStorageKey(me.upn), '/v2/group/12');
    mount('/v2');
    expect(screen.getByText('group-page')).toBeTruthy();
  });

  it('falls back to personal only when the server denies the inherited group', () => {
    h.overviewError = new ApiError(403, { code: 'FORBIDDEN' } as never);
    localStorage.setItem(areaStorageKey(me.upn), '/v2/group/12');
    mount('/v2');
    expect(screen.getByText('personal-page')).toBeTruthy();
    expect(localStorage.getItem(areaStorageKey(me.upn))).toBe('/v2/personal');
  });

  it('keeps the selected organization when opening Systems and offers the selector there', () => {
    mount('/v2/organization?org=9');
    fireEvent.click(screen.getByRole('link', { name: 'מערכות באחריותנו' }));
    expect(screen.getByTestId('where').textContent).toBe('/v2/systems?org=9');
    expect(screen.getByText('systems-org-9')).toBeTruthy();
    expect(screen.getByLabelText('ארגון')).toBeTruthy();
  });
});
