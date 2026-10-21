import { Loader, Stack, Tabs, Title } from '@mantine/core';
import { Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ApiError } from '../../api/client';
import { useGroupSummary, useMe } from '../../api/queries';
import { ErrorState } from '../../components/ErrorState';
import { usePeriod } from '../../components/PeriodSelector';
import { he } from '../../i18n/he';
import Allocation, { GlobalAllocation } from './Allocation';
import Overallocation from './Overallocation';
import Usage from './Usage';
import Grants from './Grants';
import Blocked from './Blocked';

/** Scoped report tabs; PMO reports contain department grants only. */
export default function ReportsPage() {
  const { kind = '', id = '', tab = 'allocation' } = useParams();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const { data: me } = useMe();
  const { period } = usePeriod();
  const group = useGroupSummary(id, kind === 'group' ? period : '');
  if (!me) return null;
  if (
    !['me', 'group', 'department', 'global', 'pmo'].includes(kind) ||
    (kind === 'global' && !me.scopes.some((s) => s.kind === 'platform'))
  ) {
    return <ErrorState error={new ApiError(403, { code: 'FORBIDDEN' })} />;
  }
  if (kind === 'group' && group.isPending) return <Loader aria-label={he.loading} />;
  const isAdmin = me.roles.some((r) => r.role === 'platform_admin');
  const canViewMembers =
    kind === 'me' ||
    isAdmin ||
    (kind === 'group' && group.data?.actions.edit_members) ||
    (kind === 'department' &&
      me.roles.some(
        (r) =>
          r.role === 'department_rep' && r.scope_kind === 'department' && String(r.scope_id) === id,
      ));
  const tabs: (keyof typeof he.reports.tabs)[] =
    kind === 'pmo'
      ? ['grants']
      : [
          'allocation',
          'usage',
          ...(kind === 'department' || kind === 'global'
            ? (['grants', 'overallocation'] as const)
            : []),
          ...(canViewMembers ? (['blocked'] as const) : []),
        ];
  if (!tabs.some((t) => t === tab)) {
    return (
      <Navigate
        to={{ pathname: `/report/${kind}/${id}/${tabs[0]}`, search: search.toString() }}
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
      {tab === 'grants' && <Grants department={kind === 'global' ? undefined : id} />}
      {tab === 'blocked' && (
        <Blocked
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
