# Transfers v2: explicit state machine (contract for U2 and U3)

Source: `docs/design/redesign-p5-plan.md` §(b), §(e). Decisions of 28/10 (r18 decision 1) are fixed:
a compensated source restore stays held until EVERY destination repair is CONFIRMED; a permanent
error after an uncertain attempt leads to STUCK (credit temporarily lost, never duplicated); an
admin can resolve STUCK transfers; the compensation parent re-check uses FRESH spend.

Code references are to branch `phase-5-redesign` at 90f5db3 (`app/services/transfers.py` unless noted).

## 1. Model

A transfer moves `amount` of base credit from a `source` entity to a `target` entity inside one
section (employee, initiative, group, system; initiative ends have no upstream write).
Upstream effects are **legs**, one row per LiteLLM write the transfer causes.

### 1.1 Leg roles

| Role             | Meaning                                      | Value written (absolute)                                   |
| ---------------- | -------------------------------------------- | ---------------------------------------------------------- |
| `source`         | lower the source entity's upstream allowance | source domain total (minus unsettled incoming, see inv. 2) |
| `target`         | raise the target's allowance                 | target domain total incl. settled credit only              |
| `revert_target`  | lower the target back after compensation     | target domain total after revert                           |
| `restore_source` | raise the source back after compensation     | source domain total after restore                          |

Entities without an upstream (initiative) have no leg for that end; the domain row is the truth.

### 1.2 Leg states

`QUEUED` (not yet sent) → `IN_FLIGHT` (claimed by a worker) → `CONFIRMED` | `REJECTED` | `UNCERTAIN`.

- `UNCERTAIN`: an attempt was made and no definitive answer came back (timeout, 5xx, lost
  response). **Sticky:** a later permanent error keeps it `UNCERTAIN`. It becomes `CONFIRMED`
  or `REJECTED` only by response of a retry that succeeds, or by read-back (section 5).
- `CONFIRMED`: applied upstream (response, or read-back equals the expected value, or a later
  write with `entity_version >= ours` was confirmed, see 3.3).
- `REJECTED`: provably never applied: failed permanently with zero attempts (never sent), or
  read-back shows the old value after the attempt window.

Table `transfer_leg(id, transfer_id, role, n, entity, entity_id, write_id, state,
entity_version, expected_value, old_value, key)`; unique `(transfer_id, role, n)`.

## 2. Transfer states and the full transition table

States: `RESERVED`, `SOURCE_APPLIED`, `PUBLISHING`, `SETTLED` (terminal), `COMPENSATING`,
`COMPENSATED` (terminal), `STUCK` (terminal for workers; only an operator leaves it).

Events are observations of legs/spend, computed by the executor and passed to the pure
`step(transfer, legs, obs) -> (state', actions)`. `obs` = leg states, `spend` (`FRESH(value)` or
`UNREADABLE`), `commit_ok` (commitment check result), `now`, `operator` (resolve request).

Actions (data, executed by the executor): `QueueLeg(role, n)`, `RevertDomain`,
`MarkIncomingSpendable`, `Notify(kind)`, `Audit(kind)`, `ReadBack(leg)`.

| #   | State                | Event                                                                                                                                             | New state                                               | Actions                                                                                                                                                                                                                                                                                                                                                                                            |
| --- | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | —                    | create (plan passed under locks)                                                                                                                  | `RESERVED`                                              | domain: source −a, target +a (incoming, unspendable); `QueueLeg(source,0)` if source has an upstream; audit                                                                                                                                                                                                                                                                                        |
| 1b  | —                    | create, no legs at all (initiative↔initiative)                                                                                                    | `RESERVED`, then immediately row 4                      | domain only                                                                                                                                                                                                                                                                                                                                                                                        |
| 2   | RESERVED             | source leg CONFIRMED                                                                                                                              | `SOURCE_APPLIED`                                        | —                                                                                                                                                                                                                                                                                                                                                                                                  |
| 3   | RESERVED             | source leg REJECTED                                                                                                                               | `COMPENSATING`                                          | —                                                                                                                                                                                                                                                                                                                                                                                                  |
| 3b  | RESERVED             | source leg UNCERTAIN                                                                                                                              | RESERVED                                                | `ReadBack(source)`; never compensate on a guess                                                                                                                                                                                                                                                                                                                                                    |
| 3c  | RESERVED             | source QUEUED/IN_FLIGHT                                                                                                                           | RESERVED                                                | —                                                                                                                                                                                                                                                                                                                                                                                                  |
| 4   | SOURCE_APPLIED       | spend FRESH, source not overspent (`commitment` snapshot of source ≤ source total − unsettled incoming)                                           | `PUBLISHING`                                            | `QueueLeg(target,0)` (none if target has no upstream: go to row 6 at once)                                                                                                                                                                                                                                                                                                                         |
| 5   | SOURCE_APPLIED       | spend FRESH, source overspent (late spend)                                                                                                        | `COMPENSATING`                                          | —                                                                                                                                                                                                                                                                                                                                                                                                  |
| 5b  | SOURCE_APPLIED       | spend UNREADABLE                                                                                                                                  | SOURCE_APPLIED                                          | — (fail closed, invariant 4)                                                                                                                                                                                                                                                                                                                                                                       |
| 6   | PUBLISHING           | target leg CONFIRMED (own, or a later leg of the same entity with `entity_version ≥` ours)                                                        | `SETTLED`                                               | `MarkIncomingSpendable`; notify                                                                                                                                                                                                                                                                                                                                                                    |
| 7   | PUBLISHING           | target leg REJECTED                                                                                                                               | `COMPENSATING`                                          | —                                                                                                                                                                                                                                                                                                                                                                                                  |
| 7b  | PUBLISHING           | target leg UNCERTAIN                                                                                                                              | PUBLISHING                                              | `ReadBack(target)`                                                                                                                                                                                                                                                                                                                                                                                 |
| 7c  | PUBLISHING           | target QUEUED/IN_FLIGHT                                                                                                                           | PUBLISHING                                              | —                                                                                                                                                                                                                                                                                                                                                                                                  |
| 8   | COMPENSATING         | enter (first step in this state; guarded by `compensation_started=false`)                                                                         | COMPENSATING                                            | `RevertDomain` under locks (source +a, target −a; refuse if a balance would go negative or the current parent would overflow per fresh-spend `assert_fits` → row 12); `QueueLeg(revert_target,n)` for every `target` leg whose state ≠ REJECTED (QUEUED target legs are cancelled=REJECTED instead, never sent); `QueueLeg(restore_source,n)` with `depends_on` = all revert_target legs CONFIRMED |
| 9   | COMPENSATING         | every revert_target CONFIRMED (or none exist) and restore_source CONFIRMED (or source has no upstream)                                            | `COMPENSATED`                                           | notify                                                                                                                                                                                                                                                                                                                                                                                             |
| 9b  | COMPENSATING         | some revert_target UNCERTAIN                                                                                                                      | COMPENSATING                                            | `ReadBack(that leg)`; restore_source stays held                                                                                                                                                                                                                                                                                                                                                    |
| 10  | COMPENSATING         | revert_target cannot be confirmed: its definitive failure is a permanent error AND read-back is impossible or inconclusive (leg stayed UNCERTAIN) | `STUCK`                                                 | source stays reduced; `Notify(stuck)` to admins; audit                                                                                                                                                                                                                                                                                                                                             |
| 11  | COMPENSATING         | restore_source REJECTED/permanent error after all revert_target CONFIRMED                                                                         | `STUCK`                                                 | as row 10 (source credit lost, never duplicated)                                                                                                                                                                                                                                                                                                                                                   |
| 12  | COMPENSATING         | `RevertDomain` refused (balance would be negative, parent overflow with FRESH spend)                                                              | `STUCK`                                                 | no balance change; notify admins                                                                                                                                                                                                                                                                                                                                                                   |
| 12b | COMPENSATING         | spend UNREADABLE while row 8 needs the parent check                                                                                               | COMPENSATING                                            | — (retry later)                                                                                                                                                                                                                                                                                                                                                                                    |
| 13  | STUCK                | operator resolve → COMPENSATED                                                                                                                    | `COMPENSATED`                                           | requires read-back proof (section 7)                                                                                                                                                                                                                                                                                                                                                               |
| 14  | STUCK                | operator resolve → SETTLED                                                                                                                        | `SETTLED`                                               | requires read-back proof                                                                                                                                                                                                                                                                                                                                                                           |
| 15  | any non-terminal     | operator resolve                                                                                                                                  | per 13/14 only from STUCK; on other states reject (409) |
| 16  | SETTLED, COMPENSATED | any event                                                                                                                                         | same                                                    | no-op (monotone, invariant 5)                                                                                                                                                                                                                                                                                                                                                                      |
| 17  | any                  | event whose `from` state does not match the stored state                                                                                          | same                                                    | no-op (stale replay)                                                                                                                                                                                                                                                                                                                                                                               |

Notes. Rows 4/5 implement the late-spend fence (D-069). Row 8's "revert_target for every target leg
≠ REJECTED" is what replaces `_release_cancelled`'s `attempts > 0` guess (lines 523–550): a leg
that was ever attempted is UNCERTAIN until proven otherwise.

## 3. Idempotency rules

1. **Conditional UPDATE.** Every state change is
   `UPDATE transfer SET state=:to WHERE id=:id AND state=:from RETURNING id`; no row returned means another actor already moved it: do nothing, run no actions. Leg state changes likewise (`WHERE state=:from`).
2. **Deterministic key.** Leg `n` of role `r` of transfer `t` has `idempotency_key =
transfer:{t}:{r}:{n}` (unique index on `litellm_write.idempotency_key` where not null);
   creating a leg twice is a no-op.
3. **Absolute values + `entity_version`.** A leg never carries a delta. The worker rebuilds the
   payload from domain rows under the money locks (as `build` does today, `app/outbox/worker.py:165`)
   and stamps the write with `entity_version` = a per-entity counter incremented in the same
   transaction as every domain change of that entity's budget (`entity_version` column on
   employee, grp, production_system). A leg records the version its payload was built at.
4. **Confirming version v confirms every leg ≤ v.** When a write for entity E with version `v` is
   CONFIRMED, every leg of E whose `entity_version ≤ v` and is not terminal becomes CONFIRMED
   (the absolute payload included their effect). This single rule replaces `_retire_superseded`
   (719), `held_by_incoming` (611) and `_repair_unconfirmed` (588): there is no waiting between
   transfers, only version order. A confirmation of a _revert_ leg at version v likewise
   confirms older target legs as **applied-and-reverted** only if the version includes the revert
   (the revert has the higher version by construction).
5. **Replay.** Replaying any event, or two workers racing, changes nothing after the first
   winner (rule 1).

## 4. Executor and locks

The executor is the only code that reads/writes `transfer`, `transfer_leg` and balances for a
transfer. One call = one transaction:

1. Lock in the existing order (`lock_scope`, lines 93–100): DEPARTMENT, GROUP (the section),
   PRODUCTION (when a system is an end), then child groups by ascending id; a group endpoint that
   was moved also locks its CURRENT parent group (as `_undo` does, lines 403–410), after the
   others and in id order. All `SELECT … FOR UPDATE`.
2. Load transfer + legs, compute `obs` (spend via `fresh_spend(db, reader, now)`; `SpendUnavailable` → `UNREADABLE`).
3. `step(...)`; for each action: apply the conditional transition first, then actions.
4. Commit. Only after commit may a worker perform LiteLLM calls (legs are sent from the outbox,
   never inside this transaction); each call's outcome is an observation for the next executor call.

Entry points: `create` (HTTP), `advance(transfer_id)` (worker, after every leg outcome and on a
periodic sweep for non-terminal transfers), `resolve(transfer_id, outcome, reason)` (admin).

## 5. Read-back reconcile

The worker owns a `LiteLLMReader` (`app/worker.py:20`). An `UNCERTAIN` leg is resolved by reading
the entity's current upstream budget and comparing with the leg's `expected_value` (new) and
`old_value`:

- equals `expected_value` → `CONFIRMED`;
- equals `old_value` and the attempt window has passed (no in-flight request: leg not `IN_FLIGHT`
  for > `outbox_inflight_timeout`) → `REJECTED`;
- anything else (a third value, reader down) → stays `UNCERTAIN` (never guess).

Because values are absolute and versions are ordered, a value that equals a _later_ version's
expected value also confirms this leg (rule 3.4). Reads never change upstream state.

## 6. Fresh-spend fence

Every forward step that could let credit become usable (rows 4, 5, 8 parent check) reads spend
with `fresh_spend`. `SpendUnavailable` or a missing reader means **no transition** (rows 5b, 12b).
The fence compares with the commitment snapshot (see `commitments.md`): source overspent ⇔
`commitment(source).total > source_total − unsettled_incoming(source)` (replaces `_source_overspent`,
lines 512–520).

## 7. Admin "resolve stuck transfer"

- Endpoint `POST /transfers/{id}/resolve` body `{outcome: "COMPENSATED"|"SETTLED", reason}`.
  Admin only (`p.is_admin`); `reason` mandatory (non-empty, ≤ 500 chars); `Idempotency-Key`
  required (same key → same response, no second effect).
- Allowed only from `STUCK`; any other state → 409. Executed by the executor under the full lock
  order, conditional UPDATE `WHERE state='STUCK'`.
- **Proof by read-back.** The executor reads upstream for the transfer's source and target:
  - `COMPENSATED` allowed only if target upstream = its post-revert expected value AND source
    upstream = its restored expected value (or the executor can write the missing restore now:
    queue `restore_source` after proof of the target revert, state stays STUCK until CONFIRMED);
  - `SETTLED` allowed only if target upstream = its post-transfer expected value and the source
    upstream = its reduced value (domain unchanged since).
  - Reader unavailable or values inconsistent → 409 `RESOLVE_NOT_PROVEN`, no change.
- Audited (`AuditLog`: actor, outcome, reason, observed values, before/after state) and notified.
- Visibility: STUCK transfers appear in the existing platform operations screen for admins
  (frontend `pages/platform/Operations.tsx` list) and the transfer `GET` returns `state: STUCK`
  with `needs_operator: true`. On entering STUCK the executor calls
  `notifications.notify(...)` (`app/services/notifications.py:36`) for
  `ai_team_or_admins` (line 104) with kind `transfer_stuck`, dedupe key `transfer:{id}:stuck`.

## 8. Invariants (testable predicates)

Let, per entity E, `domain(E)` = base total in the domain row, `settled(E)` = `domain(E)` minus
credit of E's incoming transfers not yet `SETTLED`; `legs(E)` = legs of E not REJECTED.

1. **Conservation.** `upstream_worst(E) ≤ funding(E)` where `upstream_worst(E)` = the largest
   absolute value among: the last CONFIRMED leg value of E, and every UNCERTAIN leg's
   `expected_value` (UNCERTAIN counts at max(old,new)); and
   `funding(E) = domain(E) + Σ amount of transfers T with T.source = E and T.state ∈ {RESERVED, SOURCE_APPLIED}`
   (the source's released credit is still its own until the target leg is queued). Corollary: for
   one transfer, Σ over source+target of upstream-worst deltas ≤ 0 + credit in flight, i.e. credit
   is never spendable in two places.
2. **Incoming is inert.** For a transfer T not `SETTLED`: no leg, commitment check, key reservation
   or allocation counts T.amount as available on T.target (`settled(E)` is used everywhere).
3. **Restore after revert.** `restore_source` leg state ≠ `QUEUED→IN_FLIGHT/CONFIRMED` unless every
   `revert_target` leg of the transfer is `CONFIRMED`.
4. **No unreadable-spend transition.** Any step from `SOURCE_APPLIED` or any parent check taken
   with `spend=UNREADABLE` leaves state unchanged (checked on the model by comparing before/after).
5. **Monotone.** State order `RESERVED < SOURCE_APPLIED < PUBLISHING < SETTLED`;
   `COMPENSATING < COMPENSATED`; `COMPENSATING < STUCK`; terminal states never change except
   `STUCK→COMPENSATED|SETTLED` by operator. Leg states never leave terminal (`CONFIRMED`,
   `REJECTED`); `UNCERTAIN` never goes to `REJECTED` without a read-back event. Replaying any
   recorded event is a no-op.
6. **Liveness.** From any reachable state, with faults disabled and enough steps (bound
   `4 × legs + 4`), every transfer is in `SETTLED`, `COMPENSATED` or `STUCK`.

## 9. Hypothesis model for U2

`RuleBasedStateMachine` over the pure core only (no DB): bundles `transfers` and `entities`
(1–4 entities with `domain`, `upstream` absolute values, `version`).

Rules (each calls `step` or the in-memory executor and then runs all invariant checks):

- `create_transfer(src, dst, amount)` precondition `amount ≤ unused(src)`;
- `deliver(leg)`: leg applied upstream and response received (CONFIRMED);
- `apply_and_lose_response(leg)`: upstream value changes, leg → UNCERTAIN;
- `transient_error(leg)`: nothing applied, attempts+1, leg stays IN_FLIGHT/UNCERTAIN if attempted;
- `permanent_error(leg)`: REJECTED if never attempted else stays UNCERTAIN (and upstream may or may not have changed);
- `late_source_spend(entity, amount)`: spend increases so the fence fails;
- `spend_unreadable(flag)`: toggles `UNREADABLE`;
- `sibling_receipt(target)`: second transfer to the same target (exercises version confirmation);
- `duplicate_worker(transfer)`, `crash_after_commit`, `crash_before_commit`: re-run `advance` / drop a step;
- `reorder`: process queued legs in any order, respecting `depends_on`;
- `readback(leg)`: resolve UNCERTAIN by comparing upstream (rules in section 5);
- `operator_resolve(transfer, outcome)`: only with proof;
- `heal()`: disable faults and run `advance` until quiescent (precondition for the liveness check).

Invariant placement: 1–5 in an `@invariant()` after every rule; 6 in `heal()`'s postcondition
and in a terminal `teardown`. Settings: `derandomize=True`, `deadline=None`, `max_examples=200`
(state machine `stateful_step_count=30`), no wall clock (time is an integer counter in `obs`).

## 10. Mapping from the current implementation

### 10.1 Predicates

| Old (file:line)                                               | What it does today                                                            | New rule                                                                                                 |
| ------------------------------------------------------------- | ----------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `held_by_incoming` (611)                                      | holds a write of a target while another transfer's credit is unsettled        | removed: legs are absolute+versioned (3.3/3.4); inert incoming by inv. 2                                 |
| `_repair_unconfirmed` (588)                                   | holds restored source until `restore-target:%` writes applied (LIKE on key)   | row 8: `restore_source` depends on all `revert_target` legs CONFIRMED; inv. 3                            |
| `_retire_superseded` (719)                                    | marks sibling destination writes applied when a later aggregate write applies | rule 3.4 (confirm all legs ≤ v)                                                                          |
| `source_holdback` (558) / `apply_holdback` (579)              | subtract unsettled incoming from a source write payload                       | `settled(E)` is the written value (inv. 2); no payload mutation                                          |
| `fence_release` (676)                                         | late-spend re-read before raising destination; may cancel siblings            | rows 4/5/5b                                                                                              |
| `settle` (764)                                                | pending → applied when all writes applied; initiative fence                   | rows 2, 6; initiative ends have no leg                                                                   |
| `_undo` (394)                                                 | domain restore, held writes failed, `failed` when impossible                  | row 8 `RevertDomain`; row 12 → STUCK                                                                     |
| `_overallocates_parent` (460)                                 | parent check with `group_allocated`                                           | `commitments.assert_fits` with fresh spend (row 8/12)                                                    |
| `compensate_failed_source` (485) / `_release_cancelled` (523) | queue `restore-source`/`restore-target:{id}` writes by key pattern            | rows 3, 5, 8 legs with keys `transfer:{id}:{role}:{n}`                                                   |
| `lock_for_write` (650)                                        | worker takes transfer locks before building                                   | executor/`advance` lock order (section 4); the outbox build for a leg keeps calling the same lock helper |

### 10.2 State backfill (migration 0018)

`Transfer.state` ∈ {pending, applied, failed, compensated} (`app/models.py:457`; migration
`alembic/versions/0016_transfer_state.py:20–34`); `LitellmWrite.status` ∈ {pending, applied,
failed} (`app/outbox/worker.py:122,171,180`). `first_write_id` is the source write
(`transfers.py:332–337`); `write_ids[1:]` are destination writes (`_undo` line 414).

Legs are created from `write_ids`: `write_ids[0] == first_write_id` → role `source`; others → `target`
(role by position; initiative-origin transfers have no source leg). Leg state from the write:
`applied` → CONFIRMED; `pending` and `attempts = 0` → QUEUED; `pending` and `attempts > 0` →
UNCERTAIN; `failed` and `attempts = 0` → REJECTED; `failed` and `attempts > 0` → UNCERTAIN
(sticky rule). Keys for existing writes keep their old values; new legs use the new pattern.
Writes with keys `transfer:%:restore-source` / `restore-target:%` become `restore_source` /
`revert_target` legs (n = 0).

| Old `Transfer.state` | Condition                                     | New state                                                                         |
| -------------------- | --------------------------------------------- | --------------------------------------------------------------------------------- |
| `applied`            | all legs CONFIRMED or no legs                 | `SETTLED`                                                                         |
| `pending`            | source leg QUEUED/IN_FLIGHT                   | `RESERVED`                                                                        |
| `pending`            | source CONFIRMED, any target leg not terminal | `PUBLISHING` (fence re-run by the executor; no target leg yet → `SOURCE_APPLIED`) |
| `pending`            | source UNCERTAIN                              | `RESERVED` (read-back)                                                            |
| `pending`            | source REJECTED                               | `COMPENSATING`                                                                    |
| `compensated`        | no restore/revert writes, or all CONFIRMED    | `COMPENSATED`                                                                     |
| `compensated`        | a `restore-target` write not applied          | `COMPENSATING` (restore_source held)                                              |
| `failed`             | `_undo` refused (domain not restored)         | `STUCK` (backfill sets `stuck_reason='legacy_failed'`)                            |
| `failed`             | target write failed after attempts            | `STUCK`                                                                           |

The migration is idempotent (re-runnable), adds `entity_version` columns defaulting to 0, and
downgrade restores `Transfer.state` by the inverse of this table (`SETTLED→applied`,
`RESERVED/SOURCE_APPLIED/PUBLISHING→pending`, `COMPENSATING/COMPENSATED→compensated`, `STUCK→failed`).
`transfer_status` (357) is rebuilt from `state`: SETTLED→done; COMPENSATED/STUCK→failed;
others→pending_apply. The check constraint `ck_transfer_state` is replaced with the new set.

## 11. Regression list (T1, r12–r18): one scenario each

Each becomes a named test in U2 (pure/model) or U3 (executor against `fake_litellm`).

1. r12 `transfers.py:687`: transfer A's source applies, its target write fails permanently; transfer B to the same target then publishes an aggregate. A's target leg is CONFIRMED by version rule 3.4 only if B's version ≥ A's, else A compensates (no credit lost, no duplication).
2. r13 `transfers.py:490`: fence compares the source's consumption only; the source group has budget 10, spend 6, unspent allocations 4: after reduction by 4 → must be overspent (row 5) because the snapshot includes allocations.
3. r13 `transfers.py:391`: two transfers from different sources to one target; one target write failed permanently; late source spend above the released credit → compensation must succeed (a REJECTED/failed target leg does not block row 8).
4. r14 `transfers.py:290`: employee transfer without `Idempotency-Key` → 400 `IDEMPOTENCY_KEY_REQUIRED`; same key twice → one transfer.
5. r14 `0016_transfer_state.py:30`: backfill sets `first_write_id` for an initiative-origin transfer; in 0018 an initiative-origin transfer has no `source` leg.
6. r15 `transfers.py:622`: `fence_release` and `settle` pass when `reader is None` → with no reader every step from SOURCE_APPLIED holds (5b, inv. 4); includes `scripts/seed.py` draining the queue.
7. r16 `transfers.py:398`: a pending destination write was applied upstream (timeout, `attempts>0`), late source spend → compensation queues `revert_target` for the UNCERTAIN leg before `restore_source`.
8. r17 `transfers.py:527`: restore-target and restore-source with no dependency → restore_source depends on revert_target CONFIRMED (inv. 3); a failing repair holds the restore.
9. r18 `transfers.py:530`: destination applied upstream, response lost, retry gets a permanent error → leg stays UNCERTAIN (not FAILED), compensation queues revert_target; if revert cannot be confirmed → STUCK with source still reduced.
10. r8 B1a1 (earlier): an earlier source write publishes this reduction inside its rebuilt aggregate; compensation restoring only the domain leaves upstream reduced → `restore_source` leg always queued on compensation (row 8).
11. Replay: the same worker outcome delivered twice, or two workers advancing one transfer → one state change, one set of keys.
12. Compensation parent check with unreadable spend → holds (12b); with fresh spend and overflowing parent → STUCK (12).
13. Reciprocal transfers A→B and B→A in flight at once → no circular wait (no cross-transfer holds exist); both terminal.
14. Moved group endpoint: compensation locks and checks the CURRENT parent (`_undo` lines 403–410 behaviour preserved).
15. Operator resolve: idempotent replay, non-admin 403, missing reason 422, reader down 409, inconsistent values 409, valid proof → terminal state + audit + notification.
