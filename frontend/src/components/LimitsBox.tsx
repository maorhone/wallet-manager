import { Card, Stack, Text, Title } from '@mantine/core';
import type { MyWallet } from '../api/types';
import { he } from '../i18n/he';
import { Num } from './Num';

export function LimitsBox({ limits }: { limits: MyWallet['limits'] }) {
  const show = (v: number | null) => (v === null ? '–' : <Num>{v}</Num>);
  return (
    <Card withBorder data-testid="limits-box">
      <Stack gap="xs">
        <Title order={5}>{he.wallet.limits}</Title>
        <Text>
          {show(limits.max_parallel)} {he.wallet.concurrent} · {show(limits.rpm_limit)}{' '}
          {he.wallet.rpm}
        </Text>
        <Text size="sm" c="dimmed">
          {he.wallet.limitsNote}
        </Text>
      </Stack>
    </Card>
  );
}
