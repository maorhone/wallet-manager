import { describe, expect, it } from 'vitest';
import type { AppNotification } from '../api/types';
import { notificationLink } from './NotificationsBell';

const n = (o: Partial<AppNotification>): AppNotification =>
  ({ id: 1, kind: 'x', scope_kind: null, scope_id: null, payload: {}, ...o }) as AppNotification;

describe('notificationLink', () => {
  it('sends employee usage alerts (group-scoped by the scheduler) to the wallet', () => {
    expect(notificationLink(n({ kind: 'employee_80', scope_kind: 'group', scope_id: 4 }))).toBe(
      '/wallet',
    );
    expect(notificationLink(n({ kind: 'employee_100', scope_kind: 'group', scope_id: 4 }))).toBe(
      '/wallet',
    );
  });
  it('sends group alerts to the group', () => {
    expect(notificationLink(n({ kind: 'group_80', scope_kind: 'group', scope_id: 4 }))).toBe(
      '/group/4/overview',
    );
  });
  it('sends pending placements to the department tab, or the platform page when unknown', () => {
    expect(
      notificationLink(n({ kind: 'pending_placement', scope_kind: 'department', scope_id: 7 })),
    ).toBe('/department/7/pending');
    expect(
      notificationLink(n({ kind: 'pending_placement', scope_kind: 'department', scope_id: null })),
    ).toBe('/platform');
  });
});
