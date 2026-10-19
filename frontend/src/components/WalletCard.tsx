import { Box, Card, Group, Stack, Text, Title } from '@mantine/core';
import type { ReactNode } from 'react';
import type { ApiState } from '../api/types';
import { freshness } from '../format';
import { he } from '../i18n/he';
import { Credits } from './Credits';
import { DateText } from './DateText';
import { Pct } from './Pct';
import { StateBadge, type WalletState } from './StateBadge';

const STATE: Record<ApiState, WalletState | null> = {
  healthy: 'healthy',
  near_limit: 'near',
  blocked_own: 'blockedOwn',
  blocked_group: 'blockedGroup',
  blocked_department: 'blockedDept',
  over_allocated: 'overAllocated',
  muted: null,
};

export interface WalletCardProps {
  title: string;
  total: number;
  used: number;
  remaining: number;
  /** Fraction, 0.689 = 68.9%. */
  percent: number;
  resetDate: string;
  takenAt: string | null;
  state?: ApiState;
  allocated?: number;
  unallocated?: number;
  /** Binding constraint line (employee and group level). */
  binding?: ReactNode;
  children?: ReactNode;
}

const Stat = ({ label, children }: { label: string; children: ReactNode }) => (
  <Stack gap={0}>
    <Text size="xs" c="dimmed">
      {label}
    </Text>
    <Text fw={600}>{children}</Text>
  </Stack>
);

/** §12.3 wallet card; the same component at every level. */
export function WalletCard(p: WalletCardProps) {
  const fill = Math.min(p.percent, 1) * 100;
  const badge = p.state ? STATE[p.state] : null;
  const over = p.allocated !== undefined && p.allocated > p.total;
  return (
    <Card withBorder data-testid="wallet-card">
      <Stack gap="sm">
        <Group justify="space-between">
          <Title order={4}>{p.title}</Title>
          {badge && <StateBadge state={badge} />}
        </Group>
        <Group gap="xl">
          <Stat label={he.wallet.available}>
            <Credits value={p.total} />
          </Stat>
          <Stat label={he.wallet.used}>
            <Credits value={p.used} /> (<Pct value={p.percent * 100} />)
          </Stat>
          <Stat label={he.wallet.remaining}>
            <Credits value={p.remaining} />
          </Stat>
          {p.allocated !== undefined && (
            <Stat label={he.wallet.allocated}>
              <Credits value={p.allocated} />
            </Stat>
          )}
          {p.unallocated !== undefined && (
            <Stat label={he.wallet.unallocated}>
              <Credits value={p.unallocated} />
            </Stat>
          )}
          <Stat label={he.wallet.resets}>
            <DateText value={p.resetDate} />
          </Stat>
        </Group>
        <Box
          role="progressbar"
          aria-valuenow={Math.round(fill)}
          aria-valuemin={0}
          aria-valuemax={100}
          pos="relative"
          h={14}
          bg="gray.2"
          style={{ borderRadius: 4, overflow: 'hidden' }}
        >
          <Box
            h="100%"
            w={`${fill}%`}
            bg={p.percent >= 1 ? 'red.7' : p.percent >= 0.8 ? 'yellow.6' : 'blue.6'}
          />
          {[80, 100].map((m) => (
            <Box
              key={m}
              data-marker={m}
              pos="absolute"
              top={0}
              bottom={0}
              // RTL: the bar fills from the right.
              style={{
                insetInlineStart: `${m === 100 ? 99.5 : m}%`,
                width: 2,
                background: '#495057',
              }}
            />
          ))}
        </Box>
        {over && (
          <Text c="orange.8" role="alert">
            ▲ {he.wallet.overAllocatedWarning}
          </Text>
        )}
        {p.binding && <Text size="sm">{p.binding}</Text>}
        {p.takenAt && (
          <Text size="xs" c="dimmed">
            {freshness(p.takenAt)}
          </Text>
        )}
        {p.children}
      </Stack>
    </Card>
  );
}
