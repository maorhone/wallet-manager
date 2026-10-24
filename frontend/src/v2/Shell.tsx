import { useEffect } from 'react';
import { Link, Navigate, useLocation } from 'react-router-dom';
import { useMe } from '../api/queries';
import { apiPost } from '../api/client';
import { ErrorState } from '../components/ErrorState';
import { he } from '../i18n/he';
import { areasFor, areaStorageKey, rememberedArea } from './navigation';
import Department from './pages/Department';
import OrgAllocation from './pages/OrgAllocation';
import Guide from './pages/Guide';
import Initiative from './pages/Initiative';
import ServiceStatus from './pages/ServiceStatus';
import Personal from './pages/Personal';
import Requests from './pages/Requests';
import SectionBudget from './pages/SectionBudget';
import '../styles/v2.css';

export function Shell() {
  const { data: me, error, isPending, refetch } = useMe();
  const { pathname } = useLocation();
  const areas = me ? areasFor(me) : [];
  const current = areas.find((area) => area.path === pathname);
  const currentPath = current?.path;
  useEffect(() => {
    if (me && currentPath) {
      try {
        localStorage.setItem(areaStorageKey(me.upn), currentPath);
      } catch {
        /* Storage is optional. */
      }
    }
  }, [me, currentPath]);
  if (isPending) return <p role="status">{he.loading}</p>;
  if (error || !me) return <ErrorState error={error} onRetry={refetch} />;
  if (!current)
    return (
      <Navigate
        to={pathname === '/v2' || pathname === '/v2/' ? rememberedArea(me, areas) : areas[0].path}
        replace
      />
    );
  const logout = async () => {
    await apiPost('/auth/logout', undefined);
    location.assign('/login');
  };
  return (
    <div className="v2-shell" dir="rtl">
      <a className="v2-skip" href="#v2-main">
        {current.label}
      </a>
      <aside className="v2-sidebar">
        <div className="v2-brand">{he.v2.title}</div>
        <nav aria-label={he.v2.navigation}>
          {areas.map((area) => (
            <Link
              key={area.path}
              to={area.path}
              aria-current={current.path === area.path ? 'page' : undefined}
            >
              {area.label}
            </Link>
          ))}
        </nav>
      </aside>
      <div className="v2-workspace">
        <header className="v2-header">
          <span>
            {me.display_name} · <bdi dir="ltr">{me.upn}</bdi>
          </span>
          <button type="button" onClick={logout}>
            {he.logout}
          </button>
        </header>
        <main id="v2-main" tabIndex={-1}>
          <h1>{current.label}</h1>
          {current.path === '/v2/guide' ? (
            <Guide />
          ) : current.path === '/v2/service' ? (
            <ServiceStatus />
          ) : current.path === '/v2/personal' ? (
            <Personal />
          ) : current.path === '/v2/systems' ? (
            <Initiative
              canMove={me.roles.some(
                (r) =>
                  ['platform_admin', 'org_allocator'].includes(r.role) &&
                  ['global', 'organization'].includes(r.scope_kind),
              )}
            />
          ) : current.path === '/v2/organization' ? (
            <OrgAllocation
              organizationId={
                me.roles.find((r) => r.role === 'org_allocator' && r.scope_kind === 'organization')
                  ?.scope_id ?? 1
              }
              isAdmin={me.roles.some((r) => r.role === 'platform_admin')}
            />
          ) : current.path === '/v2/requests' ? (
            <Requests />
          ) : current.path.startsWith('/v2/group/') ? (
            <SectionBudget groupId={current.path.split('/')[3]} />
          ) : current.path.startsWith('/v2/department/') ? (
            <Department departmentId={current.path.split('/')[3]} />
          ) : (
            <p className="v2-muted">{he.v2.pending}</p>
          )}
          {current.legacy && (
            <Link className="v2-legacy" to={current.legacy}>
              {current.path === '/v2/personal' ? he.myWallet : he.v2.legacy}
            </Link>
          )}
        </main>
      </div>
    </div>
  );
}
