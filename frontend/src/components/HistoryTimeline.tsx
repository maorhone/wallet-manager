import { Badge, Card, Group, Stack, Text, Timeline } from '@mantine/core';
import type { AuditEntry } from '../api/types';
import { he } from '../i18n/he';
import { EmptyState } from './EmptyState';
import { Num } from './Num';

const show = (v: unknown) => (typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v));

/** `key: before → after` for every field that changed. */
function changes(e: AuditEntry): [string, unknown, unknown][] {
  const before = e.before ?? {};
  const after = e.after ?? {};
  return Object.keys(after)
    .filter((k) => JSON.stringify(before[k]) !== JSON.stringify(after[k]))
    .map((k) => [k, before[k] ?? '–', after[k]]);
}

/** §12.3 history timeline: newest first, actor, reason, before → after, LiteLLM call status. */
export function HistoryTimeline({ entries }: { entries: AuditEntry[] }) {
  if (entries.length === 0) return <EmptyState message={he.history.empty} />;
  return (
    <Timeline bulletSize={14} data-testid="history-timeline">
      {entries.map((e) => (
        <Timeline.Item
          key={e.id}
          title={
            <Group gap="xs">
              <Num>{new Date(e.at).toLocaleString('he-IL')}</Num>
              <Text span fw={600}>
                {he.history.actor}: <Num>{e.actor_upn}</Num>
                {e.actor_role && ` (${he.roles[e.actor_role] ?? e.actor_role})`}
              </Text>
            </Group>
          }
        >
          <Card withBorder p="xs" mt={4} data-testid="history-entry">
            <Stack gap={2}>
              <Text size="sm">
                <Num>
                  {e.entity} {e.entity_id}
                </Num>
              </Text>
              {changes(e).map(([k, b, a]) => (
                <Text size="sm" key={k}>
                  <Num>
                    {k}: {show(b)} → {show(a)}
                  </Num>
                </Text>
              ))}
              <Text size="sm">
                {he.history.reason}: {e.reason}
              </Text>
              {e.litellm_calls.map((c, i) => (
                <Group gap={4} key={i}>
                  <Text size="xs" c="dimmed">
                    {he.history.calls}: <Num>{c.endpoint}</Num>
                  </Text>
                  <Badge size="xs" variant="light" color={c.status === 'failed' ? 'red' : 'gray'}>
                    {c.status}
                  </Badge>
                </Group>
              ))}
            </Stack>
          </Card>
        </Timeline.Item>
      ))}
    </Timeline>
  );
}
