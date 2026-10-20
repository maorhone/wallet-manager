import { Box, Group, Text } from '@mantine/core';
import { he } from '../i18n/he';
import { Credits } from './Credits';

export interface Slice {
  key: string;
  label: string;
  value: number;
}

const COLORS = ['#1c7ed6', '#37b24d', '#f08c00', '#7048e8', '#0ca678', '#e8590c', '#3bc9db'];
const GREY = '#adb5bd';

/** Stacked bar of the department budget: groups, PMO pool, reserve, unallocated (label + value in the legend). */
export function SplitChart({ slices, total }: { slices: Slice[]; total: number }) {
  const all = [
    ...slices,
    { key: 'unallocated', label: he.departmentPage.unallocated, value: total },
  ];
  all[all.length - 1].value = Math.max(0, total - slices.reduce((s, x) => s + x.value, 0));
  return (
    <Box data-testid="split-chart" role="img" aria-label={he.departmentPage.splitChart}>
      <Group gap={0} wrap="nowrap" style={{ height: 24, borderRadius: 4, overflow: 'hidden' }}>
        {all.map((s, i) => (
          <Box
            key={s.key}
            title={s.label}
            style={{
              width: `${total > 0 ? (s.value / total) * 100 : 0}%`,
              height: '100%',
              background: s.key === 'unallocated' ? GREY : COLORS[i % COLORS.length],
            }}
          />
        ))}
      </Group>
      <Group gap="md" mt="xs">
        {all.map((s, i) => (
          <Group key={s.key} gap={4}>
            <Box
              w={10}
              h={10}
              style={{ background: s.key === 'unallocated' ? GREY : COLORS[i % COLORS.length] }}
            />
            <Text size="xs">
              {s.label} <Credits value={s.value} />
            </Text>
          </Group>
        ))}
      </Group>
    </Box>
  );
}
