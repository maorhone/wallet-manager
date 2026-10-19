import { Card, Stack, Text, Title } from '@mantine/core';
import type { Owner } from '../api/types';
import { he } from '../i18n/he';

/** "Who can change what": the people holding the owning role for my group (§12.5.1). */
export function OwnersBox({ owner }: { owner: Owner }) {
  const names = owner.people.map((p) => p.display_name).join(', ');
  return (
    <Card withBorder data-testid="owners-box">
      <Stack gap="xs">
        <Title order={5}>{he.wallet.owners}</Title>
        <Text>
          {he.wallet.ownerBase}: {names || '–'} ({he.roles[owner.role] ?? owner.role})
        </Text>
      </Stack>
    </Card>
  );
}
