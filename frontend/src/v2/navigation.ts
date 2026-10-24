import type { Me } from '../api/types';
import { he } from '../i18n/he';
import { scopePath } from '../scopes';
export const areaStorageKey = (upn: string) => `wm.v2.area.${upn}`;
export interface Area {
  path: string;
  label: string;
  legacy?: string;
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
  const allocator = me.roles.some(
    (r) =>
      ['platform_admin', 'org_allocator'].includes(r.role) &&
      ['global', 'organization'].includes(r.scope_kind),
  );
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
