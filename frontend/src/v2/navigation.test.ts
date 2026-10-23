// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import type { Me } from '../api/types';
import { areasFor, areaStorageKey, rememberedArea } from './navigation';
const employee = {
  upn: 'employee@corp.example',
  roles: [{ role: 'employee', scope_kind: 'self' }],
  scopes: [{ kind: 'self' }],
} as Me;
afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});
it('accumulates scopes and deduplicates multiple roles on one scope', () => {
  const me = {
    ...employee,
    scopes: [
      ...employee.scopes,
      { kind: 'group' as const, id: 7, role: 'group_head' },
      { kind: 'group' as const, id: 7, role: 'viewer' },
      { kind: 'department' as const, id: 2, role: 'viewer' },
    ],
  };
  const paths = areasFor(me).map((a) => a.path);
  expect(paths.filter((p) => p === '/v2/group/7')).toHaveLength(1);
  expect(paths).toContain('/v2/department/2');
  expect(paths).not.toContain('/v2/organization');
});
it('requires allocator role and matching scope, ownership grants no allocation', () => {
  const owner = {
    ...employee,
    roles: [{ role: 'system_owner', scope_kind: 'system', scope_id: 4 }],
  };
  expect(areasFor(owner).some((a) => a.path === '/v2/organization')).toBe(false);
  expect(areasFor(owner).some((a) => a.path === '/v2/systems')).toBe(true);
  const admin = { ...employee, roles: [{ role: 'platform_admin', scope_kind: 'global' }] };
  expect(areasFor(admin).some((a) => a.path === '/v2/organization')).toBe(true);
  expect(
    areasFor({
      ...admin,
      roles: [{ role: 'org_allocator', scope_kind: 'group', scope_id: 7 }],
    }).some((a) => a.path === '/v2/organization'),
  ).toBe(false);
});
it('falls back after actual permission removal and never trusts external paths', () => {
  const me = {
    ...employee,
    scopes: [...employee.scopes, { kind: 'group' as const, id: 7, role: 'group_head' }],
  };
  localStorage.setItem(areaStorageKey(me.upn), '/v2/group/7');
  expect(rememberedArea(me, areasFor(me))).toBe('/v2/group/7');
  expect(rememberedArea(employee, areasFor(employee))).toBe('/v2/personal');
  localStorage.setItem(areaStorageKey(me.upn), 'https://corp.example');
  expect(rememberedArea(me, areasFor(me))).toBe('/v2/personal');
});
it('works when browser storage is blocked', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  expect(rememberedArea(employee, areasFor(employee))).toBe('/v2/personal');
});
