import { Loader, Stack, Tabs, Title } from '@mantine/core';
import { Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useDepartmentSummary } from '../../api/queries';
import { ErrorState } from '../../components/ErrorState';
import { usePeriod } from '../../components/PeriodSelector';
import { he } from '../../i18n/he';
import GroupHeads from './GroupHeads';
import Overview from './Overview';
import Pending from './Pending';
import Split from './Split';

const TABS = ['overview', 'split'];

/** "המחלקה" (§12.5.4): Overview and Split; Models, PMO and Settings are later phases. */
export default function DepartmentPage() {
  const { id = '', tab = 'overview' } = useParams();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const { period } = usePeriod();
  const summary = useDepartmentSummary(id, period);

  const tabs = [
    ...TABS,
    ...(summary.data?.group_heads.some((h) => h.actions.edit) ? ['group-heads'] : []),
    ...(summary.data?.actions.place ? ['pending'] : []),
  ];
  if (summary.data && !tabs.includes(tab))
    return <Navigate to={`/department/${id}/overview`} replace />;
  if (summary.isPending) return <Loader aria-label={he.loading} />;
  if (summary.error || !summary.data)
    return <ErrorState error={summary.error} onRetry={summary.refetch} />;
  const s = summary.data;
  return (
    <Stack>
      <Title order={3}>
        {he.department}: {s.department.name}
      </Title>
      <Tabs
        value={tab}
        onChange={(v) =>
          navigate({ pathname: `/department/${id}/${v}`, search: search.toString() })
        }
      >
        <Tabs.List>
          {tabs.map((k) => (
            <Tabs.Tab key={k} value={k} data-testid={`tab-${k}`}>
              {he.departmentPage.tabs[k]}
            </Tabs.Tab>
          ))}
        </Tabs.List>
      </Tabs>
      {tab === 'overview' && <Overview summary={s} />}
      {tab === 'split' && <Split summary={s} />}
      {tab === 'group-heads' && <GroupHeads summary={s} period={period} />}
      {tab === 'pending' && <Pending summary={s} />}
    </Stack>
  );
}
