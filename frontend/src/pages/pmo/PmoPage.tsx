import { Loader, Stack, Tabs, Title } from '@mantine/core';
import { Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useDepartmentSummary } from '../../api/queries';
import { ErrorState } from '../../components/ErrorState';
import { usePeriod } from '../../components/PeriodSelector';
import { he } from '../../i18n/he';
import Grants from './Grants';
import Pool from './Pool';
import Projects from './Projects';

const TABS = ['pool', 'grants', 'projects'];

/** "גוף פרויקטים" (§12.5.3): Pool, Grants and Projects. */
export default function PmoPage() {
  const { id = '', tab = 'pool' } = useParams();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const { period } = usePeriod();
  const summary = useDepartmentSummary(id, period);

  if (!TABS.includes(tab)) return <Navigate to={`/pmo/${id}/pool`} replace />;
  if (summary.isPending) return <Loader aria-label={he.loading} />;
  if (summary.error || !summary.data)
    return <ErrorState error={summary.error} onRetry={summary.refetch} />;
  const s = summary.data;
  return (
    <Stack>
      <Title order={3}>
        {he.pmo.title}: {s.department.name}
      </Title>
      <Tabs
        value={tab}
        onChange={(v) => navigate({ pathname: `/pmo/${id}/${v}`, search: search.toString() })}
      >
        <Tabs.List>
          {TABS.map((k) => (
            <Tabs.Tab key={k} value={k} data-testid={`tab-${k}`}>
              {he.pmo.tabs[k]}
            </Tabs.Tab>
          ))}
        </Tabs.List>
      </Tabs>
      {tab === 'pool' && <Pool summary={s} period={period} />}
      {tab === 'grants' && <Grants summary={s} period={period} />}
      {tab === 'projects' && <Projects summary={s} />}
    </Stack>
  );
}
