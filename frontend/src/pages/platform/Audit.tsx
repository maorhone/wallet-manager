import { Button, Loader, SimpleGrid, Stack, TextInput } from '@mantine/core';
import { useState } from 'react';
import { useAuditSearch } from '../../api/queries';
import type { AuditFilters } from '../../api/types';
import { ErrorState } from '../../components/ErrorState';
import { HistoryTimeline } from '../../components/HistoryTimeline';
import { he } from '../../i18n/he';

const t = he.auditSearch;

export default function Audit() {
  const [form, setForm] = useState({ entity: '', id: '', actor: '', from: '', to: '' });
  const [filters, setFilters] = useState<AuditFilters | null>(null);
  const results = useAuditSearch(filters);
  return (
    <Stack>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const next = {
            entity: form.entity.trim(),
            id: form.id.trim(),
            actor: form.actor.trim(),
            from: form.from ? new Date(form.from).toISOString() : '',
            to: form.to ? new Date(form.to).toISOString() : '',
          };
          if (JSON.stringify(next) === JSON.stringify(filters)) void results.refetch();
          else setFilters(next);
        }}
      >
        <Stack>
          <SimpleGrid cols={{ base: 1, sm: 2, lg: 5 }}>
            {(['entity', 'id', 'actor', 'from', 'to'] as const).map((field) => (
              <TextInput
                key={field}
                label={t[field]}
                type={field === 'from' || field === 'to' ? 'datetime-local' : 'text'}
                description={field === 'from' || field === 'to' ? t.localTime : undefined}
                min={field === 'to' ? form.from || undefined : undefined}
                value={form[field]}
                onChange={(event) => setForm({ ...form, [field]: event.currentTarget.value })}
                dir="ltr"
              />
            ))}
          </SimpleGrid>
          <Button type="submit" disabled={results.isFetching}>
            {t.search}
          </Button>
        </Stack>
      </form>
      {results.isFetching && <Loader aria-label={he.loading} />}
      {results.error && <ErrorState error={results.error} onRetry={results.refetch} />}
      {results.data && <HistoryTimeline entries={results.data} />}
    </Stack>
  );
}
