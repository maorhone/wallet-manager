import { MantineProvider } from '@mantine/core';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

const chart = vi.hoisted(() => ({ bars: vi.fn(), cap: vi.fn(), data: vi.fn() }));
vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => children,
  ComposedChart: (props: { children: React.ReactNode; data: unknown }) => {
    chart.data(props.data);
    return props.children;
  },
  Bar: (props: unknown) => {
    chart.bars(props);
    return null;
  },
  ReferenceLine: (props: unknown) => {
    chart.cap(props);
    return null;
  },
  CartesianGrid: () => null,
  Legend: () => null,
  Line: () => null,
  Tooltip: () => null,
  XAxis: () => null,
  YAxis: () => null,
}));
import { UsageChart } from './UsageChart';

describe('UsageChart', () => {
  it('stacks series by key and draws the cap line', () => {
    renderToStaticMarkup(
      <MantineProvider>
        <UsageChart
          series={[
            { bucket: '2026-10-02', key: 'dani@corp.example', spend: 4 },
            { bucket: '2026-10-01', key: 'model-a', spend: 2 },
            { bucket: '2026-10-01', key: 'dani@corp.example', spend: 3 },
            { bucket: '2026-10-01', key: 'model-a', spend: 5 },
          ]}
          cap={100}
          loading={false}
          error={null}
        />
      </MantineProvider>,
    );
    const bars = chart.bars.mock.calls.map(([p]) => p);
    expect(bars.map((p) => p.name).sort()).toEqual(['dani@corp.example', 'model-a']);
    expect(new Set(bars.map((p) => p.stackId)).size).toBe(1);
    const rows = chart.data.mock.calls[0][0];
    expect(rows.map((r: { bucket: string }) => r.bucket)).toEqual(['2026-10-01', '2026-10-02']);
    const modelKey = bars.find((p) => p.name === 'model-a').dataKey;
    const memberKey = bars.find((p) => p.name === 'dani@corp.example').dataKey;
    expect(rows[0][modelKey]).toBe(7);
    expect(rows[0][memberKey]).toBe(3);
    expect(rows[1][modelKey]).toBe(0);
    expect(rows[1].cumulative).toBe(14);
    expect(chart.cap.mock.calls[0][0]).toMatchObject({ y: 100, yAxisId: 'total' });
  });
});
