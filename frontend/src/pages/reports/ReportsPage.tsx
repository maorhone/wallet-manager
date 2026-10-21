import { Stack, Tabs, Title } from '@mantine/core';
import { Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ApiError } from '../../api/client';
import { useMe } from '../../api/queries';
import { ErrorState } from '../../components/ErrorState';
import { usePeriod } from '../../components/PeriodSelector';
import { he } from '../../i18n/he';
import Allocation, { GlobalAllocation } from './Allocation';
import Overallocation from './Overallocation';
import Usage from './Usage';

/** Phase 4 report shell. Allocation, Usage and Over-allocation. */
export default function ReportsPage() {
  const { kind = '', id = '', tab = 'allocation' } = useParams();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const { data: me } = useMe();
  const { period } = usePeriod();
  if (!me) return null;
  if (
    !['me', 'group', 'department', 'global'].includes(kind) ||
    (kind === 'global' && !me.scopes.some((s) => s.kind === 'platform'))
  ) {
    return <ErrorState error={new ApiError(403, { code: 'FORBIDDEN' })} />;
  }
  const tabs =
    kind === 'department' || kind === 'global'
      ? (['allocation', 'usage', 'overallocation'] as const)
      : (['allocation', 'usage'] as const);
  if (!tabs.some((t) => t === tab)) {
    return (
      <Navigate
        to={{ pathname: `/report/${kind}/${id}/allocation`, search: search.toString() }}
        replace
      />
    );
  }
  return (
    <Stack>
      <Title order={3}>{he.reports.title}</Title>
      <Tabs
        value={tab}
        onChange={(value) => {
          if (value)
            navigate({ pathname: `/report/${kind}/${id}/${value}`, search: search.toString() });
        }}
      >
        <Tabs.List>
          {tabs.map((t) => (
            <Tabs.Tab key={t} value={t}>
              {he.reports.tabs[t]}
            </Tabs.Tab>
          ))}
        </Tabs.List>
      </Tabs>
      {tab === 'allocation' &&
        (kind === 'global' ? (
          <GlobalAllocation period={period} />
        ) : (
          <Allocation
            level={kind === 'me' ? 'employee' : kind === 'group' ? 'group' : 'department'}
            id={kind === 'me' ? me.upn : id}
            period={period}
          />
        ))}
      {tab === 'usage' && (
        <Usage
          key={`${kind}:${id}`}
          scope={kind === 'me' ? 'employee' : (kind as 'group' | 'department' | 'global')}
          id={kind === 'me' ? me.upn : id}
          period={period}
        />
      )}
      {tab === 'overallocation' && (
        <Overallocation department={kind === 'department' ? id : undefined} />
      )}
    </Stack>
  );
}
