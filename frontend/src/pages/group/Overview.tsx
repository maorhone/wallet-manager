import { Card, List, Stack, Table, Text, Title } from '@mantine/core';
import type { GroupSummary, MemberSlim } from '../../api/types';
import { Credits } from '../../components/Credits';
import { EmptyState } from '../../components/EmptyState';
import { Num } from '../../components/Num';
import { Pct } from '../../components/Pct';
import { WalletCard } from '../../components/WalletCard';
import { he } from '../../i18n/he';

const t = he.groupPage;

function MemberList({
  title,
  items,
  testId,
}: {
  title: string;
  items: MemberSlim[];
  testId: string;
}) {
  return (
    <Card withBorder data-testid={testId}>
      <Title order={5}>{title}</Title>
      {items.length === 0 ? (
        <Text c="dimmed">{t.none}</Text>
      ) : (
        <List>
          {items.map((m) => (
            <List.Item key={m.upn}>
              {m.display_name}: <Credits value={m.used} /> / <Credits value={m.total} /> (
              <Pct value={m.percent * 100} />)
            </List.Item>
          ))}
        </List>
      )}
    </Card>
  );
}

export default function Overview({ summary: s }: { summary: GroupSummary }) {
  const w = s.wallet;
  return (
    <Stack>
      <WalletCard
        title={s.group.name}
        total={w.total}
        used={w.used}
        remaining={w.remaining}
        percent={w.percent}
        resetDate={s.reset_date}
        takenAt={s.spend_taken_at}
        state={w.state}
        allocated={w.allocated}
        unallocated={w.unallocated}
      />
      <Card withBorder data-testid="pots">
        <Title order={5}>{t.pots}</Title>
        {s.pots.length === 0 ? (
          <EmptyState />
        ) : (
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{he.wallet.model}</Table.Th>
                <Table.Th>{t.pot}</Table.Th>
                <Table.Th>{he.wallet.used}</Table.Th>
                <Table.Th>{t.capsSum}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {s.pots.map((p) => (
                <Table.Tr key={p.model}>
                  <Table.Td>
                    <Num>{p.model}</Num>
                  </Table.Td>
                  <Table.Td>
                    <Credits value={p.amount} />
                  </Table.Td>
                  <Table.Td>
                    <Credits value={p.used} />
                  </Table.Td>
                  <Table.Td>
                    <Credits value={p.members_caps_sum} />
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Card>
      <Card withBorder data-testid="grants">
        <Title order={5}>{t.grants}</Title>
        {s.grants.length === 0 ? (
          <Text c="dimmed">{t.noGrants}</Text>
        ) : (
          <Text>
            <Num>{s.grants.length}</Num> · <Credits value={w.grants} />
          </Text>
        )}
      </Card>
      <MemberList title={t.topConsumers} items={s.top_consumers} testId="top-consumers" />
      <MemberList title={t.nearLimit} items={s.near_limit_members} testId="near-limit" />
      <Card withBorder data-testid="pending-sync">
        <Text>
          {t.pendingSync}: <Num>{s.pending_sync_count}</Num>
        </Text>
      </Card>
    </Stack>
  );
}
