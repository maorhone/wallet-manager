import { Card, Table, Title } from '@mantine/core';
import type { MyWallet } from '../api/types';
import { he } from '../i18n/he';
import { Credits } from './Credits';
import { DateText } from './DateText';
import { EmptyState } from './EmptyState';
import { Num } from './Num';

const daysLeft = (iso: string) =>
  Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000));

export function SharesTable({ shares }: { shares: MyWallet['shares'] }) {
  return (
    <Card withBorder data-testid="shares-table">
      <Title order={5}>{he.wallet.shares}</Title>
      {shares.length === 0 ? (
        <EmptyState message={he.wallet.noShares} />
      ) : (
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{he.wallet.project}</Table.Th>
              <Table.Th>{he.wallet.amount}</Table.Th>
              <Table.Th>{he.wallet.used}</Table.Th>
              <Table.Th>{he.wallet.expires}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {shares.map((s) => (
              <Table.Tr key={s.id}>
                <Table.Td>
                  <Num>#{s.grant_id}</Num>
                </Table.Td>
                <Table.Td>
                  <Credits value={s.amount} />
                </Table.Td>
                <Table.Td>
                  <Credits value={s.used ?? 0} />
                </Table.Td>
                <Table.Td>
                  {s.expires_at ? (
                    <>
                      <DateText value={s.expires_at} /> (
                      {he.wallet.daysLeft(daysLeft(s.expires_at))})
                    </>
                  ) : (
                    '–'
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Card>
  );
}
