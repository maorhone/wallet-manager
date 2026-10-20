import { Button, Card, Loader, Stack, Table, Title } from '@mantine/core';
import { useState } from 'react';
import { useGrantShares, useGrants, useProjectSummary } from '../../api/queries';
import type { DepartmentSummary, Grant } from '../../api/types';
import { Credits } from '../../components/Credits';
import { DateText } from '../../components/DateText';
import { EmptyState } from '../../components/EmptyState';
import { ErrorState } from '../../components/ErrorState';
import { Num } from '../../components/Num';
import { he } from '../../i18n/he';

const t = he.pmo;

function GrantShares({ grant }: { grant: Grant }) {
  const shares = useGrantShares(grant.id);
  if (shares.isPending) return <Loader size="xs" aria-label={he.loading} />;
  if (shares.error || !shares.data) return <ErrorState error={shares.error} />;
  if (shares.data.length === 0) return <EmptyState message={t.projects.noShares} />;
  return (
    <Table data-testid={`shares-${grant.id}`}>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>{t.projects.employee}</Table.Th>
          <Table.Th>{t.amount}</Table.Th>
          <Table.Th>{t.used}</Table.Th>
          <Table.Th>{t.returned}</Table.Th>
          <Table.Th>{t.expiry}</Table.Th>
          <Table.Th>{t.status}</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {shares.data.map((s) => (
          <Table.Tr key={s.id}>
            <Table.Td>{s.display_name}</Table.Td>
            <Table.Td>
              <Credits value={s.amount} />
            </Table.Td>
            <Table.Td>
              <Credits value={s.used} />
            </Table.Td>
            <Table.Td>
              <Credits value={s.returned} />
            </Table.Td>
            <Table.Td>
              <DateText value={s.expires_at} />
            </Table.Td>
            <Table.Td>{t.statuses[s.status] ?? s.status}</Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

function ProjectDetail({ projectRef }: { projectRef: string }) {
  const p = useProjectSummary(projectRef);
  if (p.isPending) return <Loader aria-label={he.loading} />;
  if (p.error || !p.data) return <ErrorState error={p.error} onRetry={p.refetch} />;
  return (
    <Card withBorder data-testid="project-detail">
      <Title order={5}>
        {t.projects.grants}: <Num>{projectRef}</Num>
      </Title>
      <Stack>
        {p.data.grants.map((g) => (
          <Stack key={g.id} gap="xs" data-testid={`project-grant-${g.id}`}>
            <Table>
              <Table.Tbody>
                <Table.Tr>
                  <Table.Td>{g.group_name}</Table.Td>
                  <Table.Td>
                    <Credits value={g.amount} />
                  </Table.Td>
                  <Table.Td>
                    <Credits value={g.used} />
                  </Table.Td>
                  <Table.Td>
                    <Credits value={g.returned} />
                  </Table.Td>
                  <Table.Td>{t.statuses[g.status]}</Table.Td>
                </Table.Tr>
              </Table.Tbody>
            </Table>
            <GrantShares grant={g} />
          </Stack>
        ))}
      </Stack>
    </Card>
  );
}

/** Tab "פרויקטים" (§12.5.3 tab 3): one row per project reference, drill-down to grants and shares. */
export default function Projects({ summary }: { summary: DepartmentSummary }) {
  const grants = useGrants({ department: String(summary.department.id) });
  const [open, setOpen] = useState('');
  if (grants.isPending) return <Loader aria-label={he.loading} />;
  if (grants.error || !grants.data)
    return <ErrorState error={grants.error} onRetry={grants.refetch} />;

  const rows = new Map<
    string,
    { grants: number; amount: number; used: number; returned: number }
  >();
  for (const g of grants.data) {
    const r = rows.get(g.project_ref) ?? { grants: 0, amount: 0, used: 0, returned: 0 };
    rows.set(g.project_ref, {
      grants: r.grants + 1,
      amount: r.amount + g.amount,
      used: r.used + g.used,
      returned: r.returned + g.returned,
    });
  }
  if (rows.size === 0) return <EmptyState message={t.projects.empty} />;
  return (
    <Stack>
      <Card withBorder data-testid="project-list">
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t.project}</Table.Th>
              <Table.Th>{he.pmo.tabs.grants}</Table.Th>
              <Table.Th>{t.amount}</Table.Th>
              <Table.Th>{t.used}</Table.Th>
              <Table.Th>{t.returned}</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {[...rows].map(([ref, r]) => (
              <Table.Tr key={ref} data-testid={`project-row-${ref}`}>
                <Table.Td>
                  <Num>{ref}</Num>
                </Table.Td>
                <Table.Td>
                  <Num>{r.grants}</Num>
                </Table.Td>
                <Table.Td>
                  <Credits value={r.amount} />
                </Table.Td>
                <Table.Td data-testid={`project-used-${ref}`}>
                  <Credits value={r.used} />
                </Table.Td>
                <Table.Td data-testid={`project-returned-${ref}`}>
                  <Credits value={r.returned} />
                </Table.Td>
                <Table.Td>
                  <Button
                    size="compact-xs"
                    data-testid={`project-open-${ref}`}
                    onClick={() => setOpen(ref)}
                  >
                    {t.projects.open}
                  </Button>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>
      {open && <ProjectDetail projectRef={open} />}
    </Stack>
  );
}
