# Wallet Manager for LiteLLM: Product Requirements (living document)

Source of truth is SPEC.md for v1, with the local spec v2 and owner decisions taking precedence
for Phase 5. This file summarises the product and tracks status per area.
Update it whenever a step changes product functionality. Status: Done / In progress / Planned.

## 1. Purpose

LiteLLM enforces spend budgets and concurrency per key, user, team and organization, but has no way to
allocate a budget down an organization hierarchy and let managers control their part. Wallet Manager sits
in front of a LiteLLM proxy and lets each level split what it received:
department budget -> groups (teams) -> employees, plus per-model allowances, rpm/concurrency limits and
temporary project grants. Employees see their own wallet; managers allocate; LiteLLM enforces.
All writes go through Wallet Manager (database transaction, audit, outbox), then to LiteLLM through one typed client module.

## 2. Users and roles

| Role                      | Can do                                                                                                                                            |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Platform admin            | Everything; sets department budgets and model allowances; manual member actions; placement of unknown users                                       |
| Department representative | Splits the department budget among groups, sets pots and limits, sets group heads' values, shares grants, kick/restore/move within the department |
| Group head                | Sets employees' caps, model caps and limits in their group, creates shares of grants, kick/restore                                                |
| PMO                       | Creates, changes and closes project grants from the department PMO pool; read-only elsewhere                                                      |
| Viewer                    | Read-only aggregates                                                                                                                              |
| Employee                  | Own wallet only                                                                                                                                   |

No one changes their own values (DR9, `SELF_ALLOCATION_FORBIDDEN`); the next level up does.

## 3. Key owner decisions

- No automatic AD sync (20/10/2026). Users enter through the onboarding API called by an external portal and are
  moved, kicked or deleted manually by authorised roles.
- Authentication is real OIDC (ADFS); `DEV_AUTH=1` enables seeded dev login in the sandbox only.
- Personal key secrets are never returned; Claude Code uses the user's ADFS JWT mapped by upn.
- Approved open questions (PROGRESS "Questions for owner", PLAN section 5): defaults are decisions.
- The repository is public: placeholders only (corp.example), enforced by a hygiene test.
- The spec wins over PLAN.md when they disagree; differences are noted in PROGRESS.md.
- No paid APIs or Enterprise license in the sandbox; a fake LiteLLM implements the management API.

## 4. Functionality by area

### 4.1 Budgets and allocation (FR-D, FR-G) - Phase 1: Done

- Admin sets a department budget and period; representative splits it into group bases, PMO pool and reserve.
- Per-model allowances per department, pots per group, personal model caps, over-allocation factor.
- Group and personal tpm/rpm/concurrency limits within platform policy; member defaults for new joiners.
- Every sum is checked under a per-parent advisory lock; errors carry requested/available/parent_total.
- Platform review fixes (Step 3.2): malformed policy values return 422 before any change; global role bindings can be
  replaced or removed (the last admin binding remains protected). Concurrent creation rejects duplicate department/group
  DNs and second staff groups with 422; database constraints also enforce these rules.

### 4.1.1 Organization and nested-group schema - Phase 5 Step 5.1: Done

Existing departments belong to one default Wallet Manager organization. The organization stores
separate Decimal credit envelopes for departments and organization systems, plus a budget period;
the root itself creates no LiteLLM object. Upgrade initializes the department envelope from existing
department budgets and the systems envelope at zero, preserving existing allocations and systems.
Legacy department creation continues to attach to the default organization.

Groups can reference a parent in the same department. The database rejects self-parenting, cycles
and trees deeper than four group levels (a root group is level one), including subtree moves and
multi-row changes. Organization-scoped `org_allocator` bindings can be stored and seeded using
scope `default`.

### 4.1.3 Nested group budgets - Phase 5 Step 5.3: Done

A subgroup's base budget is carved from its parent group, not the department: the parent's allocated
amount is member caps plus service keys plus the bases of its subgroups, and the department only
counts top-level groups. Saving the split, moving a subtree or inserting a parent is rejected with
`PARENT_OVERALLOCATED` when a parent cannot cover its children. Group summaries add `rollup_used`
(own plus descendants' spend, each team counted once). Over-allocation factors still apply per group.

### 4.1.2 Hierarchy management API - Phase 5 Step 5.2: Done

Admins create Wallet Manager roots with `POST /organizations` and attach new departments with
`POST /departments` using `organization_id` (omitting it keeps the default organization).
The organization root creates no LiteLLM object.

`POST /groups` takes `department_id` and an optional `parent_group_id`. Department representatives
can add root groups or subgroups anywhere in their department; group heads can add below their own
group or a descendant. Representatives can insert a new parent with `POST /groups/{id}/insert-parent`
or move a whole subtree with `PATCH /groups/{id}/parent`; a null parent moves it to the department root.
Admins can manage every tree. Organization-scoped `org_allocator` bindings are configurable through
the existing role-binding API and provide representative/head management within that organization's tree.
An ancestor group head inherits group-head permissions on descendants; reparenting immediately
changes that authority, with authorization rechecked under the department lock.

All hierarchy writes require a reason, support idempotency and record an audit entry atomically.
Cross-department parenting, cycles and trees deeper than four group levels are rejected without
partial changes. Creating a group queues one flat LiteLLM team with zero budget; inserting a parent
preserves the child's existing parent above the new node. Moves preserve the subtree's budgets,
members and LiteLLM team identities and queue no LiteLLM writes. The legacy admin-only group-creation
route remains available. Nested budget calculations are still planned for Step 5.3.

### 4.2 Wallets and reports (FR-E, FR-R, FR-V) - Phase 1: Done

- "My wallet": base cap, shares, total, used, remaining, reset date, binding constraint (self/group/department), owners' names.
- Group and department summaries, member tables, history timeline, usage charts from a spend snapshot.
- Over-allocation report against live LiteLLM values; status, outbox and metrics endpoints.
- Share rows in the wallet show project, model, amount, used (attributed), expiry and days left - Done (Step 2.2).
- Allocation and grants reports backend - Done (Step 4.1). `GET /reports/allocation?level=department|group|employee&id=&period=YYYY-MM`
  returns allocation, snapshot spend, remaining, fraction used, wallet state, per-model allocation/spend and snapshot time.
  Department rows include groups, PMO pool and reserve; group rows include members' base caps, active shares and model caps;
  employees have no child rows. Past periods use their snapshots and the existing audit-based allocation history.
  Department reports allow admin, representative and scoped viewers; member rows require head, representative or admin access;
  each employee can read their own report. PMO report access is limited to grants.
  `GET /reports/grants?department=&project_ref=` groups visible grants by project with distinct department/group counts,
  grant count, amount, shared, unshared, attributed used and returned, plus totals across visible grants. Visibility matches
  the grant list, including heads' own groups and PMO's own department. Grants report UI is available (Step 4.6).
- Usage, concurrency peaks and blocked spend backend - Done (Step 4.2).
  `GET /reports/usage?scope=employee|group|department|global&id=&from=&to=&granularity=day|week|month&by=model|member`
  returns snapshot series, the current cap and snapshot time for up to 400 inclusive local dates. Day snapshots now follow
  `APP_TIMEZONE`, including month boundaries; weeks start Monday and month buckets start on the first day. Member series
  require member visibility; employees can read their own series. Global aggregates require admin or a global viewer.
  `GET /reports/concurrency-peaks` accepts the same scopes and up to 31 local dates. Its live read counts overlapping
  SpendLogs intervals, with end events before starts at the same instant, carrying running requests across midnight and
  clipping to the selected range. It returns daily aggregate peaks with UTC timestamps and the top 100 visible members
  (peak, timestamp, configured concurrency limit); viewers receive daily aggregates only.
  `GET /reports/blocked` returns a row per exhausted employee/base or model cap per day, with current cap, cumulative
  calendar-month spend and that day's spend, including zero-spend days after exhaustion. Per FR-R5, base exhaustion uses
  the base cap even when a share increases the wallet total. Member visibility applies; PMO is limited to grant reports.
  The fake supports explicit request intervals and key hashes in `/_test/spend` and atomic `/_test/spend/bulk` setup.
- Report alerts - Done (Step 4.3). Every over-allocation finding sends a critical notification to platform admins
  and the affected department's representatives, once per finding/model/local day, including live LiteLLM breaches.
  Unknown departments notify admins only. The daily scheduler examines yesterday's SpendLogs overlaps in APP_TIMEZONE;
  when employees reach their effective key concurrency limit (personal override or `concurrency.default`), admins receive
  one summary with the full count and the top 100 affected members (peak, UTC timestamp, effective limit).
  Members with no effective limit are excluded. This pilot input for D6 can be muted.
  The same job updates `wm_concurrency_peak{scope="global"}` and `wm_blocked_employees` for yesterday; the latter counts
  distinct employees at a base or model cap using snapshots and FR-R5.
- Reports shell, Allocation and Over-allocation UI - Done (Step 4.4).
  The Hebrew “דוחות” link opens `/report/:kind/:id/:tab` for the selected own-wallet, group, department or global scope.
  Allocation shows the shared wallet card with snapshot freshness, child allocation/spend/remaining/status rows and model
  allocations/spend/remaining; model remaining stays negative when spend exceeds the cap, including child rows,
  while uncapped models show no remaining value. Global scope lists department reports. The shared period selector loads that period's values,
  and past periods remain read-only. Department and global scopes also have an Over-allocation tab showing source,
  level, entity/model, allocated amount, ceiling and a warning badge. These findings are explicitly current-only,
  independent of the selected period. Existing server permissions apply: member reports require member visibility,
  scoped viewers get department aggregates, and PMO has a department grants report link.
- Grants-by-project and Blocked tabs - Done (Step 4.6).
  Department/global Grants tabs show project rows and totals for distinct departments/groups, grant count,
  amount, shared, unshared, used and returned credits. PMO's Reports link opens only its department grants
  at `/report/pmo/:id/grants`, retaining the PMO scope. Grants are labeled current and independent of the selected period.
  The Blocked tab shows own/group/department/global daily base/model exhaustion where member visibility permits;
  viewers and PMO do not receive member tabs. Rows show date, employee, cap kind/model, current cap,
  month-to-date spend and daily spend, using the selected month's snapshot range. Caps are labeled current.
  Loading, retry/error and empty states reuse shared components.
- Concurrency peaks UI - Done (Step 4.7); Phase 4 reports and alerts complete.
  The Concurrency peaks tab plots daily peaks for the selected own/group/department/global scope and calendar month,
  using live SpendLogs overlaps rather than request counts or summed employee peaks.
  Authorized member scopes show the top 100 employees with observed peak, current personal `max_parallel`
  (a dash when unset) and peak timestamp in UTC. Viewers see aggregate peaks only; PMO remains grants-only.
  The period selector reloads the live report for that month (at most 31 local dates); shared loading,
  error/retry and empty states apply. These measurements support D6 pilot decisions without changing policy.
- Usage charts at every level - Done (Step 4.5).
  The Usage report tab shows snapshot spend stacked by model for own-wallet, group, department and global scopes,
  with day/week/month buckets, cumulative spend and a cap line. Heads, representatives and admins can switch
  authorized group/department/global reports to member stacks; viewers see model aggregates only.
  Group and department overviews include daily model charts; My Wallet keeps its daily chart.
  The selected period controls the date range; the report labels the API's cap as current and shows snapshot freshness.
  Loading, empty, error and retry states reuse the shared chart. PMO remains limited to grants.
  All Phase 4 report tabs are available (Steps 4.4–4.7).

### 4.3 Onboarding and member lifecycle (section 9) - Phase 1b: Done

- Onboarding API for the portal (service + user JWT) returns the placement result and wallet readiness.
- Pending placement list for unknown-department users; admin or representative places them.
- Manual kick, restore, move, delete with target-tier rules, typed confirmation and ordered outbox chains.

### 4.4 Grants and shares (FR-P) - Phase 2: In progress

- Review fixes (2.1-2.4): closing shares enforces the same self and hierarchy restrictions as
  creating/changing them. Usage attribution accounts for earlier active/closed shares within the
  relevant team and local budget months. Grant/project/PMO usage figures include active shares'
  snapshot usage. Closing a grant removes only unused credit, retaining spent credit in the team's
  ceiling and model pot for the closing month. Returns preserve four decimal places, conserving
  fractional spend. Expiry and member recomputation commit together; concurrent retries do not
  create duplicate future grants. Removing the last model pot clears it in LiteLLM.
- Review fixes (2.8): share dialogs sum money exactly, keep an unchanged share expiry, show a before/after summary, and
  offer only active employees; the close warning states that unused money returns to the PMO pool and lowers the group's
  budget. Edit/close buttons follow each share's server `actions`; share rows show sync status; dialogs close on period
  change and are hidden for past periods; the group Grants tab shows the unshared remainder; the wallet shows project refs.
- PMO pool, create/change/close grants, project summary - Done (Step 2.1).
- Shares (Step 2.2, Done, backend): head, representative or admin shares part of an active grant with an employee
  (`POST /grants/{id}/shares`, bulk and all-or-nothing). Rejections: sum above grant (`SHARE_EXCEEDS_GRANT`), expiry after
  the grant, self (`SELF_ALLOCATION_FORBIDDEN`), co-head or outside the group (`SCOPE_FORBIDDEN`), inactive member (409),
  second active share for the same person. Change (`PATCH`), close (`POST .../close`) and list (`GET`).
- Effect in LiteLLM: member `temp_budget_increase` = sum of the person's active shares, `temp_budget_expiry` = earliest expiry
  (both always sent; `0` and null when none); a model grant also raises the personal cap for that model.
- Closing a share (and a grant, which closes its shares first) attributes usage: spend counts against the base cap first,
  then shares by earliest expiry (7.3.1); the unused part returns to the grant and the PMO pool, and the team budget is lowered.
- Activation and expiry by the scheduler - Done (Step 2.3): a pending grant becomes active at its start time; an expired
  share is closed (reason `expiry`) and its unused part returns to the grant; an expired grant closes its remaining shares and
  returns the rest to the pool. Moving or deleting a member closes their active shares first (reason `move` / `manual`), with
  usage taken from the old team; the return never holds up the move.
- PMO page `/pmo/<dept>/pool|grants` - Done (Step 2.6): the pool card (size, active grants, used, returned, available), a grant
  list filtered by project, group, status and "expiring soon" (7 days or less), and a create wizard (project and purpose, group
  with its current state, amount with the pool available and a block above it, optional model, dates, then a summary with the
  reason). A pool race returns the `PMO_POOL_INSUFFICIENT` message. The create button needs `actions.create_grant`.
- PMO grant actions - Done (Step 2.7): per grant row (only when the server `actions` allow) PMO can extend the expiry,
  increase the amount or close early, each with a required reason; closing early shows the unused amount (amount - used - returned) that returns to the pool.
  The dialog shows before/after of the changed value and of the pool availability, uses application-timezone days (from `/me`),
  keeps one idempotency key across retries of the same submission, and is hidden for past (read-only) periods.
  Projects tab: one row per project reference (grants, amount, used, returned) with drill-down to each group's grant and each
  share's usage; the detail refreshes every 15 s. References containing `/` drill down via
  `GET /projects/summary?project_ref=` (the path form stays).
- Group Grants tab - Done (Step 2.8): `/group/<id>/grants` lists the group's grants (shown to head, rep, PMO, viewer, admin).
  A head or rep (server `actions.share`, active grant, current period) opens "שיתוף מענק": pick members, amount and expiry
  (default and maximum: the grant's expiry), the unshared remainder updates live and blocks above the grant; the actor's own
  row is disabled and names the owner role (DR9; the API still returns `SELF_ALLOCATION_FORBIDDEN`). Each grant opens its
  shares (amount, used, returned, expiry) with change and close (reason required). Employees see shares with days left in My wallet.
- Department Group heads tab - Done (Step 2.9): `/department/<id>/group-heads` (shown to a rep or admin, also for past periods, read-only) lists each
  group head with group, base cap, shares, model caps, limits and sync badge. The side panel edits the base cap, model caps and limits;
  "שיתוף מענק" offers only the head's group's active grants. A rep's own row (when also a head) is read-only (DR9). Viewers and PMO get `group_heads: []` (aggregates only). Heads are matched department-wide, so a moved head stays listed under their current group; shares sum all active grants.
  `GET /departments/{id}/summary` gains `group_heads`.

### 4.5 Scheduler - Phase 2: In progress (grants job Done in Step 2.3)

Separate process (`python -m app.scheduler`) with an injectable clock; one replica runs a job at a time (advisory lock) and a
restart catches up on everything overdue. Grants job (every 60 s): activation, share and grant expiry, then one recompute of the
member's temporary budget (earliest remaining expiry) per affected person. Metrics: `wm_scheduler_job_runs_total`,
`wm_scheduler_job_last_success_timestamp`. Notifications and reconciliation jobs run alongside grants.
The report-alert job runs at startup and every 86400 seconds (configurable with `SCHEDULER_REPORT_ALERTS_INTERVAL_S`),
under its own advisory lock; reruns share the notification dedupe keys with the worker's over-allocation checks.

### 4.6 Reconciliation (FR-RC) - Phase 2: Done (Step 2.5)

Every five minutes, compare owned organization, team, membership, personal key and mapping fields
against live LiteLLM values. Repairs use the durable outbox and its retries; admins can read the
correction log through `GET /audit?actor=system:reconcile`. Three corrections of the same field
within 24 hours trigger a critical administrator notification, deduplicated per field and UTC day.
Role fields are excluded; there is no AD sync. Pending/failed writes and writes arriving during
the live read suppress corrections. Archived groups and left/pending-placement employees are
excluded. Missing objects (membership, key, mapping) are never recreated automatically; each raises a
critical administrator notification (`reconcile_missing`), deduplicated per object and UTC day.
A mapping that points at another employee's key is re-pointed to the employee's own key (delete +
new, no new key). Corrections are queued under the employee/group/department outbox entities, so
they block move/delete/place until applied, and run under the same locks as those actions.

### 4.7 Notifications (FR-N) - Phase 2: Done (backend Step 2.4, UI Step 2.10)

In-app notifications, deduplicated per person and event. Written by the scheduler job (every 15 min): employee at 80% / 100%
of their total (the employee), group at 80% / 100% (group heads), grant expiring in 7 days and grant under 20% used with 7 days
left (group heads and the department's PMO), failed outbox rows (platform admins). Written with the action itself: a user left
pending placement (the department's representatives, or platform admins when the department is unknown), a member moved into a
group (the new group head), a grant created (the group heads). 100% and outbox-failure kinds are critical and cannot be muted;
the others can (`PUT /me/notification-prefs`). API: `GET /me/notifications?unread=`, `POST /me/notifications/{id}/read`.
Optional email copy (`NOTIFY_EMAIL_ENABLED`, off by default, needs an internal relay). UI Done in Step 2.10: a bell in the
header shows the unread count and opens a drawer with the list (mark read, link to the scope: a grant opens the group's Grants
tab; employee 80%/100% alerts open My wallet; a pending placement opens the department's Pending tab, or the Platform page when the department is unknown; unread alerts older than the newest 100 stay reachable); "My wallet" has a preferences card with a checkbox per kind, critical kinds shown checked and disabled.
The dev seed can add a sample grant (`SEED_GRANTS=1`; the live preview does).
Step 4.3 adds Hebrew labels and preferences for critical `overallocation` (cannot mute, 422) and non-critical
`concurrency_at_limit` notifications, reusing the bell and preference controls.

### 4.8 UI screens - Phase 1/1b: Done; Phase 2: Planned

RTL Hebrew React app: my wallet, group (overview, members with actions, history), department (overview, split, pending),
scope switcher, change-summary dialog, PMO page (pool and grants with the create wizard; Done in Step 2.6; a PMO scope opens
`/pmo/<dept>/pool`; grant actions and Projects tab Done in Step 2.7). Group Grants tab with share dialogs Done in Step 2.8.
Group heads tab Done in Step 2.9. Notifications bell and preferences Done in Step 2.10. Group Service keys tab Done in Step 3.5
(see 4.9).

### 4.8.1 Platform shell and Departments tab - Step 3.7: Done

Platform admins enter `/platform`, which redirects to the existing Pending placement tab. The Departments tab
lists current budgets and LiteLLM budget periods; admins can create departments, edit their budget and period,
and create regular or department staff groups with a zero base budget and a sync badge. Each department links
to its staff group's Members screen for representatives' personal values (DR9). A role editor assigns AD group
DNs per department role and shows resolved member names; changes require a reason in the shared change summary.
Past-period selection disables changes. Operations, Policies and Audit tabs are reserved for subsequent steps.

### 4.8.2 Platform Operations tab - Step 3.8: Done

Platform admins open `/platform/operations` to see LiteLLM reachability, Redis status (including
"לא מוגדר" when Redis is not configured), outbox depth with pending and failed counts, pending placements,
and spend freshness with its timestamp. The outbox table defaults to failed writes and can show
pending, applied or all writes, including error text. Pending and failed writes can be retried
through the shared change summary with a required reason and Idempotency-Key; polling updates
their status after application. The drift log uses the existing reconciliation audit filter and
shared history timeline. Overlapping outbox consumers serialize each write through application
and commit, preventing duplicate provisioning calls and preserving the resulting applied or failed
status. Policies and Audit search remain reserved for Step 3.9.

### 4.8.3 Platform Policies and Audit search tabs - Step 3.9: Done

Platform admins open `/platform/policies` to edit concurrency and rpm minimum/default/maximum,
notification thresholds, the default over-allocation factor and each group's factor (1–2).
Changes show before/after values and require a reason; requests include an Idempotency-Key.
Invalid ranges/factors cannot be saved, and past-period selections disable changes. Saved values
are reloaded from the API; department summaries now include each group's current factor. Existing
personal limits are left as saved when a policy is lowered; a later save outside the new range
shows the Hebrew `LIMIT_OUT_OF_POLICY` error with the allowed range.

`/platform/audit` searches across all audit records with optional entity, id, actor and from/to
filters. Date/time inputs use the browser's local time and are sent as UTC instants. Results use
the shared history timeline with actor, reason, before/after values and LiteLLM call status. Empty
filters search everything. Both tabs are restricted to platform admins. The phase-3b acceptance
mapping is recorded in PROGRESS.md; Steps 3.3–3.6 are verified at merge into main.

### 4.9 Production and service keys (FR-K) - Phase 3: Done (Steps 3.3–3.6)

A platform admin sets a department's production budget (DR4: a separate LiteLLM Organization from
the department's own, so employee spending can never block production) and creates systems (Teams)
under it with their own budget. Creating a system is rejected with the usual over-allocation error
when its budget would push the department's systems past the production budget; lowering the
production budget below the systems' total is accepted and shown as over-allocated. The department's
representative and the admin can read the production organization, its systems, their spend and
sync status (admin only edits); other roles have no access. Production is not reconciled (FR-RC
only re-derives the §8.1 fields). Production org/system LiteLLM ids are generated (`{org}-prod`,
`sys-{id}`); DR4 reserves these patterns, so platform department/group creation rejects an id ending
in `-prod` or starting with `sys-`, which would otherwise let a department's own org or an employee
team collide with a production object. Setting the production budget without a period keeps the
current budget period ("1mo" the first time). The Production tab follows the selected period: a past
period shows that month's spend and is read-only (no edit actions); system key counts refresh after
key creation or revocation.

Step 3.4 service-key backend is complete: group heads and admins manage group keys; admins manage
system keys. Scoped representatives/viewers can list group keys, and representatives can list
system keys. Creation and rotation show the secret once with no-store caching; only hashes reach
storage, audits and deletion jobs. Replays omit secrets. Pending/active key budgets count with member
caps against the group base times its allocation factor, or against the system budget. Policy limits
apply, with the existing admin maximum override. Upstream failure returns 503 and failed creation
reserves no budget; failed rotation preserves the old active key. Rotation/revocation delete the fixed
old hash through the outbox. Per-key period, daily and model spend includes earlier rotated hashes.

Step 3.5 adds the group head's "מפתחות שירות" (Service keys) tab: a list of the group's keys (alias, budget,
used, concurrency, rpm, allowed models, status), create through the usual change-summary-and-reason flow, and
rotate/revoke with a reason. Creating or rotating shows the new secret exactly once, with a copy button and a
warning that it cannot be shown again; the UI never stores it beyond that one dialog. A department viewer or
representative sees the same list read-only; writes they attempt are rejected by the server as usual. The
department's Production tab (system keys) is Step 3.6.

Step 3.6 adds the department's "ייצור" (Production) tab, shown to the admin and the department's
representative. It shows the production organization's wallet card (budget, used, remaining, allocated to
systems, sync) and a table of systems (budget, used, key count, sync). The admin can set the production
budget, add a system (both through change summary and a required reason) and open a per-system keys drawer
(same list/create/rotate/revoke and one-time secret as the group tab). The representative sees everything
read-only; until the organization is set, an empty state is shown.

### 4.10 Directory (section 5.1, 9.1) - Phase 3: In progress (Step 3.1)

A new `DIRECTORY_BACKEND` setting switches the one place Wallet Manager reads AD: `fixture` (dev/test,
unchanged) or `ldap` (LDAPS, read-only). The directory backs three things, still with no periodic sync:
the one-time onboarding lookup, owners' names shown on wallets, and ADFS login roles when the token carries
no groups claim. If the directory cannot be reached, onboarding and member-target writes answer 503
`DIRECTORY_UNAVAILABLE` and change nothing; a wallet still shows the correct owner role with empty names;
and a login with no groups claim falls back to an employee-only session instead of failing.

## 5. Non-functional

Explicit LiteLLM client boundary (NFR3), outbox with idempotent retries, audit of every change, CSRF + CSP,
RTL accessibility, `./scripts/test.sh` (pytest, vitest, Playwright) green before each commit.
