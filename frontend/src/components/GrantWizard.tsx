import { Button, Group, Modal, NativeSelect, Stack, Stepper, Text, TextInput } from '@mantine/core';
import { useState } from 'react';
import type { ApiState, GrantIn } from '../api/types';
import { he } from '../i18n/he';
import { ChangeSummaryDialog } from './ChangeSummaryDialog';
import { Credits } from './Credits';
import { Num } from './Num';
import { Pct } from './Pct';
import { API_STATE, StateBadge } from './StateBadge';

const t = he.pmo;

export interface WizardGroup {
  id: number;
  name: string;
  base: number;
  total: number;
  used: number;
  percent: number;
  state: ApiState;
}

interface Props {
  opened: boolean;
  /** Pool available now; the amount step blocks anything above it. */
  available: number;
  groups: WizardGroup[];
  /** Models of the department allowances, for the optional model. */
  models: string[];
  onClose: () => void;
  onSubmit: (body: GrantIn, reason: string, key: string) => Promise<unknown>;
}

const today = () => new Date().toLocaleDateString('en-CA');
/** Local start of day / end of day as an aware ISO instant. */
const startIso = (d: string) => new Date(`${d}T00:00:00`).toISOString();
const endIso = (d: string) => new Date(`${d}T23:59:59`).toISOString();

/** §12.5.3 create wizard: project → group → amount and model → dates → summary with the reason. */
export function GrantWizard({ opened, available, groups, models, onClose, onSubmit }: Props) {
  const [step, setStep] = useState(0);
  const [project, setProject] = useState('');
  const [purpose, setPurpose] = useState('');
  const [groupId, setGroupId] = useState('');
  const [amount, setAmount] = useState('');
  const [model, setModel] = useState('');
  const [start, setStart] = useState(today());
  const [expiry, setExpiry] = useState('');
  const [summary, setSummary] = useState(false);

  const group = groups.find((g) => String(g.id) === groupId);
  const value = amount.trim() === '' ? NaN : Number(amount);
  const amountError = Number.isNaN(value)
    ? null
    : value <= 0
      ? he.empty
      : value > available
        ? t.aboveAvailable
        : null;
  const datesError =
    start === '' || expiry === ''
      ? he.empty
      : expiry < start
        ? t.expiryBeforeStart
        : expiry < today()
          ? t.expiryPast
          : null;
  const valid = [
    project.trim() !== '' && purpose.trim() !== '',
    group !== undefined,
    !Number.isNaN(value) && amountError === null,
    datesError === null,
  ];

  const body = (): GrantIn => ({
    group_id: Number(groupId),
    amount: value,
    model: model || null,
    starts_at: startIso(start),
    expires_at: endIso(expiry),
    project_ref: project.trim(),
    purpose: purpose.trim(),
  });

  return (
    <Modal opened={opened} onClose={onClose} title={t.create} size="lg">
      <Stack data-testid="grant-wizard">
        <Stepper active={step} size="sm">
          <Stepper.Step label={t.steps.project} />
          <Stepper.Step label={t.steps.group} />
          <Stepper.Step label={t.steps.amount} />
          <Stepper.Step label={t.steps.dates} />
        </Stepper>
        {step === 0 && (
          <>
            <TextInput
              label={t.project}
              data-testid="wizard-project"
              value={project}
              onChange={(e) => setProject(e.currentTarget.value)}
            />
            <TextInput
              label={t.purpose}
              data-testid="wizard-purpose"
              value={purpose}
              onChange={(e) => setPurpose(e.currentTarget.value)}
            />
          </>
        )}
        {step === 1 && (
          <>
            <NativeSelect
              label={t.group}
              data-testid="wizard-group"
              value={groupId}
              onChange={(e) => setGroupId(e.currentTarget.value)}
              data={[
                { value: '', label: '—' },
                ...groups.map((g) => ({ value: String(g.id), label: g.name })),
              ]}
            />
            {group && (
              <Group gap="xs" data-testid="wizard-group-state">
                <Text size="sm">{t.groupState}:</Text>
                <StateBadge state={API_STATE[group.state] ?? 'healthy'} />
                <Text size="sm">
                  <Credits value={group.used} /> / <Credits value={group.total} /> (
                  <Pct value={group.percent * 100} />)
                </Text>
              </Group>
            )}
          </>
        )}
        {step === 2 && (
          <>
            <Text size="sm" data-testid="wizard-available">
              {t.available}: <Credits value={available} />
            </Text>
            <TextInput
              label={t.amount}
              data-testid="wizard-amount"
              inputMode="decimal"
              value={amount}
              error={amountError && <span data-testid="wizard-amount-error">{amountError}</span>}
              onChange={(e) => setAmount(e.currentTarget.value)}
            />
            <NativeSelect
              label={t.model}
              data-testid="wizard-model"
              value={model}
              onChange={(e) => setModel(e.currentTarget.value)}
              data={[{ value: '', label: t.allModels }, ...models]}
            />
          </>
        )}
        {step === 3 && (
          <>
            <TextInput
              type="date"
              label={t.start}
              data-testid="wizard-start"
              value={start}
              onChange={(e) => setStart(e.currentTarget.value)}
            />
            <TextInput
              type="date"
              label={t.expiry}
              data-testid="wizard-expiry"
              value={expiry}
              error={
                expiry !== '' &&
                datesError && <span data-testid="wizard-dates-error">{datesError}</span>
              }
              onChange={(e) => setExpiry(e.currentTarget.value)}
            />
          </>
        )}
        <Group justify="space-between">
          <Button variant="default" disabled={step === 0} onClick={() => setStep(step - 1)}>
            {t.back}
          </Button>
          <Button
            data-testid="wizard-next"
            disabled={!valid[step]}
            onClick={() => (step < 3 ? setStep(step + 1) : setSummary(true))}
          >
            {step < 3 ? t.next : t.summary}
          </Button>
        </Group>
      </Stack>
      <ChangeSummaryDialog
        opened={summary}
        rows={[
          { key: 'project', label: t.project, before: '—', after: <Num>{project.trim()}</Num> },
          { key: 'group', label: t.group, before: '—', after: group?.name },
          { key: 'amount', label: t.amount, before: '—', after: <Credits value={value} /> },
          { key: 'model', label: t.model, before: '—', after: model || t.allModels },
          { key: 'start', label: t.start, before: '—', after: <Num>{start}</Num> },
          { key: 'expiry', label: t.expiry, before: '—', after: <Num>{expiry}</Num> },
        ]}
        effect={
          <>
            {t.available}: <Credits value={available} /> → <Credits value={available - value} />.{' '}
            {t.groupHeadNotified}
          </>
        }
        onClose={() => setSummary(false)}
        onConfirm={async (reason, key) => {
          await onSubmit(body(), reason, key);
          setStep(0);
          setProject('');
          setPurpose('');
          setGroupId('');
          setAmount('');
          setModel('');
          setExpiry('');
          onClose();
        }}
      />
    </Modal>
  );
}
