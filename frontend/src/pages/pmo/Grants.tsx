import { Button, Group, Loader, Stack } from '@mantine/core';
import { useEffect, useState } from 'react';
import { useGrantAction, useGrantWrite, useGrants, useMe, usePmoPool } from '../../api/queries';
import type { DepartmentSummary, Grant } from '../../api/types';
import { ErrorState } from '../../components/ErrorState';
import { GrantEditDialog, type GrantEditMode } from '../../components/GrantEditDialog';
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
  const act = useGrantAction();
  const [open, setOpen] = useState(false);
  const [edit, setEdit] = useState<{ g: Grant; mode: GrantEditMode } | null>(null);
  const { data: me } = useMe();
  const canCreate = !!pool.data?.actions.create_grant && !summary.read_only;
  useEffect(() => setOpen(false), [period]);
  if (grants.isPending) return <Loader aria-label={he.loading} />;
  if (grants.error || !grants.data)
    return <ErrorState error={grants.error} onRetry={grants.refetch} />;
  return (
    <Stack>
      {canCreate && (
        <Group>
          <Button data-testid="create-grant" onClick={() => setOpen(true)}>
            {he.pmo.create}
          </Button>
        </Group>
      )}
      <GrantList
        grants={grants.data}
        actions={(g) =>
          g.actions.edit || g.actions.close ? (
            <Group gap={4} wrap="nowrap">
              {g.actions.edit && (
                <>
                  <Button
                    size="compact-xs"
                    data-testid={`extend-${g.id}`}
                    onClick={() => setEdit({ g, mode: 'extend' })}
                  >
                    {he.pmo.extend}
                  </Button>
                  <Button
                    size="compact-xs"
                    data-testid={`increase-${g.id}`}
                    onClick={() => setEdit({ g, mode: 'increase' })}
                  >
                    {he.pmo.increase}
                  </Button>
                </>
              )}
              {g.actions.close && (
                <Button
                  size="compact-xs"
                  color="red"
                  data-testid={`close-${g.id}`}
                  onClick={() => setEdit({ g, mode: 'close' })}
                >
                  {he.pmo.closeEarly}
                </Button>
              )}
            </Group>
          ) : null
        }
      />
      {edit && (
        <GrantEditDialog
          grant={edit.g}
          mode={edit.mode}
          onClose={() => setEdit(null)}
          onConfirm={(patch, reason, key) => act.mutateAsync({ id: edit.g.id, patch, reason, key })}
        />
      )}
      {pool.data && canCreate && (
        <GrantWizard
          opened={open}
          available={pool.data.available}
          groups={summary.groups.map((g) => ({ ...g, total: g.total }))}
          models={summary.allowances.map((a) => a.model)}
          timezone={me?.timezone ?? 'Asia/Jerusalem'}
          onClose={() => setOpen(false)}
          onSubmit={(body, reason, key) => write.mutateAsync({ body, reason, key })}
        />
      )}
    </Stack>
  );
}
