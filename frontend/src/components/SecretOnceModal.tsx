import { Alert, Button, CopyButton, Group, Modal, Stack, TextInput } from '@mantine/core';
import { he } from '../i18n/he';

const t = he.serviceKeys;

/**
 * NFR5: the secret is shown exactly once, in the caller's component state only (never the
 * TanStack Query cache). Closing clears it and it cannot be reopened.
 */
export function SecretOnceModal({ secret, onClose }: { secret: string; onClose: () => void }) {
  return (
    <Modal opened onClose={onClose} title={t.secretTitle} closeOnClickOutside={false}>
      <Stack data-testid="secret-once">
        <Alert color="yellow" variant="light">
          {t.secretWarning}
        </Alert>
        <Group align="flex-end" wrap="nowrap">
          <TextInput
            readOnly
            aria-label={t.secretTitle}
            data-testid="secret-value"
            value={secret}
            flex={1}
          />
          <CopyButton value={secret}>
            {({ copied, copy }) => (
              <Button data-testid="secret-copy" onClick={copy}>
                {copied ? t.copied : t.copy}
              </Button>
            )}
          </CopyButton>
        </Group>
        <Group justify="flex-end">
          <Button data-testid="secret-close" onClick={onClose}>
            {t.close}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
