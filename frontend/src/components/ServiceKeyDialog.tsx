import { Button, Modal, NumberInput, Stack, TextInput } from '@mantine/core';
import { useState } from 'react';
import type { ServiceKeyIn } from '../api/types';
import { he } from '../i18n/he';
import { ChangeSummaryDialog } from './ChangeSummaryDialog';
import { Credits } from './Credits';

const t = he.serviceKeys;

interface Props {
  ownerKind: 'group' | 'system';
  ownerId: number;
  onClose: () => void;
  onConfirm: (body: ServiceKeyIn, reason: string, key: string) => Promise<unknown>;
  parent?: { allocated: number; base: number };
}

/** FR-K3/FR-K4 create form: alias, budget and limits, then the usual change summary and reason. */
export function ServiceKeyDialog({ ownerKind, ownerId, onClose, onConfirm, parent }: Props) {
  const [alias, setAlias] = useState('');
  const [maxBudget, setMaxBudget] = useState<number>(0);
  const [maxParallel, setMaxParallel] = useState<number>(4);
  const [rpmLimit, setRpmLimit] = useState<number>(120);
  const [modelsText, setModelsText] = useState('');
  const [review, setReview] = useState(false);

  const models = modelsText
    .split(',')
    .map((m) => m.trim())
    .filter((m) => m !== '');
  const body: ServiceKeyIn = {
    owner_kind: ownerKind,
    owner_id: ownerId,
    alias: alias.trim(),
    max_budget: maxBudget,
    max_parallel: maxParallel,
    rpm_limit: rpmLimit,
    models,
  };
  const valid = body.alias !== '' && maxBudget > 0 && maxParallel > 0 && rpmLimit > 0;

  return (
    <Modal opened onClose={onClose} title={t.create}>
      <Stack data-testid="service-key-dialog">
        <TextInput
          label={t.alias}
          data-testid="service-key-alias"
          value={alias}
          onChange={(e) => setAlias(e.currentTarget.value)}
          required
        />
        <NumberInput
          label={t.budget}
          data-testid="service-key-budget"
          min={0}
          value={maxBudget}
          onChange={(v) => setMaxBudget(Number(v) || 0)}
        />
        <NumberInput
          label={t.concurrency}
          data-testid="service-key-concurrency"
          min={1}
          value={maxParallel}
          onChange={(v) => setMaxParallel(Number(v) || 0)}
        />
        <NumberInput
          label={t.rpm}
          data-testid="service-key-rpm"
          min={1}
          value={rpmLimit}
          onChange={(v) => setRpmLimit(Number(v) || 0)}
        />
        <TextInput
          label={t.models}
          description={t.allModels}
          placeholder="model-a, model-b"
          data-testid="service-key-models"
          value={modelsText}
          onChange={(e) => setModelsText(e.currentTarget.value)}
        />
        <Button data-testid="service-key-review" disabled={!valid} onClick={() => setReview(true)}>
          {he.groupPage.review}
        </Button>
      </Stack>
      <ChangeSummaryDialog
        opened={review}
        rows={[
          { key: 'alias', label: t.alias, before: '', after: body.alias },
          { key: 'budget', label: t.budget, before: '', after: <Credits value={maxBudget} /> },
          { key: 'concurrency', label: t.concurrency, before: '', after: maxParallel },
          { key: 'rpm', label: t.rpm, before: '', after: rpmLimit },
          ...(parent
            ? [
                {
                  key: 'parent',
                  label: t.parentAllocated,
                  before: <Credits value={parent.allocated} />,
                  after: <Credits value={parent.allocated + maxBudget} />,
                },
              ]
            : []),
          {
            key: 'models',
            label: t.models,
            before: '',
            after: models.length > 0 ? models.join(', ') : t.allModels,
          },
        ]}
        onClose={() => setReview(false)}
        onConfirm={async (reason, key) => {
          await onConfirm(body, reason, key);
          onClose();
        }}
      />
    </Modal>
  );
}
