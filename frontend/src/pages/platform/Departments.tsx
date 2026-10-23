import {
  Anchor,
  Button,
  Group,
  Loader,
  Modal,
  NativeSelect,
  NumberInput,
  Stack,
  Table,
  Text,
  TextInput,
} from '@mantine/core';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  useCreateDepartment,
  useCreateGroup,
  useDepartments,
  useDepartmentSummary,
  useDepartmentWrite,
} from '../../api/queries';
import type { DepartmentRecord, GroupIn } from '../../api/types';
import { ChangeSummaryDialog, type SummaryRow } from '../../components/ChangeSummaryDialog';
import { Credits } from '../../components/Credits';
import { EmptyState } from '../../components/EmptyState';
import { ErrorState } from '../../components/ErrorState';
import { usePeriod } from '../../components/PeriodSelector';
import { RoleBindingsEditor } from '../../components/RoleBindingsEditor';
import { SyncBadge } from '../../components/SyncBadge';
import { he } from '../../i18n/he';

const t = he.platformPage;

export default function Departments() {
  const departments = useDepartments();
  const { readOnly } = usePeriod();
  const [creating, setCreating] = useState(false);
  if (departments.isPending) return <Loader aria-label={he.loading} />;
  if (departments.error || !departments.data)
    return <ErrorState error={departments.error} onRetry={departments.refetch} />;
  return (
    <Stack>
      <Group justify="space-between">
        <Text c="dimmed" size="sm">
          {t.currentConfiguration}
        </Text>
        <Button disabled={readOnly} onClick={() => setCreating(true)}>
          {t.createDepartment}
        </Button>
      </Group>
      {departments.data.length === 0 ? (
        <EmptyState />
      ) : (
        <Table.ScrollContainer minWidth={800}>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{he.department}</Table.Th>
                <Table.Th>{t.budget}</Table.Th>
                <Table.Th>{t.budgetPeriod}</Table.Th>
                <Table.Th>{he.group}</Table.Th>
                <Table.Th>{he.memberActions.section}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {departments.data.map((d) => (
                <DepartmentRow key={d.id} department={d} />
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
      {creating && <DepartmentDialog onClose={() => setCreating(false)} />}
    </Stack>
  );
}

function DepartmentRow({ department: d }: { department: DepartmentRecord }) {
  const { current, period, readOnly } = usePeriod();
  const search = period === current ? '' : `?period=${period}`;
  const summary = useDepartmentSummary(String(d.id), current);
  const [dialog, setDialog] = useState<'budget' | 'group' | 'bindings' | null>(null);
  const staff = summary.data?.groups.find((g) => g.kind === 'department_staff');
  return (
    <Table.Tr data-testid="platform-department">
      <Table.Td>
        <Group gap="xs">
          <Anchor component={Link} to={`/department/${d.id}/overview${search}`}>
            {d.name}
          </Anchor>
          {summary.data && (
            <span data-testid="department-sync">
              <SyncBadge status={summary.data.split.pmo_reserve_sync_status} />
            </span>
          )}
        </Group>
      </Table.Td>
      <Table.Td>
        <Credits value={d.budget} />
      </Table.Td>
      <Table.Td>
        <Text dir="ltr">{d.budget_period}</Text>
      </Table.Td>
      <Table.Td>
        {summary.isPending && <Loader size="xs" aria-label={he.loading} />}
        {summary.error && <ErrorState error={summary.error} onRetry={summary.refetch} />}
        <Stack gap="xs">
          {summary.data?.groups.map((g) => (
            <Group key={g.id} gap="xs" data-testid="platform-group">
              <Anchor component={Link} to={`/group/${g.id}/overview${search}`}>
                {g.name}
              </Anchor>
              <span data-testid="group-base">
                <Credits value={g.base} />
              </span>
              <SyncBadge status={g.sync_status} />
            </Group>
          ))}
          {staff && (
            <Anchor component={Link} to={`/group/${staff.id}/members${search}`}>
              {t.personalValues}
            </Anchor>
          )}
        </Stack>
      </Table.Td>
      <Table.Td>
        <Stack gap="xs" align="flex-start">
          <Button
            size="compact-sm"
            variant="default"
            disabled={readOnly}
            onClick={() => setDialog('budget')}
          >
            {t.editBudget}
          </Button>
          <Button
            size="compact-sm"
            variant="default"
            disabled={readOnly}
            onClick={() => setDialog('group')}
          >
            {t.createGroup}
          </Button>
          <Button
            size="compact-sm"
            variant="default"
            disabled={readOnly}
            onClick={() => setDialog('bindings')}
          >
            {t.bindings}
          </Button>
        </Stack>
        {dialog === 'budget' && <DepartmentDialog department={d} onClose={() => setDialog(null)} />}
        {dialog === 'group' && (
          <GroupDialog departmentId={d.id} hasStaff={!!staff} onClose={() => setDialog(null)} />
        )}
        <Modal
          opened={dialog === 'bindings'}
          onClose={() => setDialog(null)}
          title={`${t.bindings}: ${d.name}`}
          size="lg"
        >
          {dialog === 'bindings' && (
            <Stack>
              <RoleBindingsEditor
                scope={{ scope_kind: 'department', scope_id: d.id }}
                readOnly={readOnly}
              />
              <Button variant="default" onClick={() => setDialog(null)}>
                {t.closeBindings}
              </Button>
            </Stack>
          )}
        </Modal>
      </Table.Td>
    </Table.Tr>
  );
}

function DepartmentDialog({
  department: d,
  onClose,
}: {
  department?: DepartmentRecord;
  onClose: () => void;
}) {
  const create = useCreateDepartment();
  const write = useDepartmentWrite();
  const { readOnly } = usePeriod();
  const [name, setName] = useState('');
  const [orgId, setOrgId] = useState('');
  const [dn, setDn] = useState('');
  const [budget, setBudget] = useState<number | string>(d?.budget ?? 0);
  const [period, setPeriod] = useState(d?.budget_period ?? '1mo');
  const [review, setReview] = useState(false);
  const valid =
    !readOnly &&
    budget !== '' &&
    /^\d+(s|m|h|d|mo)$/.test(period) &&
    (d ? budget !== d.budget || period !== d.budget_period : !!name.trim() && !!orgId.trim());
  const rows: SummaryRow[] = [
    {
      key: 'budget',
      label: t.budget,
      before: d ? <Credits value={d.budget} /> : '—',
      after: <Credits value={Number(budget)} />,
    },
    { key: 'period', label: t.budgetPeriod, before: d?.budget_period ?? '—', after: period },
    ...(!d
      ? [
          { key: 'name', label: t.departmentName, before: '—', after: name.trim() },
          { key: 'org', label: t.orgId, before: '—', after: orgId.trim() },
          { key: 'dn', label: t.adGroup, before: '—', after: dn.trim() || '—' },
        ]
      : []),
  ];
  return (
    <>
      <Modal opened={!review} onClose={onClose} title={d ? t.editBudget : t.createDepartment}>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (valid) setReview(true);
          }}
        >
          <Stack>
            {!d && (
              <>
                <TextInput
                  label={t.departmentName}
                  value={name}
                  onChange={(e) => setName(e.currentTarget.value)}
                  required
                />
                <TextInput
                  label={t.orgId}
                  value={orgId}
                  onChange={(e) => setOrgId(e.currentTarget.value)}
                  required
                  dir="ltr"
                />
                <TextInput
                  label={t.adGroup}
                  value={dn}
                  onChange={(e) => setDn(e.currentTarget.value)}
                  dir="ltr"
                />
              </>
            )}
            <NumberInput
              label={t.budget}
              value={budget}
              onChange={setBudget}
              min={0}
              allowNegative={false}
              decimalScale={2}
              thousandSeparator=","
              required
            />
            <TextInput
              label={t.budgetPeriod}
              description={t.periodHint}
              value={period}
              onChange={(e) => setPeriod(e.currentTarget.value)}
              required
              dir="ltr"
            />
            <Button type="submit" data-testid="platform-review" disabled={!valid}>
              {t.review}
            </Button>
          </Stack>
        </form>
      </Modal>
      <ChangeSummaryDialog
        opened={review}
        rows={rows}
        onClose={() => setReview(false)}
        onConfirm={async (reason, key) => {
          if (readOnly) return;
          const values = { budget: Number(budget), budget_period: period };
          if (d)
            await write.mutateAsync({
              path: `/departments/${d.id}/budget`,
              body: values,
              reason,
              key,
            });
          else
            await create.mutateAsync({
              body: {
                ...values,
                name: name.trim(),
                litellm_org_id: orgId.trim(),
                ad_group_dn: dn.trim() || null,
              },
              reason,
              key,
            });
          onClose();
        }}
      />
    </>
  );
}

function GroupDialog({
  departmentId,
  hasStaff,
  onClose,
}: {
  departmentId: number;
  hasStaff: boolean;
  onClose: () => void;
}) {
  const create = useCreateGroup(departmentId);
  const { readOnly } = usePeriod();
  const [name, setName] = useState('');
  const [teamId, setTeamId] = useState('');
  const [dn, setDn] = useState('');
  const [kind, setKind] = useState<GroupIn['kind']>('regular');
  const [review, setReview] = useState(false);
  const valid =
    !readOnly && !!name.trim() && !!teamId.trim() && !(hasStaff && kind === 'department_staff');
  return (
    <>
      <Modal opened={!review} onClose={onClose} title={t.createGroup}>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (valid) setReview(true);
          }}
        >
          <Stack>
            <TextInput
              label={t.groupName}
              value={name}
              onChange={(e) => setName(e.currentTarget.value)}
              required
            />
            <TextInput
              label={t.teamId}
              value={teamId}
              onChange={(e) => setTeamId(e.currentTarget.value)}
              required
              dir="ltr"
            />
            <TextInput
              label={t.adGroup}
              value={dn}
              onChange={(e) => setDn(e.currentTarget.value)}
              dir="ltr"
            />
            <NativeSelect
              label={t.kind}
              value={kind}
              onChange={(e) => setKind(e.currentTarget.value as GroupIn['kind'])}
              data={[
                { value: 'regular', label: t.regular },
                { value: 'department_staff', label: t.staff, disabled: hasStaff },
              ]}
            />
            <Text size="sm">{t.zeroBase}</Text>
            <Button type="submit" data-testid="platform-review" disabled={!valid}>
              {t.review}
            </Button>
          </Stack>
        </form>
      </Modal>
      <ChangeSummaryDialog
        opened={review}
        rows={[
          { key: 'name', label: t.groupName, before: '—', after: name.trim() },
          { key: 'team', label: t.teamId, before: '—', after: teamId.trim() },
          { key: 'dn', label: t.adGroup, before: '—', after: dn.trim() || '—' },
          {
            key: 'kind',
            label: t.kind,
            before: '—',
            after: kind === 'department_staff' ? t.staff : t.regular,
          },
          { key: 'base', label: he.groupPage.base, before: '—', after: <Credits value={0} /> },
        ]}
        onClose={() => setReview(false)}
        onConfirm={async (reason, key) => {
          if (readOnly) return;
          await create.mutateAsync({
            body: {
              name: name.trim(),
              litellm_team_id: teamId.trim(),
              ad_group_dn: dn.trim() || null,
              kind,
            },
            reason,
            key,
          });
          onClose();
        }}
      />
    </>
  );
}
