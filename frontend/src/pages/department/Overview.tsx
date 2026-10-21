import { Anchor, Card, Stack, Table, Title } from '@mantine/core';
import { Link, useSearchParams } from 'react-router-dom';
import { useMe } from '../../api/queries';
import type { DepartmentSummary } from '../../api/types';
import { Credits } from '../../components/Credits';
import { Pct } from '../../components/Pct';
import { SplitChart } from '../../components/SplitChart';
import { API_STATE, StateBadge } from '../../components/StateBadge';
import { WalletCard } from '../../components/WalletCard';
import Usage from '../reports/Usage';
import { usePeriod } from '../../components/PeriodSelector';
import { he } from '../../i18n/he';

const t = he.departmentPage;

export default function Overview({ summary: s }: { summary: DepartmentSummary }) {
  const { data: me } = useMe();
  const w = s.wallet;
  const { period } = usePeriod();
  const [search] = useSearchParams();
  return (
    <Stack>
      <WalletCard
        title={s.department.name}
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
      {me?.roles.some(
        (r) =>
          r.role === 'platform_admin' ||
          (r.role === 'viewer' && r.scope_kind === 'global') ||
          (['department_rep', 'viewer'].includes(r.role) &&
            r.scope_kind === 'department' &&
            r.scope_id === s.department.id),
      ) && (
        <Usage scope="department" id={String(s.department.id)} period={period} controls={false} />
      )}
      <Card withBorder>
        <Title order={5}>{t.splitChart}</Title>
        <SplitChart
          total={w.total}
          slices={[
            ...s.split.groups.map((g) => ({ key: `g${g.id}`, label: g.name, value: g.base })),
            { key: 'pmo', label: t.pmoPool, value: s.split.pmo_pool },
            { key: 'reserve', label: t.reserve, value: s.split.reserve },
          ]}
        />
      </Card>
      <Card withBorder data-testid="groups-table">
        <Title order={5}>{t.groups}</Title>
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{he.groupPage.name}</Table.Th>
              <Table.Th>{t.base}</Table.Th>
              <Table.Th>{t.grants}</Table.Th>
              <Table.Th>{he.groupPage.total}</Table.Th>
              <Table.Th>{he.wallet.used}</Table.Th>
              <Table.Th>{he.groupPage.percent}</Table.Th>
              <Table.Th>{he.groupPage.status}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {s.groups.map((g) => {
              const badge = API_STATE[g.state];
              return (
                <Table.Tr key={g.id} data-row={`g:${g.id}`}>
                  <Table.Td>
                    <Anchor
                      component={Link}
                      to={{ pathname: `/group/${g.id}/overview`, search: search.toString() }}
                      aria-label={`${t.openGroup} ${g.name}`}
                    >
                      {g.name}
                    </Anchor>
                  </Table.Td>
                  <Table.Td>
                    <Credits value={g.base} />
                  </Table.Td>
                  <Table.Td>
                    <Credits value={g.grants} />
                  </Table.Td>
                  <Table.Td>
                    <Credits value={g.total} />
                  </Table.Td>
                  <Table.Td>
                    <Credits value={g.used} />
                  </Table.Td>
                  <Table.Td>
                    <Pct value={g.percent * 100} />
                  </Table.Td>
                  <Table.Td>{badge && <StateBadge state={badge} />}</Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      </Card>
    </Stack>
  );
}
