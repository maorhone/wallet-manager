# Wallet Manager for LiteLLM: Product Requirements (living document)

Source of truth for requirements is SPEC.md; this file summarises the product and tracks status per area.
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

### 4.2 Wallets and reports (FR-E, FR-R, FR-V) - Phase 1: Done

- "My wallet": base cap, shares, total, used, remaining, reset date, binding constraint (self/group/department), owners' names.
- Group and department summaries, member tables, history timeline, usage charts from a spend snapshot.
- Over-allocation report against live LiteLLM values; status, outbox and metrics endpoints.
- Share rows in the wallet show project, model, amount, used (attributed), expiry and days left - Done (Step 2.2).

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
- UI for PMO and group heads - Planned (Steps 2.6-2.9).

### 4.5 Scheduler - Phase 2: In progress (grants job Done in Step 2.3)

Separate process (`python -m app.scheduler`) with an injectable clock; one replica runs a job at a time (advisory lock) and a
restart catches up on everything overdue. Grants job (every 60 s): activation, share and grant expiry, then one recompute of the
member's temporary budget (earliest remaining expiry) per affected person. Metrics: `wm_scheduler_job_runs_total`,
`wm_scheduler_job_last_success_timestamp`. Notifications and reconciliation jobs run alongside grants.

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

### 4.7 Notifications (FR-N) - Phase 2: Backend Done (Step 2.4); UI Planned (Step 2.10)

In-app notifications, deduplicated per person and event. Written by the scheduler job (every 15 min): employee at 80% / 100%
of their total (the employee), group at 80% / 100% (group heads), grant expiring in 7 days and grant under 20% used with 7 days
left (group heads and the department's PMO), failed outbox rows (platform admins). Written with the action itself: a user left
pending placement (the department's representatives, or platform admins when the department is unknown), a member moved into a
group (the new group head), a grant created (the group heads). 100% and outbox-failure kinds are critical and cannot be muted;
the others can (`PUT /me/notification-prefs`). API: `GET /me/notifications?unread=`, `POST /me/notifications/{id}/read`.
Optional email copy (`NOTIFY_EMAIL_ENABLED`, off by default, needs an internal relay). Bell and preferences UI: Step 2.10.
The dev seed can add a sample grant (`SEED_GRANTS=1`; the live preview does).

### 4.8 UI screens - Phase 1/1b: Done; Phase 2: Planned

RTL Hebrew React app: my wallet, group (overview, members with actions, history), department (overview, split, pending),
scope switcher, change-summary dialog. Planned: PMO page (pool, grants, projects), group Grants tab with share dialogs,
department group-heads tab, notifications.

### 4.9 Production and service keys (FR-K) - Later phase: Planned

Production organizations, service keys, rotate/revoke.

## 5. Non-functional

Explicit LiteLLM client boundary (NFR3), outbox with idempotent retries, audit of every change, CSRF + CSP,
RTL accessibility, `./scripts/test.sh` (pytest, vitest, Playwright) green before each commit.
