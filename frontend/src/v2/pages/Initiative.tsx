import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMyInitiatives } from '../../api/queries';
import { ErrorState } from '../../components/ErrorState';
import { he } from '../../i18n/he';
import { SystemDetail } from './SystemDetail';

const s = he.v2.initPage;

export default function Initiative({ canMove }: { canMove: boolean }) {
  const list = useMyInitiatives();
  const [open, setOpen] = useState<number | null>(null);
  if (list.isPending) return <p role="status">{he.loading}</p>;
  if (list.error || !list.data) return <ErrorState error={list.error} onRetry={list.refetch} />;
  const items = list.data.items;
  if (!items.length) return <p className="v2-muted">{s.none}</p>;
  const current = items.find((i) => i.id === open) ?? items[0];
  return (
    <>
      <p className="v2-muted">{s.readOnly}</p>
      <nav aria-label={s.title}>
        {items.map((i) => (
          <button
            key={i.id}
            type="button"
            aria-pressed={i.id === current.id}
            onClick={() => setOpen(i.id)}
          >
            {i.name}
          </button>
        ))}
      </nav>
      <SystemDetail
        subject={{
          kind: 'initiative',
          id: current.id,
          name: current.name,
          budget: current.budget,
          owner: current.owner_upn,
          tech: current.tech_status,
        }}
        canMove={canMove}
      />
      <Link to="/v2/requests">{s.change}</Link>
    </>
  );
}
