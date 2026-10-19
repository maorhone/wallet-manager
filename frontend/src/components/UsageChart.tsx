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
import type { UsagePoint } from '../api/types';
import { he } from '../i18n/he';
import { EmptyState } from './EmptyState';
import { ErrorState } from './ErrorState';

const COLORS = ['#1c7ed6', '#f08c00', '#2f9e44', '#ae3ec9', '#e03131', '#0c8599'];

/** Pivot (day, model, spend) rows into one row per day plus a running total. */
export function pivot(points: UsagePoint[]) {
  const models = [...new Set(points.map((p) => p.model))].sort();
  const days = new Map<string, Record<string, number | string>>();
  for (const p of points) {
    const row = days.get(p.day) ?? { day: p.day };
    row[p.model] = ((row[p.model] as number) ?? 0) + p.spend;
    days.set(p.day, row);
  }
  let running = 0;
  const rows = [...days.values()]
    .sort((a, b) => (a.day as string).localeCompare(b.day as string))
    .map((row) => {
      running += models.reduce((s, m) => s + ((row[m] as number) ?? 0), 0);
      return { ...row, cumulative: running };
    });
  return { models, rows };
}

interface Props {
  points: UsagePoint[] | undefined;
  loading: boolean;
  error: unknown;
  onRetry?: () => void;
  /** The personal cap for the period, drawn as a line against the running total. */
  cap: number;
}

/** Daily spend stacked by model (left axis); running total against the cap line (right axis). */
export function UsageChart({ points, loading, error, onRetry, cap }: Props) {
  let body;
  if (loading) body = <Loader aria-label={he.loading} />;
  else if (error) body = <ErrorState error={error} onRetry={onRetry} />;
  else if (!points || points.length === 0) body = <EmptyState />;
  else {
    const { models, rows } = pivot(points);
    body = (
      <ResponsiveContainer width="100%" height={280}>
        <ComposedChart data={rows}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="day" tickFormatter={(d: string) => d.slice(8)} />
          <YAxis yAxisId="day" orientation="right" />
          <YAxis
            yAxisId="total"
            orientation="left"
            domain={[0, (max: number) => Math.max(max, cap)]}
          />
          <Tooltip />
          <Legend />
          {models.map((m, i) => (
            <Bar key={m} yAxisId="day" dataKey={m} stackId="d" fill={COLORS[i % COLORS.length]} />
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
      <Title order={5}>{he.wallet.usage}</Title>
      {body}
    </Card>
  );
}
