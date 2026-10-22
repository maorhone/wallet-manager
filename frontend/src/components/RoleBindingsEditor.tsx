import { Button, Loader, Paper, Stack, Text, Textarea } from '@mantine/core';
import { useState } from 'react';
import { useRoleBindings, useRoleBindingsWrite } from '../api/queries';
import type { RoleBinding, RoleBindingScope } from '../api/types';
import { he } from '../i18n/he';
import { ChangeSummaryDialog } from './ChangeSummaryDialog';
import { ErrorState } from './ErrorState';

const ROLES = {
  global: ['platform_admin', 'viewer'],
  department: ['department_rep', 'pmo', 'viewer'],
  group: ['group_head', 'viewer'],
};

/** Role assignments use AD groups; resolved people are display-only, never mirrored into WM. */
export function RoleBindingsEditor({
  scope,
  readOnly = false,
}: {
  scope: RoleBindingScope;
  readOnly?: boolean;
}) {
  const bindings = useRoleBindings(scope);
  if (bindings.isPending) return <Loader aria-label={he.loading} />;
  if (bindings.error || !bindings.data)
    return <ErrorState error={bindings.error} onRetry={bindings.refetch} />;
  return (
    <Stack>
      {ROLES[scope.scope_kind].map((role) => {
        const binding = bindings.data.find((b) => b.role === role);
        return (
          <RoleEditor
            key={`${role}:${JSON.stringify(binding?.ad_group_dns ?? [])}`}
            scope={scope}
            role={role}
            binding={binding}
            readOnly={readOnly}
          />
        );
      })}
    </Stack>
  );
}

function RoleEditor({
  scope,
  role,
  binding,
  readOnly,
}: {
  scope: RoleBindingScope;
  role: string;
  binding?: RoleBinding;
  readOnly: boolean;
}) {
  const before = binding?.ad_group_dns ?? [];
  const [text, setText] = useState(before.join('\n'));
  const [review, setReview] = useState(false);
  const write = useRoleBindingsWrite();
  const after = [
    ...new Set(
      text
        .split('\n')
        .map((dn) => dn.trim())
        .filter(Boolean),
    ),
  ].sort();
  const changed = JSON.stringify([...before].sort()) !== JSON.stringify(after);
  return (
    <Paper withBorder p="sm" data-testid={`role-editor-${role}`}>
      <Stack gap="xs">
        <Text fw={600}>{he.roles[role]}</Text>
        <Textarea
          label={he.platformPage.dns}
          value={text}
          onChange={(e) => setText(e.currentTarget.value)}
          readOnly={readOnly}
          dir="ltr"
          autosize
          minRows={2}
        />
        <Text size="sm">{he.platformPage.members}</Text>
        <Text size="sm" data-testid="role-members">
          {binding?.members.map((m) => `${m.display_name} (${m.upn})`).join(', ') || he.empty}
        </Text>
        <Button disabled={readOnly || !changed} onClick={() => setReview(true)}>
          {he.platformPage.review}
        </Button>
        <ChangeSummaryDialog
          opened={review}
          rows={[
            {
              key: role,
              label: he.roles[role],
              before: before.join('\n') || '—',
              after: after.join('\n') || '—',
            },
          ]}
          onClose={() => setReview(false)}
          onConfirm={(reason, key) =>
            write.mutateAsync({
              body: { ...scope, role, ad_group_dns: after },
              reason,
              key,
            })
          }
        />
      </Stack>
    </Paper>
  );
}
