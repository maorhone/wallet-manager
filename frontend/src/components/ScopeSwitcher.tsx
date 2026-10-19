import { NativeSelect } from '@mantine/core';
import { useLocation, useNavigate } from 'react-router-dom';
import { useMe } from '../api/queries';
import { he } from '../i18n/he';
import { rememberScope, scopeFromPath, scopeKey, scopeLabel, scopePath } from '../scopes';

/** Top-bar scope switcher (§12.1): self, each group, each department, platform. */
export function ScopeSwitcher() {
  const { data: me } = useMe();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  if (!me) return null;
  const current = scopeFromPath(pathname, me.scopes);
  return (
    <NativeSelect
      aria-label={he.scope}
      data-testid="scope-switcher"
      value={current ? scopeKey(current) : ''}
      data={me.scopes.map((s) => ({ value: scopeKey(s), label: scopeLabel(s) }))}
      onChange={(e) => {
        const next = me.scopes.find((s) => scopeKey(s) === e.currentTarget.value);
        if (!next) return;
        rememberScope(me.upn, next);
        navigate(scopePath(next));
      }}
    />
  );
}
