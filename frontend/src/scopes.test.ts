import { describe, expect, it } from 'vitest';
import type { Scope } from './api/types';
import { scopeFromPath, scopeKey } from './scopes';

const pmo: Scope = { kind: 'department', id: 5, name: 'D', role: 'pmo' };
const rep: Scope = { kind: 'department', id: 5, name: 'D', role: 'representative' };

describe('scopes', () => {
  it('PMO and representative of one department are distinct', () => {
    expect(scopeKey(pmo)).not.toBe(scopeKey(rep));
    expect(scopeFromPath('/pmo/5/pool', [rep, pmo])).toBe(pmo);
    expect(scopeFromPath('/department/5/overview', [pmo, rep])).toBe(rep);
  });
});
