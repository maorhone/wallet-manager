import { Card, Table, Title } from '@mantine/core';
import type { MyWallet } from '../api/types';
import { he } from '../i18n/he';
import { Credits } from './Credits';
import { EmptyState } from './EmptyState';
import { Num } from './Num';

export function ModelTable({ models }: { models: MyWallet['models'] }) {
  return (
    <Card withBorder data-testid="model-table">
      <Title order={5}>{he.wallet.models}</Title>
      {models.length === 0 ? (
        <EmptyState />
      ) : (
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{he.wallet.model}</Table.Th>
              <Table.Th>{he.wallet.cap}</Table.Th>
              <Table.Th>{he.wallet.used}</Table.Th>
              <Table.Th>{he.wallet.remaining}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {models.map((m) => (
              <Table.Tr key={m.model}>
                <Table.Td>
                  <Num>{m.model}</Num>
                </Table.Td>
                <Table.Td>{m.cap === null ? he.wallet.noCap : <Credits value={m.cap} />}</Table.Td>
                <Table.Td>
                  <Credits value={m.used} />
                </Table.Td>
                <Table.Td>{m.remaining === null ? '–' : <Credits value={m.remaining} />}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Card>
  );
}
