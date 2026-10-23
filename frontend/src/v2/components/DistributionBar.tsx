import { he } from '../../i18n/he';

/** Presentation only: values are disjoint, already calculated by the API. */
export function DistributionBar({
  used,
  assignedUnused,
  unallocated,
  overAllocated = false,
}: {
  used: number;
  assignedUnused: number;
  unallocated: number;
  overAllocated?: boolean;
}) {
  const parts = [
    { label: he.v2.used, value: used, tone: 'used' },
    { label: he.v2.assignedUnused, value: assignedUnused, tone: 'assigned' },
    { label: he.v2.unallocated, value: unallocated, tone: 'unallocated' },
  ];
  const total = parts.reduce((sum, part) => sum + Math.max(0, part.value), 0);
  return (
    <div className="v2-distribution" aria-label={he.v2.distribution}>
      <div className="v2-distribution-track" aria-hidden="true">
        {parts.map((part) => (
          <span
            key={part.tone}
            data-testid="distribution-segment"
            data-value={part.value}
            className={`v2-distribution-${part.tone}`}
            style={{ width: `${total ? (Math.max(0, part.value) / total) * 100 : 0}%` }}
          />
        ))}
      </div>
      <ul className="v2-distribution-legend">
        {parts.map((part) => (
          <li key={part.tone}>
            <span className={`v2-dot v2-distribution-${part.tone}`} />
            {part.label}: <bdi dir="ltr">{part.value.toLocaleString('he-IL')}</bdi>
          </li>
        ))}
      </ul>
      {overAllocated && (
        <p role="status" className="v2-warning">
          {he.v2.overAllocated}
        </p>
      )}
    </div>
  );
}
