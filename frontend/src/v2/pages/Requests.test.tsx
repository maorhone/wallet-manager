import { describe, expect, it } from 'vitest';
import { actionsFor, approvalBlocked, decisionKey, parseTpm } from './Requests';

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

describe('approvalBlocked', () => {
  it('needs an approved amount when the request carries none', () => {
    expect(approvalBlocked('approve', null, '')).toBe(true);
    expect(approvalBlocked('approve', null, '500')).toBe(false);
    expect(approvalBlocked('approve', 300, '')).toBe(false);
    expect(approvalBlocked('reject', null, '')).toBe(false);
  });
});

// review-p5-r12 major F2: a retry after a lost response keeps the key; a new decision renews it
describe('decisionKey', () => {
  it('reuses the key for the same decision and renews it for another', () => {
    const first = decisionKey(null, 'a');
    expect(decisionKey(first, 'a')).toBe(first);
    expect(decisionKey(first, 'b').key).not.toBe(first.key);
    expect(decisionKey(null, 'a').key).not.toBe(first.key); // renewed after a confirmed success
  });
});

// review-p5-r5 major: AI handlers supply per-model TPM limits
describe('parseTpm', () => {
  it('parses model=tpm pairs and drops invalid ones', () => {
    expect(parseTpm('gpt=1000, claude=2000,bad=x')).toEqual({ gpt: 1000, claude: 2000 });
    expect(parseTpm('')).toBeUndefined();
  });
});
