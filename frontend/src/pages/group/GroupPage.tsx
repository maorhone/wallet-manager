import { Loader, Stack, Tabs, Title } from '@mantine/core';
import { Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useGroupSummary } from '../../api/queries';
import { ErrorState } from '../../components/ErrorState';
import { usePeriod } from '../../components/PeriodSelector';
import { he } from '../../i18n/he';
import Grants from './Grants';
import History from './History';
import Members from './Members';
import Overview from './Overview';
import ServiceKeys from './ServiceKeys';

const TABS = ['overview', 'members', 'grants', 'service-keys', 'history'];

/** "הקבוצה" (§12.5.2): Overview, Members, Grants, Service keys and History; Models come later. */
export default function GroupPage() {
  const { id = '', tab = 'overview' } = useParams();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const { period } = usePeriod();
  const summary = useGroupSummary(id, period);

  if (summary.isPending) return <Loader aria-label={he.loading} />;
  if (summary.error || !summary.data)
    return <ErrorState error={summary.error} onRetry={summary.refetch} />;
  const s = summary.data;
  // D11: PMO and viewers do not see the Members tab (same roles as `edit_members`).
  const tabs = TABS.filter(
    (k) =>
      (k !== 'members' || s.actions.edit_members) &&
      (k !== 'service-keys' || s.actions.service_keys.view),
  );
  if (!tabs.includes(tab)) return <Navigate to={`/group/${id}/overview`} replace />;
  return (
    <Stack>
      <Title order={3}>
        {he.group}: {s.group.name}
      </Title>
      <Tabs
        value={tab}
        onChange={(v) => navigate({ pathname: `/group/${id}/${v}`, search: search.toString() })}
      >
        <Tabs.List>
          {tabs.map((k) => (
            <Tabs.Tab key={k} value={k} data-testid={`tab-${k}`}>
              {he.groupPage.tabs[k]}
            </Tabs.Tab>
          ))}
        </Tabs.List>
      </Tabs>
      {tab === 'overview' && <Overview summary={s} />}
      {tab === 'members' && <Members groupId={id} period={period} summary={s} />}
      {tab === 'grants' && <Grants groupId={id} period={period} summary={s} />}
      {tab === 'service-keys' && <ServiceKeys groupId={id} summary={s} />}
      {tab === 'history' && <History groupId={id} />}
    </Stack>
  );
}
