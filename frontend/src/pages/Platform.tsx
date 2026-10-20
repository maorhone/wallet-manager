import { Stack, Title } from '@mantine/core';
import { PendingPlacementsTable } from '../components/PendingPlacementsTable';
import { he } from '../i18n/he';

/** Platform view (§12.5.5): only the pending-placement section so far; the rest is Phase 3. */
export default function Platform() {
  return (
    <Stack>
      <Title order={3}>{he.platform}</Title>
      <Title order={4}>{he.pending.title}</Title>
      <PendingPlacementsTable scope="all" canPlace />
    </Stack>
  );
}
