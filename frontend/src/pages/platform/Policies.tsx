import {
  Button,
  Group,
  Loader,
  NumberInput,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { useState } from 'react';
import {
  useDepartments,
  useDepartmentSummary,
  usePolicies,
  usePoliciesWrite,
} from '../../api/queries';
import type {
  DepartmentRecord,
  DepartmentSummary,
  Policies as PolicyValues,
  PoliciesIn,
} from '../../api/types';
import { ChangeSummaryDialog } from '../../components/ChangeSummaryDialog';
import { ErrorState } from '../../components/ErrorState';
import { usePeriod } from '../../components/PeriodSelector';
import { he } from '../../i18n/he';

const t = he.policies;
type Edits = Record<string, number | string>;

export default function Policies() {
  const policies = usePolicies();
  const departments = useDepartments();
  if (policies.isPending || departments.isPending) return <Loader aria-label={he.loading} />;
  if (policies.error || !policies.data)
    return <ErrorState error={policies.error} onRetry={policies.refetch} />;
  if (departments.error || !departments.data)
    return <ErrorState error={departments.error} onRetry={departments.refetch} />;
  return (
    <Stack>
      <Text size="sm" c="dimmed">
        {t.currentConfiguration}
      </Text>
      <PolicyEditor values={policies.data} />
      <Title order={4}>{t.groupFactors}</Title>
      {departments.data.map((d) => (
        <DepartmentFactors key={d.id} department={d} />
      ))}
    </Stack>
  );
}

function PolicyEditor({ values }: { values: PolicyValues }) {
  const { readOnly } = usePeriod();
  const write = usePoliciesWrite();
  const [edits, setEdits] = useState<Edits>({});
  const [review, setReview] = useState(false);
  const thresholds = { near: 0.8, full: 1, ...values.thresholds };
  const fields = [
    ...(['concurrency', 'rpm'] as const).flatMap((range) =>
      (['min', 'default', 'max'] as const).map((field) => ({
        key: `${range}-${field}`,
        label: `${t[range]}: ${t[field]}`,
        before: values[range][field],
        integer: true,
        min: 0,
        max: undefined as number | undefined,
      })),
    ),
    ...(['near', 'full'] as const).map((field) => ({
      key: `thresholds-${field}`,
      label: t[field],
      before: thresholds[field],
      integer: false,
      min: 0,
      max: 1,
    })),
    {
      key: 'overalloc_factor_default',
      label: t.defaultFactor,
      before: values.overalloc_factor_default,
      integer: false,
      min: 1,
      max: 2,
    },
  ];
  const value = (key: string, before?: number) => edits[key] ?? before ?? '';
  const changed = fields.filter((f) => edits[f.key] !== undefined && edits[f.key] !== f.before);
  const rangeValid = (range: 'concurrency' | 'rpm') => {
    const v = (field: 'min' | 'default' | 'max') =>
      Number(value(`${range}-${field}`, values[range][field]));
    return v('min') <= v('default') && v('default') <= v('max');
  };
  const valid =
    changed.length > 0 &&
    !readOnly &&
    rangeValid('concurrency') &&
    rangeValid('rpm') &&
    fields.every((f) => {
      const n = value(f.key, f.before);
      return (
        n !== '' &&
        Number.isFinite(Number(n)) &&
        Number(n) >= f.min &&
        (f.max === undefined || Number(n) <= f.max) &&
        (!f.integer || Number.isInteger(Number(n)))
      );
    });
  const body: PoliciesIn = {};
  for (const range of ['concurrency', 'rpm'] as const) {
    if (changed.some((f) => f.key.startsWith(`${range}-`)))
      body[range] = {
        min: Number(value(`${range}-min`, values[range].min)),
        default: Number(value(`${range}-default`, values[range].default)),
        max: Number(value(`${range}-max`, values[range].max)),
      };
  }
  if (changed.some((f) => f.key.startsWith('thresholds-')))
    body.thresholds = {
      near: Number(value('thresholds-near', thresholds.near)),
      full: Number(value('thresholds-full', thresholds.full)),
    };
  if (changed.some((f) => f.key === 'overalloc_factor_default'))
    body.overalloc_factor_default = Number(edits.overalloc_factor_default);
  return (
    <Paper withBorder p="md">
      <Stack>
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          {fields.map((f) => (
            <NumberInput
              key={f.key}
              label={f.label}
              data-testid={`policy-${f.key}`}
              value={value(f.key, f.before)}
              onChange={(n) => setEdits({ ...edits, [f.key]: n })}
              min={f.min}
              max={f.max}
              allowDecimal={!f.integer}
              step={f.integer ? 1 : 0.01}
              clampBehavior="none"
              readOnly={readOnly}
              hideControls
            />
          ))}
        </SimpleGrid>
        <Text size="sm" c="dimmed">
          {t.rangeHint}
        </Text>
        <Button data-testid="policies-review" disabled={!valid} onClick={() => setReview(true)}>
          {he.platformPage.review}
        </Button>
        <ChangeSummaryDialog
          opened={review}
          rows={changed.map((f) => ({
            key: f.key,
            label: f.label,
            before: f.before ?? '—',
            after: edits[f.key],
          }))}
          onClose={() => setReview(false)}
          onConfirm={async (reason, key) => {
            if (!valid) return;
            await write.mutateAsync({ body, reason, key });
            setEdits({});
          }}
        />
      </Stack>
    </Paper>
  );
}

function DepartmentFactors({ department }: { department: DepartmentRecord }) {
  const { current } = usePeriod();
  const summary = useDepartmentSummary(String(department.id), current);
  if (summary.isPending) return <Loader aria-label={he.loading} />;
  if (summary.error || !summary.data)
    return <ErrorState error={summary.error} onRetry={summary.refetch} />;
  return <GroupFactors name={department.name} id={department.id} groups={summary.data.groups} />;
}

function GroupFactors({
  name,
  id,
  groups,
}: {
  name: string;
  id: number;
  groups: DepartmentSummary['groups'];
}) {
  const { readOnly } = usePeriod();
  const write = usePoliciesWrite();
  const [edits, setEdits] = useState<Edits>({});
  const [review, setReview] = useState(false);
  const changed = groups.filter(
    (g) => edits[g.id] !== undefined && edits[g.id] !== g.overalloc_factor,
  );
  const valid =
    !readOnly &&
    changed.length > 0 &&
    changed.every((g) => {
      const n = edits[g.id];
      return n !== '' && Number.isFinite(Number(n)) && Number(n) >= 1 && Number(n) <= 2;
    });
  return (
    <Paper withBorder p="md" data-testid={`department-factors-${id}`}>
      <Stack>
        <Title order={5}>{name}</Title>
        <Group grow>
          {groups.map((g) => (
            <NumberInput
              key={g.id}
              label={g.name}
              data-testid={`group-factor-${g.id}`}
              value={edits[g.id] ?? g.overalloc_factor}
              onChange={(n) => setEdits({ ...edits, [g.id]: n })}
              min={1}
              max={2}
              step={0.01}
              decimalScale={2}
              clampBehavior="none"
              readOnly={readOnly}
              hideControls
            />
          ))}
        </Group>
        <Button disabled={!valid} onClick={() => setReview(true)}>
          {he.platformPage.review}
        </Button>
        <ChangeSummaryDialog
          opened={review}
          rows={changed.map((g) => ({
            key: String(g.id),
            label: g.name,
            before: g.overalloc_factor,
            after: edits[g.id],
          }))}
          onClose={() => setReview(false)}
          onConfirm={async (reason, key) => {
            if (!valid) return;
            await write.mutateAsync({
              body: {
                overalloc_factors: Object.fromEntries(
                  changed.map((g) => [g.id, Number(edits[g.id])]),
                ),
              },
              reason,
              key,
            });
            setEdits({});
          }}
        />
      </Stack>
    </Paper>
  );
}
