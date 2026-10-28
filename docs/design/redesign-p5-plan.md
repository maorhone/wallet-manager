Approved by the owner 28/10/2026

# Phase 5 redesign plan (transfers + commitments), for the owner's approval

Written 28/10/2026 10:22 (Israel time). Planning only: nothing is built until the owner approves.
Basis: review-phase5-split-r12..r18.md, the r18 verdict JSONs, PROGRESS.md, OPEN-ITEMS.md, and the code at origin/phase-5 90f5db3.
Track status: wallet-manager-p5 is held in the queue file plus a `HOLD` file.

## (a) Why the loop stopped converging: root-cause themes, r12–r18

All 41 blockers and majors from r12 to r18 fall into these themes (B = blocker, M = major).

| #   | Theme                                                                                                                                | r12 | r13   | r14   | r15 | r16 | r17 | r18   | Total                            |
| --- | ------------------------------------------------------------------------------------------------------------------------------------ | --- | ----- | ----- | --- | --- | --- | ----- | -------------------------------- |
| T1  | Transfer compensation and repair (transfers.py, 0016 migration, transfer idempotency)                                                | 1M  | 1B 1M | 1B 1M | 1B  | 1B  | 1B  | 1B    | **6B 3M = 9**                    |
| T2  | Allocation and commitment accounting (group_allocated vs consumption, fresh spend, PMO split, key generations, tree and move checks) | 1B  | 1M    | 1M    | –   | 1B  | 4B  | 3B 1M | **9B 3M = 12**                   |
| T3  | Lost-response idempotency and sync status in the UI (keys regenerated, outcomes lost)                                                | 2M  | 1M    | –     | –   | –   | 1M  | 1M    | **5M**                           |
| T4  | Historical and overview reconstruction (history.py, overview.py)                                                                     | 1M  | –     | 2M    | 1M  | 1M  | 1M  | –     | **6M**                           |
| T5  | Access scope (owner, managing-group head, delegated systems, service-key reads)                                                      | 1M  | –     | –     | –   | 1M  | 2M  | –     | **4M**                           |
| T6  | Service-key generations (telemetry)                                                                                                  | –   | 1M    | –     | –   | –   | –   | –     | 1M (r18 B3a is counted under T2) |
| T7  | Missing spec UI (SystemDetail failures, Department action center)                                                                    | 1M  | –     | –     | –   | –   | –   | 1M    | 2M                               |
| T8  | Rate card currency and concurrency                                                                                                   | –   | –     | –     | –   | 1M  | 1M  | –     | 2M                               |

Diagnosis:

- **T1 and T2 account for 15 of the 16 blockers.**
- **T1.** The transfer lifecycle is implicit. It is spread across `Transfer.state`, `LitellmWrite.status` and `attempts`, idempotency-key name patterns (`LIKE 'transfer:%:restore-target:%'`), and 7 helper predicates: `held_by_incoming`, `_repair_unconfirmed`, `_retire_superseded`, `source_holdback`, `fence_release`, `settle` and `_undo`. Each fix added one more predicate, and the reviewer then found the next edge case. r18 B1a1 is the canonical example: a write that was possibly applied upstream and then failed permanently is treated as "never applied".
- **T2.** There are two parallel accounting helpers. One is `validation.group_allocated` / `group_own_allocated`, which has no consumption. The other is `transfers.group_committed`, which is spend-aware. Each round moved some call sites from the first to the second, and the reviewer found the next one: tree, move, rotation, or the PMO split.

## (b) Transfer compensation and repair as an explicit state machine

**Core idea.** A pure transition function, `step(transfer, legs, observation) -> (new_state, actions)`, with no I/O. It is wrapped by a thin executor that applies the actions under the existing lock order (department → group → production → child groups by id, all FOR UPDATE).

**New table `transfer_leg`.** Columns: transfer_id, role (`source` | `target` | `revert_target` | `restore_source`), entity, write_id, state, entity_version.

**Leg states:**

- `QUEUED`
- `IN_FLIGHT`
- `UNCERTAIN`: an attempt was made and no definitive answer came back. **A later permanent error keeps it UNCERTAIN, never FAILED.**
- `CONFIRMED`: applied, confirmed by response or by read-back.
- `REJECTED`: provably never applied, i.e. failed before any send, or read-back shows the old value.

**Transfer states and transitions:**

| From           | Event                                                                        | To                      | Actions                                                                                                                                            |
| -------------- | ---------------------------------------------------------------------------- | ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| —              | create (plan passed, locked)                                                 | `RESERVED`              | Domain source −a and target +a(incoming, not spendable); queue source leg                                                                          |
| RESERVED       | source CONFIRMED                                                             | `SOURCE_APPLIED`        | —                                                                                                                                                  |
| RESERVED       | source REJECTED                                                              | `COMPENSATING`          | —                                                                                                                                                  |
| RESERVED       | source UNCERTAIN                                                             | (stay)                  | Reconcile by read-back. Never compensate on a guess.                                                                                               |
| SOURCE_APPLIED | fence ok (fresh spend, commitment check)                                     | `PUBLISHING`            | Queue target leg (absolute value, carries entity_version)                                                                                          |
| SOURCE_APPLIED | fence: late spend                                                            | `COMPENSATING`          | —                                                                                                                                                  |
| SOURCE_APPLIED | spend unreadable                                                             | (stay)                  | Fail closed                                                                                                                                        |
| PUBLISHING     | target CONFIRMED (own write, or any later write whose entity_version ≥ ours) | `SETTLED`               | Incoming becomes spendable                                                                                                                         |
| PUBLISHING     | target REJECTED                                                              | `COMPENSATING`          | —                                                                                                                                                  |
| PUBLISHING     | target UNCERTAIN                                                             | (stay)                  | Read-back                                                                                                                                          |
| COMPENSATING   | enter                                                                        | (stay)                  | Domain revert under lock. Queue `revert_target` for every target leg ≠ REJECTED. `restore_source` **depends on all revert_target legs CONFIRMED**. |
| COMPENSATING   | all revert_target CONFIRMED and restore_source CONFIRMED                     | `COMPENSATED`           | —                                                                                                                                                  |
| COMPENSATING   | revert_target cannot be confirmed (permanent error after uncertainty)        | `STUCK`                 | Source stays reduced (safe direction: credit is temporarily lost, never duplicated). Operator notification.                                        |
| any            | operator resolve                                                             | `COMPENSATED`/`SETTLED` | Audited                                                                                                                                            |

**Idempotency:**

- Every transition is a conditional UPDATE `WHERE state = <from>`.
- Every leg has a deterministic key `transfer:{id}:{role}:{n}`.
- Every upstream write is an absolute value rebuilt from domain rows plus an `entity_version`. Confirming version v confirms every leg included at ≤ v.
- This one rule replaces `_retire_superseded`, `held_by_incoming` and `_repair_unconfirmed`.

**Invariants** (checked by the tests in (e) after every step):

1. **Conservation.** For every entity, the worst-case upstream allowance must stay within the funding domain:
   - **Worst case.** UNCERTAIN legs count at the higher of their old and new values.
   - **Funding domain.** Domain totals plus the credit of transfers in RESERVED or SOURCE_APPLIED.
   - **Meaning.** Credit is never spendable in two places.
2. Incoming credit is not spendable, allocatable or key-reservable before `SETTLED`.
3. Source restore is never published before every revert_target leg is CONFIRMED.
4. Unreadable spend means no transition.
5. Transitions are monotone. A replay or a second worker is a no-op.
6. Once faults stop, every transfer reaches a terminal state: SETTLED, COMPENSATED or STUCK.

**Read-back reconcile.** The worker already owns a `LiteLLMReader` (worker.py). An `UNCERTAIN` leg is resolved by reading the current upstream budget and comparing it with the expected value. It is not resolved by retry guessing.

## (c) One authoritative commitment check

New `app/services/commitments.py`. It is the only module allowed to compute "how much of a group is committed".

- `snapshot(group, spend: FreshSpend)` returns a breakdown:
  - **consumption**, split by attribution.py into base-funded and PMO-funded (fixes B1c);
  - **outstanding unspent allocations**: member caps, active initiatives, group keys over **all generations** (`retired_budget`, fixes B3a), and children at `max(budget, spent)`;
  - **pending incoming**, which is excluded.
- `assert_fits(group, delta: ProposedChange)` takes a proposed change: caps, child budgets, a new parent, a new key or rotation, or an approval. It runs under the group lock, with **fresh spend required (503 when unreadable)**, and compares against settled base × factor.
- **Call sites routed through it:**
  - members.set_caps and bulk;
  - service_keys create and rotate for every owner kind;
  - initiatives create and budget;
  - both request approvals;
  - departments.set_split;
  - `check_group_tree` for parents of changed children (B1b);
  - platform.move_group (B3b);
  - the transfer plan, fence and compensation parent check (replaces `group_allocated` in `_overallocates_parent`; the worker has a reader).
- `group_allocated` and `group_own_allocated` are deleted, or kept display-only. A guard test fails if any service imports them.

## (d) Service-key and scope authorization

T5 is a recurring theme (4 majors), mostly at a small scale. Plan:

- One policy table `can(principal, action, resource)`. Roles: owner, managing-group head, department rep, admin, AI team. Actions are split into read and manage.
- The frontend renders summaries independently of restricted sub-resources.
- One parametrized test covers the full role × action matrix.

This is a small unit and not a redesign.

## (e) Test strategy

- **Transfers.** A Hypothesis `RuleBasedStateMachine` over the pure core, plus the executor, against `fake_litellm` with fault injection:
  - faults: apply-and-lose-response, transient error, permanent error after an uncertain attempt, late source spend, a sibling receipt, duplicate or crashed workers, reorder;
  - check all 6 invariants after every rule;
  - include explicit regression examples for r12–r18 T1.
  - Hypothesis is a new free dev dependency.
- **Commitments.** Generated org trees with random members, keys (with rotations), initiatives, departed members, revoked keys, PMO shares and consumption. Random mutation sequences go through the public services and are compared with a brute-force reference oracle built from the ledger:
  - an accepted op must satisfy the bound;
  - a rejected op must actually violate it. This catches false rejects like B1c.
- **UI idempotency.** One `useIdempotentMutation(scope, payload)` hook keeps its key until a confirmed response. Tests cover a lost response with sync still pending (F1 BulkAllocation), and the existing OrgAllocation and decision hooks move onto it.
- **The 5 deterministic Playwright failures:** department.spec:91 and :101, platform.spec:5 and :108, reports.spec:74.
  - They have failed since r2.
  - Cause: the specs raise the department budget to 1,100,000, which the org envelope added in phase 5 refuses (the UI shows 1,000,000).
  - Fix: update the e2e seed and amounts (or the envelope) to match spec v2, not the backend.
- **Quarantined load flakes.** DB-setup timeouts in 4 tests. Fix them with a per-worker template DB or a pool and timeout fix, then un-quarantine.
- **Gate.** A green full run (pytest + vitest + e2e) before the final review.

## (f) Mapping of the open r18 findings

| r18 finding                                                            | Severity | Resolved by                                                                               |
| ---------------------------------------------------------------------- | -------- | ----------------------------------------------------------------------------------------- |
| B1a1 transfers.py:530: failed-after-uncertain destination not repaired | blocker  | (b) a leg stays UNCERTAIN, restore_source depends on revert_target CONFIRMED, invariant 3 |
| B1b validation.py:174: child funding ignores consumption               | blocker  | (c) `assert_fits` in check_group_tree                                                     |
| B3b platform.py:340: move_group ignores consumption                    | blocker  | (c) `assert_fits(new_parent, +child)`                                                     |
| B3a service_keys.py:241: group-key rotation not reserved               | blocker  | (c) all generations counted, and rotation checked                                         |
| B1c requests.py:368: PMO-funded consumption counted as base            | major    | (c) base and PMO split in the snapshot                                                    |
| F1 BulkAllocation.tsx:73: key regenerated per preview                  | major    | (e) `useIdempotentMutation`                                                               |
| F1 Department.tsx:58: no action center                                 | major    | Unit U4: spec D-050/D-051 UI (small, independent)                                         |

## (g) Build steps

Each unit is one Claude Code session on Pro at medium effort, followed by one focused Codex review (read-only, high effort, scoped to the unit diff plus its module).

- **Fix limit per unit:** at most 2 fix iterations.
- **Out-of-scope findings** go to a backlog, never into the loop.

| Unit | Content                                                                                                                                                     | Effort          | Codex review                                                  |
| ---- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------- | ------------------------------------------------------------- |
| U0   | Design doc in repo (`docs/design/transfers-v2.md`, `commitments.md`): states, invariants, oracle                                                            | 0.5 session     | Review the doc first (cheap, catches design gaps before code) |
| U1   | `commitments.py`, route all call sites, delete group_allocated from services, property tests + oracle                                                       | 1–2 sessions    | B1b/B1c/B3a/B3b scope                                         |
| U2   | Pure transfer state machine + Hypothesis model tests (no wiring)                                                                                            | 1 session       | core only                                                     |
| U3   | Executor + outbox wiring + migration 0018 (map existing pending/applied/compensated/failed to new states, legs from write_ids); delete the old 7 predicates | 1–2 sessions    | transfers + worker                                            |
| U4   | Authz policy table, `useIdempotentMutation`, Department action center                                                                                       | 1 session       | B2 + F1                                                       |
| U5   | Fix the 5 e2e specs and DB-setup flakes; one full run                                                                                                       | 0.5–1 session   | none (tests only)                                             |
| U6   | One full split re-review (all chunks) and merge per rule                                                                                                    | about 1 h Codex | full                                                          |

Total: about 5–7 Claude sessions plus 6–7 Codex reviews, roughly 1.5–2 days at the current Pro and ChatGPT limits.

**Branch.** A new branch `phase-5-redesign` cut from phase-5 at 90f5db3.

- **Kept:** all phase-5 feature work, which is 176 commits and about 19.7k lines; only the transfers and validation internals are rewritten.
- **Rejected alternative:** restarting phase 5 would throw away about a week of correct feature work.
- **Why a new branch:** phase-5 stays untouched as a fallback.

## (h) Open decisions for the owner

1. **Approve this plan and the branch approach.** The proposal is phase-5-redesign cut from phase-5; the alternative is rebuilding the transfers module from scratch.
2. **r18 decision 1 (compensation):**
   - A compensated source restore stays held until destination repairs are confirmed. If a repair fails permanently, the transfer stays held for an operator (STUCK).
   - **Recommend: keep (safe direction), and add an admin "resolve stuck transfer" action.** Also recommend that the compensation parent check use fresh spend, since the worker has a reader.
3. **r18 decision 2:**
   - A cap save that changes nothing skips the over-allocation check.
   - Allocation increases return 503 when LiteLLM spend cannot be read.
   - **Recommend: keep both.**
4. **Merge rule after U6.**
   - Same as 27/10: merge at 0 blockers, and majors go to cleanup-p5.
   - Alternative: also require 0 transfer and commitment majors.
5. **Scope of UI majors (T3, T4, T7).** The proposal is to include only the r18 ones. Earlier history and overview items are fixed and stay as they are.
6. **Hypothesis as a new dev dependency.** It is free and open source.
7. **Older open product questions** (OPEN-ITEMS):
   - Transfers between internal systems and members or initiatives are refused (5.9).
   - Throughput limits on targeted requests are not pushed to LiteLLM.
   - Recommend leaving both for after the merge.
