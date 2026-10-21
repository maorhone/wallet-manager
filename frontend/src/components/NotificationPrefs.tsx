import { Card, Checkbox, Stack, Title } from '@mantine/core';
import { useNotificationPrefs } from '../api/queries';
import { he } from '../i18n/he';
import { ErrorState } from './ErrorState';

/** Checked = receive. Critical kinds are always on and disabled (FR-N2). */
export function NotificationPrefs() {
  const { prefs, error, set } = useNotificationPrefs();
  if (error) return <ErrorState error={error} />;
  return (
    <Card withBorder data-testid="notification-prefs">
      <Title order={5} mb="xs">
        {he.notifications.prefs}
      </Title>
      <Stack gap={4}>
        {prefs?.map((p) => (
          <Checkbox
            key={p.kind}
            data-testid={`pref-${p.kind}`}
            label={he.notifications.kinds[p.kind] ?? p.kind}
            description={p.critical ? he.notifications.critical : undefined}
            checked={!p.muted}
            disabled={p.critical}
            onChange={(e) => set.mutate({ kind: p.kind, muted: !e.currentTarget.checked })}
          />
        ))}
      </Stack>
    </Card>
  );
}
