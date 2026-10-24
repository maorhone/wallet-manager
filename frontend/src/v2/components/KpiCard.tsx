import type { ReactNode } from 'react';
import { he } from '../../i18n/he';
export function KpiCard({
  title,
  value,
  description,
}: {
  title: string;
  value: ReactNode;
  description: string;
}) {
  return (
    <section className="v2-card v2-kpi">
      <h3>{title}</h3>
      <div className="v2-kpi-value">
        <bdi dir="ltr">{value ?? he.v2.unavailable}</bdi>
      </div>
      <p className="v2-muted">{description}</p>
    </section>
  );
}
