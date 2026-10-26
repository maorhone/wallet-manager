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
    {
      id: number;
      name: string;
      budget: number;
      owner?: string;
      used?: number;
      funding: 'department' | 'organization';
    }
  >();
  // The managed inventory lists Org Systems only; /systems/mine says how each owned one is funded.
  for (const i of managed.data?.systems.rows ?? [])
    systems.set(i.id, { ...i, funding: 'organization' });
  for (const i of owned.data.items)
    systems.set(i.id, {
      ...i,
      owner: i.owner_upn ?? undefined,
      funding: i.funding ?? 'organization',
    });
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
      funding: undefined,
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
      funding: i.funding,
    })),
  ];
  if (!items.length) return <p className="v2-muted">{s.none}</p>;
  const current = items.find((i) => i.ref === open) ?? items[0];
  return (
    <>
      <p className="v2-muted">{s.readOnly}</p>
      <nav aria-label={s.title}>
        {[
          [s.groupInitiatives, (i: (typeof items)[number]) => i.kind === 'initiative'],
          [s.groupInternal, (i: (typeof items)[number]) => i.funding === 'department'],
          [s.groupOrg, (i: (typeof items)[number]) => i.funding === 'organization'],
        ].map(([title, pick]) => {
          const group = items.filter(pick as (i: (typeof items)[number]) => boolean);
          return group.length ? (
            <div key={title as string} role="group" aria-label={title as string}>
              <h2>{title as string}</h2>
              {group.map((i) => (
                <button
                  key={i.ref}
                  type="button"
                  aria-pressed={i.ref === current.ref}
                  onClick={() => setOpen(i.ref)}
                >
                  {i.name}
                </button>
              ))}
            </div>
          ) : null;
        })}
      </nav>
      <SystemDetail
        key={current.ref}
        subject={{
          kind: current.kind,
          id: current.id,
          name: current.name,
          budget: current.budget,
          owner: current.owner,
          funding: current.funding,
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
