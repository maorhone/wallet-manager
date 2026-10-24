import { describe, expect, it } from 'vitest';
import { actionsFor } from './Requests';

describe('actionsFor', () => {
  it('lets heads only forward throughput, and the AI team apply or reject', () => {
    expect(
      actionsFor({ type: 'throughput', status: 'submitted', handler_role: 'group_head' }),
    ).toEqual(['forward']);
    expect(
      actionsFor({ type: 'throughput', status: 'forwarded', handler_role: 'department_rep' }),
    ).toEqual(['forward']);
    expect(
      actionsFor({ type: 'throughput', status: 'forwarded', handler_role: 'ai_team' }),
    ).toEqual(['apply', 'reject']);
  });
  it('approves or rejects credits and initiatives, and nothing once decided', () => {
    expect(
      actionsFor({ type: 'credits', status: 'submitted', handler_role: 'group_head' }),
    ).toEqual(['approve', 'reject']);
    expect(
      actionsFor({ type: 'initiative', status: 'applied', handler_role: 'group_head' }),
    ).toEqual([]);
  });
});
