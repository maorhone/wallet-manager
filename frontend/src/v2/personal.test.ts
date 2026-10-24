import { describe, expect, it } from 'vitest';
import { explainBlock } from './personal';

describe('explainBlock', () => {
  it('rate limit with positive balance suggests throughput, not credits', () => {
    const r = explainBlock({
      reason: 'RATE_LIMIT',
      sub: 'concurrency',
      action: 'REQUEST_THROUGHPUT',
    });
    expect(r.cta).toBe('throughput');
    expect(r.text).toContain('מקביליות');
  });
  it('exhausted credits suggests a credits request', () => {
    expect(
      explainBlock({ reason: 'CREDITS_EXHAUSTED', sub: null, action: 'REQUEST_CREDITS' }).cta,
    ).toBe('credits');
  });
  it('service errors offer no request', () => {
    expect(
      explainBlock({ reason: 'SERVICE_ERROR', sub: null, action: 'CONTACT_AI_TEAM' }).cta,
    ).toBeNull();
  });
});
