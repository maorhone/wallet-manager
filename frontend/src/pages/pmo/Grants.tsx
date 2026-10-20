import { Button, Group, Loader, Stack } from '@mantine/core';
import { useState } from 'react';
import { useGrantWrite, useGrants, usePmoPool } from '../../api/queries';
import type { DepartmentSummary } from '../../api/types';
import { ErrorState } from '../../components/ErrorState';
import { GrantList } from '../../components/GrantList';
import { GrantWizard } from '../../components/GrantWizard';
import { he } from '../../i18n/he';

/** Tab "מענקים" (§12.5.3 tab 2): the grant list and the create wizard. */
export default function Grants({
  summary,
  period,
}: {
  summary: DepartmentSummary;
  period: string;
}) {
  const dept = String(summary.department.id);
  const grants = useGrants({ department: dept });
  const pool = usePmoPool(dept, period);
  const write = useGrantWrite();
  const [open, setOpen] = useState(false);
  if (grants.isPending) return <Loader aria-label={he.loading} />;
  if (grants.error || !grants.data)
    return <ErrorState error={grants.error} onRetry={grants.refetch} />;
  return (
    <Stack>
      {pool.data?.actions.create_grant && (
        <Group>
          <Button data-testid="create-grant" onClick={() => setOpen(true)}>
            {he.pmo.create}
          </Button>
        </Group>
      )}
      <GrantList grants={grants.data} />
      {pool.data && (
        <GrantWizard
          opened={open}
          available={pool.data.available}
          groups={summary.groups.map((g) => ({ ...g, total: g.total }))}
          models={summary.allowances.map((a) => a.model)}
          onClose={() => setOpen(false)}
          onSubmit={(body, reason, key) => write.mutateAsync({ body, reason, key })}
        />
      )}
    </Stack>
  );
}
