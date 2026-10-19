import { Button, Card, Select, Stack, Title } from '@mantine/core';
import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiPost } from '../api/client';
import { keys, useDevUsers } from '../api/queries';
import { ErrorState } from '../components/ErrorState';
import { he } from '../i18n/he';

export default function Login() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [upn, setUpn] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  // /auth/dev/users exists only when DEV_AUTH=1; a 404 means the picker is off.
  const { data: users } = useDevUsers();
  const devAuth = users !== undefined;

  const devLogin = async () => {
    if (!upn) return;
    try {
      await apiPost('/auth/dev/login', { upn });
      await qc.invalidateQueries({ queryKey: keys.me });
      navigate('/');
    } catch (e) {
      setError(e);
    }
  };

  return (
    <Stack maw={420} mx="auto" mt={80}>
      <Title order={2}>{he.appName}</Title>
      <Card withBorder>
        <Stack>
          <Button component="a" href="/auth/login">
            {he.loginAdfs}
          </Button>
          {devAuth && (
            <>
              <Select
                label={he.devUser}
                data-testid="dev-user"
                data={users.map((u) => ({
                  value: u.upn,
                  label: `${u.display_name} (${u.upn})`,
                }))}
                value={upn}
                onChange={setUpn}
              />
              <Button variant="default" onClick={devLogin} disabled={!upn}>
                {he.devLogin}
              </Button>
            </>
          )}
          {error != null && <ErrorState error={error} />}
        </Stack>
      </Card>
    </Stack>
  );
}
