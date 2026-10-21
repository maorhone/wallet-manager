import { Group, NativeSelect, Stack, Text } from '@mantine/core';
import { useState } from 'react';
import { useGroupSummary, useMe, useUsageReport } from '../../api/queries';
import type { UsageBy, UsageGranularity, UsageScope } from '../../api/types';
import { UsageChart } from '../../components/UsageChart';
import { freshness } from '../../format';
import { he } from '../../i18n/he';

/** Reuses the snapshot report for both overview charts and the Usage tab. */
export default function Usage({
  scope,
  id,
  period,
  controls = true,
}: {
  scope: UsageScope;
  id: string;
  period: string;
  controls?: boolean;
}) {
  const { data: me } = useMe();
  const [by, setBy] = useState<UsageBy>('model');
  const [granularity, setGranularity] = useState<UsageGranularity>('day');
  // The group summary already exposes the server's member-visibility permission.
  const group = useGroupSummary(id, scope === 'group' && controls ? period : '');
  const canViewMembers =
    scope === 'global'
      ? me?.roles.some((r) => r.role === 'platform_admin')
      : scope === 'group'
        ? group.data?.actions.edit_members
        : scope === 'department' &&
          me?.roles.some(
            (r) =>
              r.role === 'platform_admin' ||
              (r.role === 'department_rep' &&
                r.scope_kind === 'department' &&
                String(r.scope_id) === id),
          );
  const report = useUsageReport(scope, id, period, granularity, canViewMembers ? by : 'model');
  return (
    <Stack>
      {controls && (
        <Group>
          {canViewMembers && (
            <NativeSelect
              label={he.reports.usageBy}
              value={by}
              onChange={(e) => setBy(e.currentTarget.value as UsageBy)}
              data={[
                { value: 'model', label: he.reports.byModel },
                { value: 'member', label: he.reports.byMember },
              ]}
            />
          )}
          <NativeSelect
            label={he.reports.granularity}
            value={granularity}
            onChange={(e) => setGranularity(e.currentTarget.value as UsageGranularity)}
            data={Object.entries(he.reports.granularities).map(([value, label]) => ({
              value,
              label,
            }))}
          />
        </Group>
      )}
      <UsageChart
        title={controls ? he.reports.tabs.usage : he.wallet.usage}
        series={report.data?.series}
        cap={report.data?.cap ?? 0}
        loading={report.isPending}
        error={report.error}
        onRetry={report.refetch}
      />
      {report.data && (
        <Text size="xs" c="dimmed">
          {he.reports.currentCap}
        </Text>
      )}
      {report.data?.taken_at && (
        <Text size="xs" c="dimmed">
          {freshness(report.data.taken_at)}
        </Text>
      )}
    </Stack>
  );
}
