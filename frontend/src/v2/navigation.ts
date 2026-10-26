import type { Me } from '../api/types';
import { he } from '../i18n/he';
import { scopePath } from '../scopes';
export const areaStorageKey = (upn: string) => `wm.v2.area.${upn}`;
export interface Area {
  path: string;
  label: string;
  legacy?: string;
}
const isAllocatorRole = (r: Me['roles'][number]) =>
  ['platform_admin', 'org_allocator'].includes(r.role) &&
  ['global', 'organization'].includes(r.scope_kind);

export const isAllocator = (me: Me) => me.roles.some(isAllocatorRole);
export const isAdmin = (me: Me) => me.roles.some((r) => r.role === 'platform_admin');

/** Organizations the caller is bound to as allocator (an administrator may open any by id). */
export const allocatorOrgs = (me: Me) =>
  me.roles
    .filter((r) => r.role === 'org_allocator' && r.scope_kind === 'organization' && r.scope_id)
    .map((r) => ({ id: r.scope_id as number, name: r.scope_name ?? `#${r.scope_id}` }));

/** An inherited group/department URL: the server decides access, the client never hides it. */
export function drillArea(pathname: string): Area | undefined {
  const m = /^\/v2\/(group|department)\/(\d+)$/.exec(pathname);
  return m
    ? { path: pathname, label: `${he.v2[m[1] as 'group' | 'department']} · ${m[2]}` }
    : undefined;
}

/** Every scoped area comes from /me scopes, never from a client-supplied ID. */
export function areasFor(me: Me): Area[] {
  const areas: Area[] = [{ path: '/v2/personal', label: he.v2.personal, legacy: '/wallet' }];
  for (const scope of me.scopes) {
    if ((scope.kind === 'group' || scope.kind === 'department') && scope.id != null) {
      const path = `/v2/${scope.kind}/${scope.id}`;
      if (!areas.some((a) => a.path === path))
        areas.push({
          path,
          label: `${he.v2[scope.kind]} · ${scope.name ?? scope.id}`,
          legacy: scopePath(scope),
        });
    }
  }
  // Ownership alone does not confer allocation rights.
  const allocator = isAllocator(me);
  if (allocator)
    areas.push({
      path: '/v2/organization',
      label: he.v2.organization,
      legacy: me.scopes.some((s) => s.kind === 'platform') ? '/platform' : undefined,
    });
  areas.push({ path: '/v2/requests', label: he.v2.requests });
  if (
    me.scopes.some(
      (s) =>
        (s.kind === 'department' && s.role !== 'pmo') ||
        (s.kind === 'group' && s.role === 'group_head'),
    ) ||
    allocator ||
    me.roles.some((r) => ['system_owner', 'initiative_owner'].includes(r.role))
  ) {
    areas.push({ path: '/v2/systems', label: he.v2.systems });
  }
  areas.push(
    { path: '/v2/service', label: he.v2.service },
    { path: '/v2/guide', label: he.v2.guide },
  );
  return areas;
}
export function rememberedArea(me: Me, areas: Area[]): string {
  try {
    const stored = localStorage.getItem(areaStorageKey(me.upn));
    return areas.find((a) => a.path === stored)?.path ?? areas[0].path;
  } catch {
    return areas[0].path;
  }
}
