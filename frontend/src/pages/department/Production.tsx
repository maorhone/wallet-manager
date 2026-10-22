import {
  Button,
  Card,
  Drawer,
  Group,
  Loader,
  NumberInput,
  Stack,
  Table,
  TextInput,
  Title,
} from '@mantine/core';
import { useState } from 'react';
import { useProduction, useProductionWrite } from '../../api/queries';
import type { DepartmentSummary, ProductionSystemRow } from '../../api/types';
import { ChangeSummaryDialog } from '../../components/ChangeSummaryDialog';
import { Credits } from '../../components/Credits';
import { EmptyState } from '../../components/EmptyState';
import { ErrorState } from '../../components/ErrorState';
import { ServiceKeysPanel } from '../../components/ServiceKeysPanel';
import { SyncBadge } from '../../components/SyncBadge';
import { WalletCard } from '../../components/WalletCard';
import { he } from '../../i18n/he';

const t = he.production;

/** Tab "ייצור" (§12.5.4 tab 6): the production organization, its systems and their keys; admin edits. */
export default function Production({ summary: s }: { summary: DepartmentSummary }) {
  const deptId = String(s.department.id);
  const prod = useProduction(deptId);
  const write = useProductionWrite(deptId);
  const [dialog, setDialog] = useState<'org' | 'system' | null>(null);
  const [review, setReview] = useState(false);
  const [budget, setBudget] = useState<number>(0);
  const [name, setName] = useState('');
  const [keysOf, setKeysOf] = useState<ProductionSystemRow | null>(null);

  if (prod.isPending) return <Loader aria-label={he.loading} />;
  if (prod.error || !prod.data) return <ErrorState error={prod.error} onRetry={prod.refetch} />;
  const { org, systems, actions } = prod.data;
  const allocated = systems.reduce((sum, x) => sum + x.budget, 0);
  const close = () => {
    setDialog(null);
    setReview(false);
    setBudget(0);
    setName('');
  };

  return (
    <Stack>
      {actions.edit && (
        <Group>
          <Button
            data-testid="production-set-budget"
            onClick={() => {
              setBudget(org?.budget ?? 0);
              setDialog('org');
            }}
          >
            {t.setBudget}
          </Button>
          {org && (
            <Button
              variant="default"
              data-testid="production-create-system"
              onClick={() => setDialog('system')}
            >
              {t.addSystem}
            </Button>
          )}
        </Group>
      )}
      {org ? (
        <WalletCard
          title={t.title}
          total={org.budget}
          used={org.used}
          remaining={Math.max(org.budget - org.used, 0)}
          percent={org.budget > 0 ? org.used / org.budget : 0}
          resetDate={s.reset_date}
          takenAt={prod.data.spend_taken_at}
          allocated={allocated}
          unallocated={org.budget - allocated}
        >
          <SyncBadge status={org.sync_status} />
        </WalletCard>
      ) : (
        <Card withBorder data-testid="production-empty">
          <EmptyState message={t.empty} />
        </Card>
      )}
      {org && (
        <Card withBorder>
          <Title order={5}>{t.systems}</Title>
          {systems.length === 0 ? (
            <EmptyState message={t.noSystems} />
          ) : (
            <Table>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>{t.systemName}</Table.Th>
                  <Table.Th>{t.budget}</Table.Th>
                  <Table.Th>{t.used}</Table.Th>
                  <Table.Th>{t.keys}</Table.Th>
                  <Table.Th>{t.sync}</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {systems.map((x) => (
                  <Table.Tr
                    key={x.id}
                    data-testid={`production-system-${x.id}`}
                    data-system-name={x.name}
                  >
                    <Table.Td>{x.name}</Table.Td>
                    <Table.Td>
                      <Credits value={x.budget} />
                    </Table.Td>
                    <Table.Td>
                      <Credits value={x.used} />
                    </Table.Td>
                    <Table.Td>{x.keys_count}</Table.Td>
                    <Table.Td>
                      <SyncBadge status={x.sync_status} />
                    </Table.Td>
                    <Table.Td>
                      {actions.edit && (
                        <Button
                          size="compact-xs"
                          variant="default"
                          data-testid={`production-system-keys-${x.id}`}
                          onClick={() => setKeysOf(x)}
                        >
                          {t.manageKeys}
                        </Button>
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </Card>
      )}
      <Drawer
        opened={keysOf !== null}
        onClose={() => setKeysOf(null)}
        position="left"
        size="xl"
        title={`${t.manageKeys}: ${keysOf?.name ?? ''}`}
      >
        {keysOf && (
          <ServiceKeysPanel ownerKind="system" ownerId={keysOf.id} manage={actions.edit} />
        )}
      </Drawer>
      {dialog === 'org' && (
        <Card withBorder data-testid="production-budget-dialog">
          <Stack>
            <NumberInput
              label={t.budget}
              data-testid="production-budget-input"
              min={0}
              value={budget}
              onChange={(v) => setBudget(Number(v) || 0)}
            />
            <Group justify="flex-end">
              <Button variant="default" onClick={close}>
                {he.groupPage.cancel}
              </Button>
              <Button
                data-testid="production-budget-review"
                disabled={budget <= 0}
                onClick={() => setReview(true)}
              >
                {he.groupPage.review}
              </Button>
            </Group>
          </Stack>
          <ChangeSummaryDialog
            opened={review}
            rows={[
              {
                key: 'budget',
                label: t.budget,
                before: org ? <Credits value={org.budget} /> : '',
                after: <Credits value={budget} />,
              },
            ]}
            onClose={() => setReview(false)}
            onConfirm={async (reason, key) => {
              await write.mutateAsync({ kind: 'org', body: { budget }, reason, key });
              close();
            }}
          />
        </Card>
      )}
      {dialog === 'system' && (
        <Card withBorder data-testid="production-system-dialog">
          <Stack>
            <TextInput
              label={t.systemName}
              data-testid="production-system-name"
              value={name}
              onChange={(e) => setName(e.currentTarget.value)}
            />
            <NumberInput
              label={t.budget}
              data-testid="production-system-budget"
              min={0}
              value={budget}
              onChange={(v) => setBudget(Number(v) || 0)}
            />
            <Group justify="flex-end">
              <Button variant="default" onClick={close}>
                {he.groupPage.cancel}
              </Button>
              <Button
                data-testid="production-system-review"
                disabled={name.trim() === '' || budget <= 0}
                onClick={() => setReview(true)}
              >
                {he.groupPage.review}
              </Button>
            </Group>
          </Stack>
          <ChangeSummaryDialog
            opened={review}
            rows={[
              { key: 'name', label: t.systemName, before: '', after: name.trim() },
              { key: 'budget', label: t.budget, before: '', after: <Credits value={budget} /> },
            ]}
            onClose={() => setReview(false)}
            onConfirm={async (reason, key) => {
              await write.mutateAsync({
                kind: 'system',
                body: { name: name.trim(), budget },
                reason,
                key,
              });
              close();
            }}
          />
        </Card>
      )}
    </Stack>
  );
}
