import { describe, expect, it } from 'vitest';
import { ApiError } from '../api/client';
import { ERROR_CODES, errorMessage } from './errors';

const env = { requested: 620000, available: 600000, parent_total: 600000 };

describe('errorMessage', () => {
  it('maps every code with numbers', () => {
    for (const code of ERROR_CODES) {
      const msg = errorMessage(
        new ApiError(422, {
          code,
          message: 'x',
          ...env,
          details: { min: 1, max: 8, owner_role: 'מנהל המחלקה' },
        }),
      );
      expect(msg, code).toMatch(/[א-ת]/);
      expect(msg, code).not.toContain('{');
    }
    expect(errorMessage(new ApiError(422, { code: 'PARENT_OVERALLOCATED', ...env }))).toBe(
      'חילקת 620,000 מתוך 600,000. יש להפחית 20,000 לפני שמירה',
    );
    const limit = errorMessage(
      new ApiError(422, {
        code: 'LIMIT_OUT_OF_POLICY',
        requested: 12,
        details: { min: 1, max: 8 },
      }),
    );
    expect(limit).toContain('12');
    expect(limit).toContain('1–8');
  });

  it('covers every §11.2 code, FORBIDDEN, CSRF_FAILED, INVALID_REQUEST', () => {
    for (const c of [
      'PARENT_OVERALLOCATED',
      'PMO_POOL_INSUFFICIENT',
      'SHARE_EXCEEDS_GRANT',
      'SHARE_EXPIRY_AFTER_GRANT',
      'LIMIT_OUT_OF_POLICY',
      'SELF_ALLOCATION_FORBIDDEN',
      'SCOPE_FORBIDDEN',
      'GRANT_BELOW_SHARED',
      'FORBIDDEN',
      'CSRF_FAILED',
      'INVALID_REQUEST',
    ]) {
      expect(ERROR_CODES).toContain(c);
    }
  });

  it('handles network failures and unknown codes', () => {
    expect(errorMessage(new TypeError('Failed to fetch'))).toMatch(/[א-ת]/);
    expect(errorMessage(new ApiError(500, { code: 'WAT' }))).toMatch(/[א-ת]/);
  });
});
