import { Badge, Card, Checkbox, Group, NativeSelect, Table, TextInput } from '@mantine/core';
import { useState, type ReactNode } from 'react';
import type { Grant } from '../api/types';
import { he } from '../i18n/he';
import { Credits } from './Credits';
import { DateText } from './DateText';
import { EmptyState } from './EmptyState';
import { Num } from './Num';
import { SyncBadge } from './SyncBadge';

const t = he.pmo;
const SOON_DAYS = 7;

/** §12.3 grant list with client-side filters; `actions` adds a column (used by later steps). */
export function GrantList({
  grants,
  actions,
  showUnshared = false,
}: {
  grants: Grant[];
  actions?: (g: Grant) => ReactNode;
  /** §12.5.2: the group view also shows the grant remainder. */
  showUnshared?: boolean;
}) {
  const [project, setProject] = useState('');
  const [group, setGroup] = useState('');
  const [status, setStatus] = useState('');
  const [soon, setSoon] = useState(false);

  const groups = [...new Map(grants.map((g) => [g.group_id, g.group_name]))];
  const rows = grants.filter(
    (g) =>
      g.project_ref.toLowerCase().includes(project.trim().toLowerCase()) &&
      (group === '' || String(g.group_id) === group) &&
      (status === '' || g.status === status) &&
      (!soon || (g.days_left !== null && g.days_left <= SOON_DAYS)),
  );
  return (
    <Card withBorder data-testid="grant-list">
      <Group align="flex-end" mb="sm">
        <TextInput
          label={t.project}
          data-testid="filter-project"
          value={project}
          onChange={(e) => setProject(e.currentTarget.value)}
        />
        <NativeSelect
          label={t.group}
          data-testid="filter-group"
          value={group}
          onChange={(e) => setGroup(e.currentTarget.value)}
          data={[
            { value: '', label: t.all },
            ...groups.map(([v, l]) => ({ value: String(v), label: l })),
          ]}
        />
        <NativeSelect
          label={t.status}
          data-testid="filter-status"
          value={status}
          onChange={(e) => setStatus(e.currentTarget.value)}
          data={[
            { value: '', label: t.all },
            ...Object.entries(t.statuses).map(([value, label]) => ({ value, label })),
          ]}
        />
        <Checkbox
          label={t.expiringSoon}
          data-testid="filter-soon"
          checked={soon}
          onChange={(e) => setSoon(e.currentTarget.checked)}
        />
      </Group>
      {rows.length === 0 ? (
        <EmptyState message={t.empty} />
      ) : (
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t.project}</Table.Th>
              <Table.Th>{t.group}</Table.Th>
              <Table.Th>{t.amount}</Table.Th>
              <Table.Th>{t.model}</Table.Th>
              <Table.Th>{t.start}</Table.Th>
              <Table.Th>{t.expiry}</Table.Th>
              <Table.Th>{t.shared}</Table.Th>
              {showUnshared && <Table.Th>{he.shares.unshared}</Table.Th>}
              <Table.Th>{t.used}</Table.Th>
              <Table.Th>{t.returned}</Table.Th>
              <Table.Th>{t.status}</Table.Th>
              {actions && <Table.Th />}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((g) => (
              <Table.Tr key={g.id} data-testid={`grant-row-${g.id}`} data-project={g.project_ref}>
                <Table.Td>
                  <Num>{g.project_ref}</Num>
                </Table.Td>
                <Table.Td>{g.group_name}</Table.Td>
                <Table.Td>
                  <Credits value={g.amount} />
                </Table.Td>
                <Table.Td>{g.model ? <Num>{g.model}</Num> : t.allModels}</Table.Td>
                <Table.Td>
                  <DateText value={g.starts_at} />
                </Table.Td>
                <Table.Td>
                  <DateText value={g.expires_at} />
                  {g.days_left !== null && (
                    <>
                      {' '}
                      (<Num>{g.days_left}</Num>)
                    </>
                  )}
                </Table.Td>
                <Table.Td>
                  <Credits value={g.shared} />
                </Table.Td>
                {showUnshared && (
                  <Table.Td data-testid={`grant-unshared-${g.id}`}>
                    <Credits value={g.unshared} />
                  </Table.Td>
                )}
                <Table.Td>
                  <Credits value={g.used} />
                </Table.Td>
                <Table.Td>
                  <Credits value={g.returned} />
                </Table.Td>
                <Table.Td>
                  <Group gap={4} wrap="nowrap">
                    <Badge variant="light" color={g.status === 'active' ? 'green' : 'gray'}>
                      {t.statuses[g.status]}
                    </Badge>
                    <SyncBadge status={g.sync_status} />
                  </Group>
                </Table.Td>
                {actions && <Table.Td>{actions(g)}</Table.Td>}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Card>
  );
}
