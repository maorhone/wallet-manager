import { useQuery } from '@tanstack/react-query';
import { he } from '../../i18n/he';
import type { WriteResponse } from '../../api/types';

type Outcome = 'applied' | 'failed' | 'pending';

/** Overall synchronization state of a write: any failed entity fails it (D-071, SPEC §12.1). */
export function outcomeOf(r: WriteResponse): Outcome {
  if (r.entities.some((e) => e.sync_status === 'failed')) return 'failed';
  return r.entities.every((e) => e.sync_status === 'applied') ? 'applied' : 'pending';
}

/**
 * Shows whether a saved write is enforced by LiteLLM yet. While it is pending the same write is
 * replayed (same idempotency key, so it only reads the current state) until it settles.
 */
export function WriteOutcome({
  result,
  replay,
  done,
}: {
  result: WriteResponse;
  replay: () => Promise<WriteResponse>;
  done: string;
}) {
  const q = useQuery({
    queryKey: ['write-outcome', result.request_id],
    queryFn: replay,
    initialData: result,
    enabled: outcomeOf(result) === 'pending',
    refetchInterval: (query) =>
      outcomeOf(query.state.data ?? result) === 'pending' ? 3000 : false,
  });
  const state = outcomeOf(q.data ?? result);
  if (state === 'applied') return <p role="status">{done}</p>;
  return state === 'failed' ? (
    <p role="alert">{he.v2.writeOutcome.failed}</p>
  ) : (
    <p role="status">{he.v2.writeOutcome.pending}</p>
  );
}
