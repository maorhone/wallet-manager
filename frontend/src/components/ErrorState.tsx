import { Alert, Button, Stack } from '@mantine/core';
import { errorMessage } from '../i18n/errors';
import { he } from '../i18n/he';

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <Alert color="red" variant="light" role="alert" title={he.error} data-testid="error-state">
      <Stack gap="xs" align="flex-start">
        {errorMessage(error)}
        {onRetry && (
          <Button size="compact-sm" variant="default" onClick={onRetry}>
            {he.retry}
          </Button>
        )}
      </Stack>
    </Alert>
  );
}
