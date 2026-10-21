import { describe, expect, it } from 'vitest';
import type { Scope } from './api/types';
import { reportPath, scopeFromPath, scopeKey } from './scopes';

const pmo: Scope = { kind: 'department', id: 5, name: 'D', role: 'pmo' };
const rep: Scope = { kind: 'department', id: 5, name: 'D', role: 'representative' };

describe('scopes', () => {
  it('PMO and representative of one department are distinct', () => {
    expect(scopeKey(pmo)).not.toBe(scopeKey(rep));
    expect(scopeFromPath('/pmo/5/pool', [rep, pmo])).toBe(pmo);
    expect(scopeFromPath('/department/5/overview', [pmo, rep])).toBe(rep);
  });

  it('reports retain the selected scope, including own and global reports', () => {
    const self: Scope = { kind: 'self' };
    const global: Scope = { kind: 'platform', role: 'viewer' };
    const group: Scope = { kind: 'group', id: 3, role: 'group_head' };
    const scopes = [self, pmo, rep, global, group];
    expect(scopeFromPath('/report/department/5/allocation', scopes)).toBe(rep);
    expect(scopeFromPath('/report/me/me/allocation', scopes)).toBe(self);
    expect(scopeFromPath('/report/global/all/allocation', scopes)).toBe(global);
    expect(scopeFromPath('/report/group/3/allocation', scopes)).toBe(group);
    expect(reportPath(rep)).toBe('/report/department/5/allocation');
    expect(reportPath(self)).toBe('/report/me/me/allocation');
    expect(reportPath(global)).toBe('/report/global/all/allocation');
    expect(reportPath(group)).toBe('/report/group/3/allocation');
  });

  it('does not offer member reports to viewers or allocation reports to PMO', () => {
    expect(reportPath(pmo)).toBeNull();
    expect(reportPath({ kind: 'group', id: 3, role: 'viewer' })).toBeNull();
    expect(scopeFromPath('/report/department/5/allocation', [pmo])).toBeUndefined();
  });
});
