import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMyInitiatives, useMySystems } from '../../api/queries';
import { ErrorState } from '../../components/ErrorState';
import { he } from '../../i18n/he';
import { SystemDetail } from './SystemDetail';

const s = he.v2.initPage;

export default function Initiative({ canMove }: { canMove: boolean }) {
  const list = useMyInitiatives();
  const owned = useMySystems();
  const [open, setOpen] = useState<string | null>(null);
  if (list.isPending || owned.isPending) return <p role="status">{he.loading}</p>;
  if (list.error || !list.data) return <ErrorState error={list.error} onRetry={list.refetch} />;
  if (owned.error || !owned.data) return <ErrorState error={owned.error} onRetry={owned.refetch} />;
  // Initiatives and systems have independent ids: the identity is kind + id.
  const items = [
    ...list.data.items.map((i) => ({
      ref: `initiative:${i.id}`,
      kind: 'initiative' as const,
      id: i.id,
      name: i.name,
      budget: i.budget,
      owner: i.owner_upn,
      tech: i.tech_status,
    })),
    ...owned.data.items.map((i) => ({
      ref: `system:${i.id}`,
      kind: 'system' as const,
      id: i.id,
      name: i.name,
      budget: i.budget,
      owner: i.owner_upn ?? undefined,
      tech: undefined,
    })),
  ];
  if (!items.length) return <p className="v2-muted">{s.none}</p>;
  const current = items.find((i) => i.ref === open) ?? items[0];
  return (
    <>
      <p className="v2-muted">{s.readOnly}</p>
      <nav aria-label={s.title}>
        {items.map((i) => (
          <button
            key={i.ref}
            type="button"
            aria-pressed={i.ref === current.ref}
            onClick={() => setOpen(i.ref)}
          >
            {i.name}
          </button>
        ))}
      </nav>
      <SystemDetail
        key={current.ref}
        subject={{
          kind: current.kind,
          id: current.id,
          name: current.name,
          budget: current.budget,
          owner: current.owner,
          tech: current.tech,
        }}
        canMove={canMove}
      />
      <Link
        to={`/v2/requests?${new URLSearchParams({
          target_kind: current.kind,
          target_id: String(current.id),
          target_name: current.name,
        })}`}
      >
        {s.change}
      </Link>
    </>
  );
}
