import { Box, Button, Group, Loader, Text, Title } from '@mantine/core';
import { Link, Navigate, Outlet, useLocation } from 'react-router-dom';
import { apiPost } from './api/client';
import { useMe } from './api/queries';
import { ErrorState } from './components/ErrorState';
import { NotificationsBell } from './components/NotificationsBell';
import { Num } from './components/Num';
import { PeriodSelector } from './components/PeriodSelector';
import { ScopeSwitcher } from './components/ScopeSwitcher';
import { StatusBanner } from './components/StatusBanner';
import { he } from './i18n/he';
import { reportPath, scopeFromPath } from './scopes';

/** Authenticated layout: banner, top bar (switcher, period, user), page outlet. */
export function Shell() {
  const { data: me, error, isPending, refetch } = useMe();
  const { pathname, search } = useLocation();
  if (isPending) return <Loader m="xl" aria-label={he.loading} />;
  if (error || !me) return <ErrorState error={error} onRetry={refetch} />;
  // D-073: the root opens the v2 shell, which restores the last authorized workspace.
  if (pathname === '/') return <Navigate to="/v2" replace />;
  const scope = scopeFromPath(pathname, me.scopes);
  const reports = scope ? reportPath(scope) : null;
  const logout = async () => {
    await apiPost('/auth/logout', undefined);
    location.assign('/login');
  };
  return (
    <>
      <StatusBanner />
      <Box component="header" px="md" py="xs" style={{ borderBottom: '1px solid #dee2e6' }}>
        <Group justify="space-between">
          <Group align="flex-end">
            <Title order={4}>{he.appName}</Title>
            <ScopeSwitcher />
            <PeriodSelector />
            {reports && (
              <Button component={Link} to={{ pathname: reports, search }} variant="subtle">
                {he.reports.title}
              </Button>
            )}
          </Group>
          <Group>
            <NotificationsBell />
            <Text size="sm">
              {me.display_name} (<Num>{me.upn}</Num>)
            </Text>
            <Button size="compact-sm" variant="default" onClick={logout}>
              {he.logout}
            </Button>
          </Group>
        </Group>
      </Box>
      <Box component="main" p="md">
        <Outlet />
      </Box>
    </>
  );
}
