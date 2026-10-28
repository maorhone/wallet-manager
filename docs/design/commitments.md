# Commitments: one authoritative check (contract for U1) and the U4 scope

Source: `docs/design/redesign-p5-plan.md` §(c), §(d), §(e). Decisions of 28/10 are fixed:
r18 decision 2 (a cap save that changes nothing skips the over-allocation check; allocation
increases return 503 `SPEND_UNAVAILABLE` when LiteLLM spend cannot be read), and the compensation
parent re-check uses fresh spend. File:line references are to `phase-5-redesign` at 90f5db3.

## 1. API (`app/services/commitments.py`)

The only module allowed to compute "how much of a group is committed".

```python
@dataclass(frozen=True)
class Snapshot:                      # all Decimal, all >= 0
    consumption_base: Decimal        # the group's own spend funded from its base budget
    consumption_pmo: Decimal         # the part funded by PMO grants (attribution.py)
    member_caps_unspent: Decimal     # Σ max(base_cap + shares − used, 0), non-"left" members
    initiatives_unspent: Decimal     # Σ max(budget − used, 0), active initiatives
    keys_unspent: Decimal            # group keys over ALL generations: see 2.3
    children: Decimal                # Σ max(child.base_budget, child.spent), non-archived children
    pending_incoming: Decimal        # reported, EXCLUDED from total
    @property
    def total(self) -> Decimal:      # committed against the settled base
        return (self.consumption_base + self.member_caps_unspent + self.initiatives_unspent
                + self.keys_unspent + self.children)

async def snapshot(db, group, spend: Spend, *, tz, overrides: Overrides | None = None) -> Snapshot
async def assert_fits(ctx, reader, clock, group, change: ProposedChange) -> None   # raises ApiError
```

`ProposedChange` is a tagged union; `overrides` applies it to the snapshot without writing:

| Variant                              | Fields                        | Effect on the snapshot                                                                  |
| ------------------------------------ | ----------------------------- | --------------------------------------------------------------------------------------- |
| `Caps`                               | `{upn: new_base_cap}`         | member caps replaced (new members added)                                                |
| `ChildBudgets`                       | `{child_id: new_base}`        | children replaced (cuts and raises)                                                     |
| `NewChild`                           | `child_id, base, from_parent` | adds a child at `max(base, spent)`; used by move_group on the new parent                |
| `RemoveChild`                        | `child_id`                    | removes a child (move_group on the old parent: always fits)                             |
| `NewKey`                             | `max_budget` (group-owned)    | + `max_budget` to `keys_unspent`                                                        |
| `RotateKey`                          | `key_id`                      | + the key's current `max_budget` (the old generation keeps reserving, `retired_budget`) |
| `NewInitiative` / `InitiativeBudget` | `budget` / `id, budget`       | initiatives replaced/added                                                              |
| `Approval`                           | `kind ("cap"                  | "initiative"), target, amount`                                                          | + amount on the target |
| `Credit`                             | `amount`                      | plain increase (transfer plan/compensation restore)                                     |

`assert_fits` semantics, in order:

1. If the change leaves every affected figure unchanged (`overrides` equal to the stored values), return without reading spend or checking (r18 decision 2).
2. Take the group lock (callers already hold the department lock; the order is `lock_scope`,
   `transfers.py:93`). Refresh the group row after the lock.
3. Read fresh spend (`fresh_spend(db, reader, now)`, `spend.py:122`). `SpendUnavailable` or a
   missing reader → `ApiError(503, "SPEND_UNAVAILABLE", ...)` (copy of the message at
   `transfers.py:181`). Never treat unreadable spend as zero.
4. `check_sum(snapshot.total, settled_budget(group), group.overalloc_factor)` (`validation.py`),
   where `settled_budget = base_budget − pending_incoming − held_restore` (`validation.py:123–128`; both terms as defined in
   `transfers-v2.md` 1.1b, so a restored-but-held source credit is not capacity). Over-allocation
   factor and the "advanced" per-model quotas stay as today.

`assert_fits` is the single place that raises `OVER_ALLOCATED`-style errors for groups. Preview
endpoints use `snapshot` with cached spend (`Spend.load`) and never raise 503.

## 2. Exact definitions

### 2.1 Consumption, base vs PMO

`spend.used("group", id)` is the group team's own spend including its members' and keys' spend.
It is split with `attribution.attribute` (`attribution.py:28`): for each member,
`attribute(member_spend, base_cap, shares)` yields the part funded by active/closed PMO grant
shares; `consumption_pmo = Σ over members of attributed share usage`, and
`consumption_base = group_spend − consumption_pmo`. Only `consumption_base` counts against the
group's base budget, so PMO-funded spend is never charged as base (fixes r18 B1c,
`requests.py:368`). Spend of departed members (`status="left"`) and revoked keys stays in
`group_spend` and therefore stays in `consumption_base`.

### 2.2 Outstanding unspent allocations

Unspent = what an allocation still holds above what it already consumed (spent credit is counted
once, in consumption):

- member caps: `max(base_cap − used(employee), 0)` for non-`left` members (grant shares are not
  base and are excluded here, as today with `with_shares=False`, `transfers.py:122–136`);
- active initiatives: `max(budget − initiative_used, 0)`, with `initiative_used` counting all keys
  including revoked and moved ones (`transfers.py:142–165`);
- children: `max(child.base_budget, child.spent)` for non-archived children (`transfers.py:137–141`).

### 2.3 Group keys, all generations

For a group-owned key in `active`/`pending` status the reserved amount is
`max_budget + retired_budget` (every earlier generation can still spend until deleted), minus the
key's spend across all generations (spend is merged by key), floored at 0:
`keys_unspent += max(max_budget + retired_budget − used(key), 0)`. Rotation adds
`max_budget` to `retired_budget` only after `assert_fits(RotateKey)` passes (fixes r18 B3a,
`service_keys.py:241`; current rotation covers only initiative and system owners, lines 225–240).
Revoked/deleted keys contribute 0 unspent; their spend stays in consumption.

### 2.4 Pending incoming

`pending_incoming(group)` (`validation.py`) is credit transferred in whose transfer is not
`SETTLED` and whose domain credit has not been reverted (`transfers-v2.md` 1.1b; v1: not
`applied`): RESERVED, SOURCE_APPLIED, PUBLISHING, and also COMPENSATING or STUCK transfers
before `RevertDomain` (a refused or delayed revert, rows 12/12b, leaves the credit in the domain
and it must stay unavailable). Transfers after `RevertDomain` (any state, COMPENSATED included)
are excluded: their credit already left the domain balance, so subtracting it would shrink
capacity forever. A source's `held_restore` (reverted COMPENSATING/STUCK, `restore_source` not
CONFIRMED) is subtracted as well. Neither is part of `total`; both are subtracted from the
capacity: `settled_budget = base_budget − pending_incoming − held_restore`. They can neither be
allocated nor key-reserved (invariant 2 of `transfers-v2.md`).

## 3. Locking and fresh spend

- Lock order for any call: DEPARTMENT, GROUP (the section), PRODUCTION when a system is
  involved, then other groups by ascending id (`transfers.py:93–100`). `assert_fits` takes the
  group lock if the caller has not (idempotent re-lock) and refreshes rows afterwards.
- Fresh spend is required for every check that can increase a group's commitments. Cuts
  (`ChildBudgets` that only lower, `Caps` that only lower) also use it, since a cut may not go
  below consumption (`BELOW_CONSUMED`, `departments.py:100–106`).
- The worker (compensation parent check) passes its own `LiteLLMReader`.

## 4. Call sites (complete list; current file:line → target)

All become `commitments.assert_fits(...)` (or `snapshot` for display) in U1.

| Call site                                     | Current                                                                                                 | Change variant                                                                                                                                                                                              |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| members.set_caps                              | `members.py:108` `fresh_group_committed(..., employee_caps=new)` + `check_sum` 109                      | `Caps`                                                                                                                                                                                                      |
| members bulk apply                            | `bulk.py:72` `group_committed(..., employee_caps=new)` (preview, cached spend) + `bulk.py:73` available | `snapshot` w/ `Caps` for the preview; the commit path goes through `members.set_caps`                                                                                                                       |
| service_keys.create (group owner)             | `service_keys.py:179–184`                                                                               | `NewKey`                                                                                                                                                                                                    |
| service_keys.create/rotate (initiative owner) | `service_keys.py:174–176`, `225–229` (`initiative_committed`)                                           | `NewKey` / `RotateKey` on the initiative scope (budget = initiative budget − incoming); initiative scope stays its own small snapshot in `commitments.py`                                                   |
| service_keys.change rotate (group owner)      | **missing** (`service_keys.py:222–241` covers initiative and system only)                               | `RotateKey` (new; fixes B3a)                                                                                                                                                                                |
| service_keys system owner                     | `service_keys.py:190`, `235–238` (`key_allocated` vs `owner.budget`)                                    | stays a system-scope check, moved into `commitments.py` as `assert_fits_system` using `production.system_committed`                                                                                         |
| initiatives.create                            | `initiatives.py:46–49`                                                                                  | `NewInitiative`                                                                                                                                                                                             |
| initiatives budget update                     | none today (no update function in `initiatives.py`; `grep "^async def"` shows only create/list/get)     | `InitiativeBudget` once an update exists; U1 adds nothing but the variant                                                                                                                                   |
| request approval, cap                         | `requests.py:366–370`                                                                                   | `Approval(cap)` (fixes B1c via PMO split)                                                                                                                                                                   |
| request approval, initiative                  | `requests.py:406–410`                                                                                   | `Approval(initiative)`                                                                                                                                                                                      |
| departments.set_split                         | `departments.py:100–106` (`group_committed(child_budgets=new)`), `147` (`check_group_tree`)             | `ChildBudgets` per changed group + tree check                                                                                                                                                               |
| `check_group_tree`                            | `validation.py:160–180`, callers `departments.py:147`, `platform.py:327`                                | rewritten: department-level sums stay; each parent of a changed child uses `assert_fits`-equivalent `snapshot` (children at `max(budget, spent)`, consumption, keys) instead of `group_own_allocated` (B1b) |
| platform.move_group                           | `platform.py:331–340` → `_check_budgets` 319–329 → `check_group_tree`                                   | `NewChild` on the new parent, `RemoveChild` on the old (B3b)                                                                                                                                                |
| transfers plan                                | `transfers.py:210` (`_side`, `group_committed`)                                                         | `snapshot` (unused = total − committed)                                                                                                                                                                     |
| transfers fence                               | `transfers.py:519` (`_source_overspent`)                                                                | `snapshot` with fresh spend                                                                                                                                                                                 |
| transfers compensation                        | `transfers.py:477` (`_overallocates_parent`, `group_allocated`)                                         | `assert_fits(parent, Credit)` with fresh spend                                                                                                                                                              |
| overview display                              | `overview.py:101` (`group_committed`, shares)                                                           | `snapshot` (display only; may use `with_shares`)                                                                                                                                                            |
| `group_committed`, `fresh_group_committed`    | `transfers.py:103`, `169`                                                                               | deleted; `bulk.py:17`, `departments.py:14`, `overview.py:24`, `requests.py:35`, `initiatives.py:19`, `service_keys.py:179`, `members.py:23` imports move to `commitments`                                   |
| `group_allocated`, `group_own_allocated`      | `validation.py:146`, `108`; imports `transfers.py:57`; uses `validation.py:174`                         | deleted, or kept in one display-only helper (`overview` if needed); guard test                                                                                                                              |

(The grep for `group_allocated|group_own_allocated|group_committed|fresh_group_committed|_overallocates_parent|check_group_tree` over `app/` at this commit is the source of this table.)

## 5. Guard test

`tests/test_commitments_guard.py`: parse every `app/services/*.py` (ast) except `commitments.py`
and `validation.py`; fail if any imports or references `group_allocated` or `group_own_allocated`
(allow-list: a single display-only helper, named in the test), or defines/imports
`group_committed`/`fresh_group_committed` from `transfers`. Also fail if `validation.check_group_tree`
still calls `group_own_allocated`.

## 6. Brute-force reference oracle

`tests/oracle.py` computes the bound directly from raw ledger rows, never calling `commitments`:

- Input: all employees, keys (with `retired_budget`), initiatives, children, grants/shares,
  per-entity spend rows, pending-incoming transfers of a generated org tree.
- For a group: `spent_g = spend(group)`; `pmo_g = Σ_members pmo_m`, where `pmo_m` is an
  independent loop (not the clamped closed form): `left = max(member_spend − current base_cap, 0)`;
  walk the member's shares by `(expires_at, id)`; a **closed** share contributes its stored `used`
  (never recomputed) and reduces `left` by it (floored at 0); an **active** share contributes
  `min(left, amount)` and reduces `left`. Generated cases must include a cap raised or lowered
  after a share closed, and spend below the cap with a closed share holding stored usage;
  `unspent = Σ_caps max(cap − spend_m, 0) + Σ_initiatives max(budget − Σ keys spend, 0) + Σ_keys max(max_budget + retired − spend_k, 0) + Σ_children max(base, spend_child)`;
  `committed = (spent_g − pmo_g) + unspent`;
  `capacity = (base_budget − pending_incoming − held_restore) × factor`, with `pending_incoming`
  (not SETTLED, not domain-reverted, incl. COMPENSATING/STUCK before revert) and `held_restore`
  computed from the raw transfer rows; generated orgs include held restores and unreverted
  compensating/stuck receipts.
- Decision for an operation: apply it to a copy of the rows, recompute `committed` for the
  affected group(s) and, for tree ops (child cut/raise, move, split), for each parent; **accept ⇔
  committed ≤ capacity for all of them AND no cut floor is violated** (a no-op change accepts
  without evaluating, per decision 2; unreadable spend → reject with 503). Cut floors (section 3,
  `BELOW_CONSUMED`): a lowered member cap or child budget must stay ≥ that entity's consumption plus
  its unsettled incoming (`members._check_cut_floors`); the oracle computes them per entity from
  raw rows. Expected rejections are classified as `OVER_ALLOCATED` (group bound) or
  `BELOW_CONSUMED` (floor); e.g. group budget 100, member spend 60, cap cut 80 → 50: committed
  60 fits, the floor rejects.
- Property tests (`tests/test_commitments_property.py`, Hypothesis, `derandomize=True`,
  `deadline=None`, `max_examples=150`, no wall-clock): generate trees (≤ 3 levels, ≤ 4 members,
  ≤ 3 keys with rotations, initiatives, departed members, revoked keys, PMO shares, spend), a
  random mutation sequence through the public service functions. Assert:
  an accepted op satisfies the oracle bound (no over-allocation); a rejected op, with readable
  spend, actually violates the group bound or a cut floor, and the error code matches the violated
  rule (no false rejects, e.g. B1c); `snapshot(...).total == oracle.committed`.

## 7. U4 scope (authz, UI idempotency, Department action center)

### 7.1 Authz policy table `can(principal, action, resource)`

New `app/authz.py`; the table maps (role, action) to allow/deny, where roles are resolved once
per request: `admin`, `ai_team`, `dept_rep`, `group_head`, `managing_group_head`, `owner`
(resource owner), `org_allocator`, `viewer`.

| Resource / action                  | admin                  | ai_team | dept_rep     | group_head | managing_group_head | owner                | org_allocator   | viewer         |
| ---------------------------------- | ---------------------- | ------- | ------------ | ---------- | ------------------- | -------------------- | --------------- | -------------- |
| system: read summary               | ✓                      | ✓       | ✓ (own dept) | –          | ✓                   | ✓                    | ✓ (org)         | –              |
| system: read keys/limits           | ✓                      | –       | ✓            | –          | ✓                   | ✓                    | ✓               | –              |
| system: manage keys                | ✓                      | –       | –            | –          | –                   | –                    | ✓ (org systems) | –              |
| group key / initiative key: read   | ✓                      | –       | ✓            | ✓          | –                   | ✓ (initiative owner) | –               | ✓ (view roles) |
| group key / initiative key: manage | ✓                      | –       | –            | ✓          | –                   | –                    | –               | –              |
| telemetry: read                    | = key read (same rows) |         |              |            |                     |                      |                 |                |

(U4 verifies each cell against the current checks and adjusts the table to the current behaviour
where it differs, noting the difference in PROGRESS.md; the table is the single source after U4.)

Current checks it replaces: `can_view_service_keys` `app/permissions.py:330–363`,
`can_manage_service_keys` `permissions.py:366–382`, `can_manage_org_systems` `permissions.py:385–391`,
`can_view_production` `permissions.py:286` (used `production.py:243`), the call sites
`service_keys.py:167` (create), `215` (change), `261/263` (list), `307` (telemetry), and the
telemetry route `routers/service_keys.py:37`. One parametrized test `tests/test_authz_matrix.py`
covers role × action × owner kind. The frontend renders the summary part independently of
restricted sub-resources (a 403 on keys must not blank the page).

### 7.2 `useIdempotentMutation(scope, payload)`

`frontend/src/api/useIdempotentMutation.ts`. Contract:

- Keeps a key per `scope` (string) in a ref/session store, created lazily.
- The same key is sent for every attempt while the payload (stable JSON string) is unchanged and
  no confirmed response was received (network error, timeout, 5xx, lost response).
- A confirmed success OR a confirmed business error (4xx other than 409 idempotency conflict)
  clears the key; a changed payload renews it.
- A 409 `IDEMPOTENCY_REPLAY`/already-applied response counts as confirmed success.
- Returns `{mutate, mutateAsync, reset, key}`; no behavioural change for callers besides the key.
  Migrations: `BulkAllocation.tsx:27–28,63` (`useBulkAllocation`, F1 r18 line 73),
  `OrgAllocation.tsx:137` (`useOrgAllocate`), and the request-decision hook
  (`frontend/src/api/queries.ts` `useDecideRequest`). Tests: lost response with sync pending →
  same key on retry; changed payload → new key; confirmed success → new key for the next call.

### 7.3 Department action center (spec D-050 / D-051)

Spec: `docs/spec-v2/AI_VISIBILITY_MANAGEMENT_SOURCE_OF_TRUTH.md:45–46` (summaries), `781–789`
(§5.1.1 information hierarchy), `791–800` (§5.1.2 three groups), `1302–1304` (decisions D-050,
D-051), `1397–1404`. The Department page (`frontend/src/v2/pages/Department.tsx`, r18 F1 line 58)
opens with the budget snapshot (4 KPIs, D-054) and below it an "דורש את תשומת לבך" area with three
groups: (1) requests awaiting handling (real requests with scope, requester, date, status; never
shown as approved before applied), (2) shortage risks (balance near zero for member/initiative/
section; conditional indicator, no predicted exhaustion dates), (3) reallocation opportunities
(unused grant alongside another need; a suggestion to review, never an automatic action).
Data comes from existing endpoints (requests list, overview, reports); an empty group shows an
explicit empty state; a failing sub-request must not blank the other groups. Out of scope for U4:
new thresholds beyond what the spec lists as open (O-061).
