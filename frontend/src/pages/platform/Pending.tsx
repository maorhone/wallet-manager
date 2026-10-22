import { Stack, Title } from '@mantine/core';
import { PendingPlacementsTable } from '../../components/PendingPlacementsTable';
import { he } from '../../i18n/he';

export default function Pending() {
  return (
    <Stack>
      <Title order={4}>{he.pending.title}</Title>
      <PendingPlacementsTable scope="all" canPlace />
    </Stack>
  );
}
