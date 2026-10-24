import type { ReactNode } from 'react';
export function StatusPill({
  children,
  tone = 'neutral',
}: {
  children: ReactNode;
  tone?: 'neutral' | 'good' | 'warning' | 'danger';
}) {
  return <span className={`v2-tag v2-status-${tone}`}>{children}</span>;
}
