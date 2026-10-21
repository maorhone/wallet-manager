import type { Me, Scope } from './api/types';
import { he } from './i18n/he';

export const scopeKey = (s: Scope): string =>
  s.kind === 'department' && s.role === 'pmo'
    ? `pmo:${s.id}`
    : s.id != null
      ? `${s.kind}:${s.id}`
      : s.kind;

export const scopePath = (s: Scope): string => {
  switch (s.kind) {
    case 'group':
      return `/group/${s.id}/overview`;
    case 'department':
      return s.role === 'pmo' ? `/pmo/${s.id}/pool` : `/department/${s.id}/overview`;
    case 'platform':
      return '/platform';
    default:
      return '/wallet';
  }
};

/** Reports for the selected scope; PMO has department grants only. */
export function reportPath(s: Scope): string | null {
  if (s.role === 'pmo') return `/report/pmo/${s.id}/grants`;
  if (s.kind === 'group' && s.role === 'viewer') return null;
  const kind = s.kind === 'self' ? 'me' : s.kind === 'platform' ? 'global' : s.kind;
  const id = s.kind === 'self' ? 'me' : s.kind === 'platform' ? 'all' : s.id;
  return `/report/${kind}/${id}/allocation`;
}

export function scopeLabel(s: Scope): string {
  const role = s.role ? ` (${he.roles[s.role] ?? s.role})` : '';
  switch (s.kind) {
    case 'group':
      return `${he.group}: ${s.name}${role}`;
    case 'department':
      return `${he.department}: ${s.name}${role}`;
    case 'platform':
      return he.platform;
    default:
      return he.myWallet;
  }
}

/** Which scope the current URL belongs to (undefined on /login and unknown paths). */
export function scopeFromPath(pathname: string, scopes: Scope[]): Scope | undefined {
  if (pathname.startsWith('/report/')) {
    const [, , kind, id] = pathname.split('/');
    const key = kind === 'me' ? 'self' : kind === 'global' ? 'platform' : `${kind}:${id}`;
    return scopes.find((s) => scopeKey(s) === key && (kind === 'pmo' || s.role !== 'pmo'));
  }
  const [, first, id] = pathname.split('/');
  const key = first === 'wallet' ? 'self' : first === 'platform' ? 'platform' : `${first}:${id}`;
  return scopes.find((s) => scopeKey(s) === key);
}

export const storageKey = (upn: string) => `wm.scope.${upn}`;

/** The remembered scope if the user still has it, else their own wallet. */
export function initialScope(me: Me): Scope {
  const stored = localStorage.getItem(storageKey(me.upn));
  return me.scopes.find((s) => scopeKey(s) === stored) ?? me.scopes[0];
}

export function rememberScope(upn: string, s: Scope) {
  localStorage.setItem(storageKey(upn), scopeKey(s));
}
