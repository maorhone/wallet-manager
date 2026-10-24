import { he } from '../i18n/he';

export type RequestKind = 'credits' | 'throughput';

export interface BlockView {
  at: string;
  model: string | null;
  reason: string;
  sub: string | null;
  action: string;
}

/** Plain-language explanation of a classified block and the request it suggests (D-061/D-062). */
export function explainBlock(b: Pick<BlockView, 'reason' | 'sub' | 'action'>): {
  text: string;
  cta: RequestKind | null;
} {
  const p = he.v2.personalPage;
  const reasons: Record<string, string> = p.reasons;
  const subs: Record<string, string> = p.subs;
  const text =
    b.reason === 'RATE_LIMIT'
      ? `${reasons.RATE_LIMIT} (${subs[b.sub ?? ''] ?? subs.other})`
      : (reasons[b.reason] ?? reasons.UNKNOWN);
  const cta =
    b.action === 'REQUEST_CREDITS'
      ? 'credits'
      : b.action === 'REQUEST_THROUGHPUT'
        ? 'throughput'
        : null;
  return { text, cta };
}
