import { Alert } from '@mantine/core';
import { useStatus } from '../api/queries';
import { he } from '../i18n/he';

export function StatusBanner() {
  const { data } = useStatus();
  if (!data || data.litellm_reachable) return null;
  return (
    <Alert color="yellow" variant="light" role="alert" data-testid="status-banner" radius={0}>
      ⚠ {he.banner}
    </Alert>
  );
}
