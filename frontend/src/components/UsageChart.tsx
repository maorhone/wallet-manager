import { Card, Loader, Title } from '@mantine/core';
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { UsageSeriesPoint } from '../api/types';
import { credits } from '../format';
import { he } from '../i18n/he';
import { EmptyState } from './EmptyState';
import { ErrorState } from './ErrorState';

const COLORS = ['#1c7ed6', '#f08c00', '#2f9e44', '#ae3ec9', '#e03131', '#0c8599'];

/** Safe data keys also support member UPNs containing dots. */
export function pivot(series: UsageSeriesPoint[]) {
  const models = [...new Set(series.map((p) => p.key))]
    .sort()
    .map((name, i) => ({ name, dataKey: `series${i}` }));
  const dataKeys = new Map(models.map((m) => [m.name, m.dataKey]));
  const buckets = new Map<string, Record<string, number | string>>();
  for (const p of series) {
    const row = buckets.get(p.bucket) ?? {
      bucket: p.bucket,
      ...Object.fromEntries(models.map((m) => [m.dataKey, 0])),
    };
    const key = dataKeys.get(p.key)!;
    row[key] = (row[key] as number) + p.spend;
    buckets.set(p.bucket, row);
  }
  let running = 0;
  const rows = [...buckets.values()]
    .sort((a, b) => (a.bucket as string).localeCompare(b.bucket as string))
    .map((row) => {
      running += models.reduce((sum, m) => sum + (row[m.dataKey] as number), 0);
      return { ...row, cumulative: running };
    });
  return { models, rows };
}

interface Props {
  series: UsageSeriesPoint[] | undefined;
  loading: boolean;
  error: unknown;
  onRetry?: () => void;
  /** The cap for the period, drawn as a line against the running total. */
  cap: number;
  title?: string;
}

/** Daily spend stacked by model (left axis); running total against the cap line (right axis). */
export function UsageChart({
  series,
  loading,
  error,
  onRetry,
  cap,
  title = he.wallet.usage,
}: Props) {
  let body;
  if (loading) body = <Loader aria-label={he.loading} />;
  else if (error) body = <ErrorState error={error} onRetry={onRetry} />;
  else if (!series || series.length === 0) body = <EmptyState />;
  else {
    const { models, rows } = pivot(series);
    body = (
      <ResponsiveContainer width="100%" height={280}>
        <ComposedChart data={rows}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="bucket" tickFormatter={(d: string) => d.slice(5)} />
          <YAxis yAxisId="day" orientation="right" tickFormatter={credits} />
          <YAxis
            yAxisId="total"
            orientation="left"
            tickFormatter={credits}
            domain={[0, (max: number) => Math.max(max, cap)]}
          />
          <Tooltip formatter={(value: number) => credits(value)} />
          <Legend />
          {models.map((m, i) => (
            <Bar
              key={m.dataKey}
              name={m.name}
              yAxisId="day"
              dataKey={m.dataKey}
              stackId="d"
              fill={COLORS[i % COLORS.length]}
            />
          ))}
          <Line
            yAxisId="total"
            dataKey="cumulative"
            name={he.wallet.cumulative}
            stroke="#495057"
            dot={false}
          />
          <ReferenceLine
            yAxisId="total"
            y={cap}
            stroke="#e03131"
            strokeDasharray="6 3"
            label={he.wallet.cap}
          />
        </ComposedChart>
      </ResponsiveContainer>
    );
  }
  return (
    <Card withBorder data-testid="usage-chart">
      <Title order={5}>{title}</Title>
      {body}
    </Card>
  );
}
