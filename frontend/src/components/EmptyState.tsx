import { Center, Text } from '@mantine/core';
import { he } from '../i18n/he';

export function EmptyState({ message = he.empty }: { message?: string }) {
  return (
    <Center p="xl" data-testid="empty-state">
      <Text c="dimmed">{message}</Text>
    </Center>
  );
}
