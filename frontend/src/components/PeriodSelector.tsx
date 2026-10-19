import { NativeSelect } from '@mantine/core';
import { useSearchParams } from 'react-router-dom';
import { useMe } from '../api/queries';
import { he } from '../i18n/he';

const MONTHS_BACK = 12;

function previousMonths(current: string): string[] {
  const [y, m] = current.split('-').map(Number);
  return Array.from({ length: MONTHS_BACK }, (_, i) => {
    const d = new Date(Date.UTC(y, m - 1 - i, 1));
    return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
  });
}

/** The selected period (`?period=YYYY-MM`, default current). Past periods are read-only (§12.1). */
export function usePeriod() {
  const { data: me } = useMe();
  const [params, setParams] = useSearchParams();
  const current = me?.current_period ?? '';
  const period = params.get('period') ?? current;
  return {
    period,
    current,
    readOnly: period !== current,
    setPeriod: (p: string) =>
      setParams((prev) => {
        const next = new URLSearchParams(prev);
        if (p === current) next.delete('period');
        else next.set('period', p);
        return next;
      }),
  };
}

export function PeriodSelector() {
  const { period, current, readOnly, setPeriod } = usePeriod();
  if (!current) return null;
  return (
    <NativeSelect
      aria-label={he.period}
      data-testid="period-selector"
      value={period}
      description={readOnly ? he.readOnlyPeriod : undefined}
      data={previousMonths(current)}
      onChange={(e) => setPeriod(e.currentTarget.value)}
    />
  );
}
