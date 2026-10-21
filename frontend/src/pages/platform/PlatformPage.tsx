import { Alert, Stack, Tabs, Title } from '@mantine/core';
import { Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useMe } from '../../api/queries';
import { he } from '../../i18n/he';
import Departments from './Departments';
import Pending from './Pending';

const TABS = ['pending', 'departments', 'operations', 'policies', 'audit'];

export default function PlatformPage() {
  const { tab = 'pending' } = useParams();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const { data: me } = useMe();
  if (!me?.roles.some((r) => r.role === 'platform_admin'))
    return <Alert color="red">{he.platformPage.forbidden}</Alert>;
  if (!TABS.includes(tab)) return <Navigate to="/platform/pending" replace />;
  return (
    <Stack>
      <Title order={3}>{he.platform}</Title>
      <Tabs
        value={tab}
        onChange={(value) =>
          navigate({ pathname: `/platform/${value}`, search: search.toString() })
        }
      >
        <Tabs.List>
          {TABS.map((key) => (
            <Tabs.Tab
              key={key}
              value={key}
              data-testid={`tab-${key}`}
              disabled={key !== 'pending' && key !== 'departments'}
            >
              {he.platformPage.tabs[key]}
            </Tabs.Tab>
          ))}
        </Tabs.List>
      </Tabs>
      {tab === 'pending' && <Pending />}
      {tab === 'departments' && <Departments />}
    </Stack>
  );
}
