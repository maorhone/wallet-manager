import { ActionIcon, Anchor, Badge, Drawer, Group, Indicator, Stack, Text } from '@mantine/core';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMarkRead, useNotifications, useUnreadNotifications } from '../api/queries';
import type { AppNotification } from '../api/types';
import { he } from '../i18n/he';
import { DateText } from './DateText';
import { EmptyState } from './EmptyState';

/** Where a notification's scope lives in the UI; grants point at the receiving group's Grants tab. */
export function notificationLink(n: AppNotification): string | null {
  if (n.kind === 'employee_80' || n.kind === 'employee_100') return '/wallet';
  if (n.kind === 'pending_placement')
    return n.scope_id != null ? `/department/${n.scope_id}/pending` : '/platform';
  const groupId =
    n.scope_kind === 'group' ? n.scope_id : (n.payload.group_id as number | undefined);
  switch (n.scope_kind) {
    case 'grant':
      return groupId != null ? `/group/${groupId}/grants` : null;
    case 'group':
      return `/group/${n.scope_id}/overview`;
    case 'department':
      return `/department/${n.scope_id}/overview`;
    case 'employee':
      return '/wallet';
    default:
      return null;
  }
}

function detail(n: AppNotification): string {
  const p = n.payload;
  return typeof p.project_ref === 'string' ? p.project_ref : '';
}

/** Header bell: unread count and a drawer with the list (FR-N1). */
export function NotificationsBell() {
  const [open, setOpen] = useState(false);
  const { data } = useNotifications();
  const unreadList = useUnreadNotifications();
  const markRead = useMarkRead();
  const unread = data?.unread_count ?? 0;
  // The recent list is capped; unread alerts older than the cap come from the unread view.
  const items = [
    ...new Map(
      [...(unreadList.data?.items ?? []), ...(data?.items ?? [])].map((n) => [n.id, n]),
    ).values(),
  ].sort((a, b) => b.id - a.id);
  return (
    <>
      <Indicator
        disabled={unread === 0}
        label={<span data-testid="bell-count">{unread}</span>}
        size={16}
        offset={3}
      >
        <ActionIcon
          variant="default"
          aria-label={he.notifications.title}
          data-testid="bell"
          onClick={() => setOpen(true)}
        >
          <span aria-hidden="true">🔔</span>
        </ActionIcon>
      </Indicator>
      <Drawer
        opened={open}
        onClose={() => setOpen(false)}
        title={he.notifications.title}
        position="left"
      >
        <Stack data-testid="notification-list">
          {!items.length && <EmptyState />}
          {items.map((n) => {
            const link = notificationLink(n);
            return (
              <Stack
                key={n.id}
                gap={2}
                data-testid={`notification-${n.id}`}
                data-kind={n.kind}
                data-read={n.read_at ? 'true' : 'false'}
                style={{ opacity: n.read_at ? 0.6 : 1 }}
              >
                <Group gap="xs">
                  <Text fw={n.read_at ? 400 : 700}>
                    {he.notifications.kinds[n.kind] ?? n.kind}
                    {detail(n) && ` — ${detail(n)}`}
                  </Text>
                  {n.critical && (
                    <Badge color="red" variant="light">
                      {he.notifications.critical}
                    </Badge>
                  )}
                </Group>
                <Text size="xs" c="dimmed">
                  <DateText value={n.created_at} />
                </Text>
                <Group gap="md">
                  {link && (
                    <Anchor component={Link} to={link} onClick={() => setOpen(false)} size="sm">
                      {he.notifications.open}
                    </Anchor>
                  )}
                  {!n.read_at && (
                    <Anchor
                      component="button"
                      size="sm"
                      data-testid="notification-read"
                      onClick={() => markRead.mutate(n.id)}
                    >
                      {he.notifications.markRead}
                    </Anchor>
                  )}
                </Group>
              </Stack>
            );
          })}
        </Stack>
      </Drawer>
    </>
  );
}
