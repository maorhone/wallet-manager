import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMyInitiatives, useMySystems, useOrgEnvelopes } from '../../api/queries';
import { ErrorState } from '../../components/ErrorState';
import { he } from '../../i18n/he';
import { SystemDetail } from './SystemDetail';

const s = he.v2.initPage;
type Tech = keyof typeof s.tech;

export default function Initiative({
  canMove,
  organizationId = 1,
}: {
  canMove: boolean;
  organizationId?: number;
}) {
  const list = useMyInitiatives();
  const owned = useMySystems();
  // Allocators manage every Org System of the selected organization, not only their own.
  const managed = useOrgEnvelopes(organizationId, canMove);
  const [open, setOpen] = useState<string | null>(null);
  if (list.isPending || owned.isPending || (canMove && managed.isPending))
    return <p role="status">{he.loading}</p>;
  if (list.error || !list.data) return <ErrorState error={list.error} onRetry={list.refetch} />;
  if (owned.error || !owned.data) return <ErrorState error={owned.error} onRetry={owned.refetch} />;
  // An unavailable inventory is not an empty one: say so and offer a retry.
  if (canMove && (managed.error || !managed.data))
    return <ErrorState error={managed.error} onRetry={managed.refetch} />;
  // Initiatives and systems have independent ids: the identity is kind + id.
  const systems = new Map<
    number,
    { id: number; name: string; budget: number; owner?: string; used?: number }
  >();
  for (const i of managed.data?.systems.rows ?? []) systems.set(i.id, i);
  for (const i of owned.data.items) systems.set(i.id, { ...i, owner: i.owner_upn ?? undefined });
  // Allocators also manage initiatives they do not own; the owner list adds the tech status.
  const initiatives = new Map<
    number,
    { id: number; name: string; budget: number; owner?: string; used?: number; tech?: Tech }
  >();
  for (const i of managed.data?.initiatives ?? [])
    initiatives.set(i.id, { ...i, owner: i.owner_upn ?? undefined });
  for (const i of list.data.items)
    initiatives.set(i.id, { ...i, owner: i.owner_upn, tech: i.tech_status });
  const items = [
    ...[...initiatives.values()].map((i) => ({
      ref: `initiative:${i.id}`,
      kind: 'initiative' as const,
      id: i.id,
      name: i.name,
      budget: i.budget,
      owner: i.owner,
      used: i.used,
      tech: i.tech,
    })),
    ...[...systems.values()].map((i) => ({
      ref: `system:${i.id}`,
      kind: 'system' as const,
      id: i.id,
      name: i.name,
      budget: i.budget,
      owner: i.owner,
      used: i.used,
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
          used: current.used,
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
