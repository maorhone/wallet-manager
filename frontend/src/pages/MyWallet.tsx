import { Alert, Card, Group, Loader, Stack, Text, Title } from '@mantine/core';
import { useMyUsage, useMyWallet } from '../api/queries';
import type { MyWallet as Wallet } from '../api/types';
import { ErrorState } from '../components/ErrorState';
import { LimitsBox } from '../components/LimitsBox';
import { ModelTable } from '../components/ModelTable';
import { NotificationPrefs } from '../components/NotificationPrefs';
import { Num } from '../components/Num';
import { OwnersBox } from '../components/OwnersBox';
import { usePeriod } from '../components/PeriodSelector';
import { Pct } from '../components/Pct';
import { SharesTable } from '../components/SharesTable';
import { SyncBadge } from '../components/SyncBadge';
import { UsageChart } from '../components/UsageChart';
import { WalletCard } from '../components/WalletCard';
import { he } from '../i18n/he';

const NEAR = 0.8;

function lastDay(period: string): string {
  const [y, m] = period.split('-').map(Number);
  return `${period}-${String(new Date(Date.UTC(y, m, 0)).getUTCDate()).padStart(2, '0')}`;
}

function bindingLine({ wallet: w }: Wallet) {
  const who = {
    self: he.wallet.bindingSelf,
    group: he.wallet.bindingGroup,
    department: he.wallet.bindingDepartment,
  }[w.binding.level];
  return (
    <>
      {he.wallet.limitedBy}: {who} ({he.wallet.groupAt}{' '}
      <Pct value={w.binding.group_percent * 100} />, {he.wallet.departmentAt}{' '}
      <Pct value={w.binding.department_percent * 100} />)
    </>
  );
}

export default function MyWallet() {
  const { period } = usePeriod();
  const wallet = useMyWallet(period);
  const usage = useMyUsage(period ? `${period}-01` : '', period ? lastDay(period) : '');

  if (wallet.isPending) return <Loader aria-label={he.loading} />;
  if (wallet.error || !wallet.data)
    return <ErrorState error={wallet.error} onRetry={wallet.refetch} />;
  const d = wallet.data;
  const w = d.wallet;
  const heads = d.owners.base_cap.people.map((p) => p.display_name).join(', ');
  return (
    <Stack>
      <Title order={3}>{he.myWallet}</Title>
      {(w.state === 'blocked_group' || w.state === 'blocked_department') && (
        <Alert color="red" variant="light" data-testid="blocked-contact">
          {he.wallet.contact}: {heads}
        </Alert>
      )}
      <WalletCard
        title={he.myWallet}
        total={w.total}
        used={w.used}
        remaining={w.remaining}
        percent={w.percent}
        resetDate={d.reset_date}
        takenAt={d.spend_taken_at}
        state={w.state}
        binding={bindingLine(d)}
      >
        {d.sync_status !== 'applied' && <SyncBadge status={d.sync_status} />}
      </WalletCard>
      <ModelTable models={d.models} />
      <SharesTable shares={d.shares} />
      <LimitsBox limits={d.limits} />
      <Card withBorder data-testid="group-context">
        <Group justify="space-between">
          <Title order={5}>{he.wallet.groupContext}</Title>
          <Text>
            <Num>{d.group.name}</Num>: {he.wallet.groupPercent}{' '}
            <Pct value={d.group.percent * 100} />
          </Text>
        </Group>
        {d.group.percent >= NEAR && (
          <Text c="orange.8" role="alert">
            ▲ {he.wallet.groupNear}
          </Text>
        )}
      </Card>
      <UsageChart
        series={usage.data?.map((p) => ({ bucket: p.day, key: p.model, spend: p.spend }))}
        loading={usage.isPending}
        error={usage.error}
        onRetry={usage.refetch}
        cap={w.total}
      />
      <OwnersBox owner={d.owners.base_cap} />
      <NotificationPrefs />
    </Stack>
  );
}
