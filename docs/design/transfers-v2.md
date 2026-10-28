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

| Role             | Meaning                                      | Value written (absolute)                                  |
| ---------------- | -------------------------------------------- | --------------------------------------------------------- |
| `source`         | lower the source entity's upstream allowance | `payload(E)` (below)                                      |
| `target`         | raise the target's allowance                 | `payload(E)`; includes this transfer's own receipt        |
| `revert_target`  | lower the target back after compensation     | `payload(E)` after RevertDomain (receipt no longer there) |
| `restore_source` | raise the source back after compensation     | `payload(E)` with this transfer's held restore released   |

Entities without an upstream (initiative) have no leg for that end; the domain row is the truth.

### 1.1b Receipts, held restores and the payload (single shared definition)

Per transfer T the domain row carries two flags: `domain_reverted` (set by `RevertDomain`, row 8)
and the state. Per entity E:

- `pending_incoming(E)` = Σ amount of T with `T.target = E`, `T.state ≠ SETTLED` and **not**
  `domain_reverted`. So it holds RESERVED/SOURCE_APPLIED/PUBLISHING **and every COMPENSATING or
  STUCK receipt whose `RevertDomain` has not happened** (row 12 and 12b leave the balances
  unchanged: the receipt is still in `domain(E)` and must stay unavailable until it is reverted or
  settled). This is the only meaning of "pending incoming" everywhere (commitments, allocation
  checks, upstream payloads). A SETTLED receipt is spendable; a receipt whose domain credit was
  reverted (`domain_reverted`, any state, incl. COMPENSATED) is **not** counted (the credit is no
  longer in `domain(E)`, so subtracting it again would shrink capacity forever). Invariant: a
  transfer is COMPENSATED only with `domain_reverted` (rows 9 and 13).
- `held_restore(S)` (availability hold) = Σ amount of T with `T.source = S`, `T.state ∈
{COMPENSATING, STUCK}`, `domain_reverted`, and `restore_source` not CONFIRMED (a source with no
  upstream: until every revert_target is CONFIRMED). It is restored in the domain at once but is
  **not available**: the destination may still hold the uncertain extra credit.
- `unreleased_restore(S)` (publication hold, ⊆ `held_restore`) = the same T while some
  revert_target leg is not CONFIRMED. The hold on publication ends before the hold on availability:
  once every destination repair is CONFIRMED the restore may enter a payload (any write of S,
  including `restore_source` itself), while S can still not spend it until `restore_source` is
  CONFIRMED (a write whose `included` has the restore_source leg confirms it, 3.4).
- `activated(E)` = receipts of T with `T.target = E`, `T.state ∈ {PUBLISHING, SETTLED}`, not
  `domain_reverted` (the transfer's own target leg has been queued by row 4).
- **`payload(E)`** (the absolute value of EVERY write of E, whatever the leg role) =
  `domain(E) − Σ unreleased_restore(E) − Σ unreverted receipts of E in {RESERVED, SOURCE_APPLIED,
COMPENSATING, STUCK}`. So a receipt joins the payload when it is activated (row 4), leaves it at
  once when its transfer compensates (no write of E may publish credit that is about to be
  reverted or whose domain revert was refused), and the restore joins when released (row 9
  condition: every revert_target CONFIRMED). Availability for spend/allocation is stricter:
  `settled(E) = domain(E) − held_restore(E) − pending_incoming(E)`.
- Each written leg records `included`: the leg ids whose effect that payload contained (target
  legs of `activated(E)`, revert_target legs of reverted transfers, restore_source legs whose
  publication hold was released, source legs of transfers whose domain reduction is in `domain(E)`).

### 1.2 Leg states

`QUEUED` (not yet sent) → `IN_FLIGHT` (claimed by a worker) → `CONFIRMED` | `REJECTED` | `UNCERTAIN`.

- `UNCERTAIN`: an attempt was made and no definitive answer came back (timeout, 5xx, lost
  response). **Sticky:** a later permanent error keeps it `UNCERTAIN`. It becomes `CONFIRMED`
  or `REJECTED` only by response of a retry that succeeds, or by read-back (section 5).
- `CONFIRMED`: applied upstream (response, or read-back equals the expected value, or a later
  write whose `included` set contains this leg was confirmed, see 3.4).
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

| #   | State                | Event                                                                                                                                                                                                                                                                   | New state                                               | Actions                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| --- | -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | —                    | create (plan passed under locks)                                                                                                                                                                                                                                        | `RESERVED`                                              | domain: source −a, target +a (incoming, unspendable); `QueueLeg(source,0)` if source has an upstream; audit                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 1b  | —                    | create, source has no upstream leg (initiative source, any target; incl. initiative↔initiative)                                                                                                                                                                         | `RESERVED`, then immediately row 2b                     | domain only                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| 2   | RESERVED             | source leg CONFIRMED                                                                                                                                                                                                                                                    | `SOURCE_APPLIED`                                        | —                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| 2b  | RESERVED             | source has no upstream leg (absent source leg)                                                                                                                                                                                                                          | `SOURCE_APPLIED`                                        | — (then row 4's fresh-spend fence; for initiative↔initiative row 4 goes on to row 6 at once)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 3   | RESERVED             | source leg REJECTED                                                                                                                                                                                                                                                     | `COMPENSATING`                                          | —                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| 3b  | RESERVED             | source leg UNCERTAIN                                                                                                                                                                                                                                                    | RESERVED                                                | `ReadBack(source)`; never compensate on a guess                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| 3c  | RESERVED             | source QUEUED/IN_FLIGHT                                                                                                                                                                                                                                                 | RESERVED                                                | —                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| 4   | SOURCE_APPLIED       | spend FRESH, source not overspent (`commitment` snapshot of source ≤ source total − unsettled incoming)                                                                                                                                                                 | `PUBLISHING`                                            | `QueueLeg(target,0)` (none if target has no upstream: go to row 6 at once)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| 5   | SOURCE_APPLIED       | spend FRESH, source overspent (late spend)                                                                                                                                                                                                                              | `COMPENSATING`                                          | —                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| 5b  | SOURCE_APPLIED       | spend UNREADABLE                                                                                                                                                                                                                                                        | SOURCE_APPLIED                                          | — (fail closed, invariant 4)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| 6   | PUBLISHING           | target leg CONFIRMED (own, or a later write of the same entity whose `included` contains this leg, see 3.4; its payload carried this receipt, so the domain credit and the upstream credit agree)                                                                       | `SETTLED`                                               | `MarkIncomingSpendable`; notify                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| 7   | PUBLISHING           | target leg REJECTED                                                                                                                                                                                                                                                     | `COMPENSATING`                                          | —                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| 7b  | PUBLISHING           | target leg UNCERTAIN                                                                                                                                                                                                                                                    | PUBLISHING                                              | `ReadBack(target)`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| 7c  | PUBLISHING           | target QUEUED/IN_FLIGHT                                                                                                                                                                                                                                                 | PUBLISHING                                              | —                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| 8   | COMPENSATING         | enter (first step in this state; guarded by `compensation_started=false`)                                                                                                                                                                                               | COMPENSATING                                            | `RevertDomain` under locks (source +a held as `held_restore`, target −a, set `domain_reverted`: the receipt leaves `pending_incoming` and the restore is excluded from every source payload until every revert_target is CONFIRMED (`unreleased_restore`) and from availability until restore_source is CONFIRMED (`held_restore`), 1.1b; refuse if a balance would go negative or the current parent would overflow per fresh-spend `assert_fits` → row 12); `QueueLeg(revert_target,n)` for every `target` leg whose state ≠ REJECTED (QUEUED target legs are cancelled=REJECTED instead, never sent); `QueueLeg(restore_source,n)` with `depends_on` = all revert_target legs CONFIRMED |
| 9   | COMPENSATING         | every revert_target CONFIRMED (or none exist): the publication hold is released (restore joins `payload(source)`, so the `restore_source` write carries it; availability stays held); then restore_source CONFIRMED (or source has no upstream) releases `held_restore` | `COMPENSATED`                                           | notify                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 9b  | COMPENSATING         | some revert_target UNCERTAIN                                                                                                                                                                                                                                            | COMPENSATING                                            | `ReadBack(that leg)`; restore_source stays held                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| 10  | COMPENSATING         | revert_target cannot be confirmed: its definitive failure is a permanent error AND read-back is impossible or inconclusive (leg stayed UNCERTAIN)                                                                                                                       | `STUCK`                                                 | source stays reduced; `Notify(stuck)` to admins; audit                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 11  | COMPENSATING         | restore_source REJECTED/permanent error after all revert_target CONFIRMED                                                                                                                                                                                               | `STUCK`                                                 | as row 10 (source credit lost, never duplicated)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| 12  | COMPENSATING         | `RevertDomain` refused (balance would be negative, parent overflow with FRESH spend)                                                                                                                                                                                    | `STUCK`                                                 | no balance change, `domain_reverted` stays false: the receipt stays in `pending_incoming` and out of every payload (unavailable, never published); notify admins                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| 12b | COMPENSATING         | spend UNREADABLE while row 8 needs the parent check                                                                                                                                                                                                                     | COMPENSATING                                            | — (retry later; the receipt stays pending and out of every payload meanwhile)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 13  | STUCK                | operator resolve → COMPENSATED                                                                                                                                                                                                                                          | `COMPENSATED`                                           | requires read-back proof and `domain_reverted` (reverted atomically by the resolve if needed; section 7)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| 14  | STUCK                | operator resolve → SETTLED (only with `domain_reverted=false` and read-back proof, section 7)                                                                                                                                                                           | `SETTLED`                                               | requires read-back proof                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| 15  | any non-terminal     | operator resolve                                                                                                                                                                                                                                                        | per 13/14 only from STUCK; on other states reject (409) |
| 16  | SETTLED, COMPENSATED | any event                                                                                                                                                                                                                                                               | same                                                    | no-op (monotone, invariant 5)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 17  | any                  | event whose `from` state does not match the stored state                                                                                                                                                                                                                | same                                                    | no-op (stale replay)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |

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
4. **Confirming a write confirms the legs it _included_.** When a write for entity E with version
   `v` is CONFIRMED, every non-terminal leg of E that is in that write's `included` set (1.1b)
   becomes CONFIRMED. A higher version alone proves nothing: payloads deliberately exclude
   unactivated incoming and held restores, so a later outgoing write can omit an older uncertain
   incoming target leg or restore; such a leg stays unconfirmed (its own retry or read-back
   decides) and its transfer does not settle. Inclusion is recorded from the same locked
   snapshot the payload was built from. This rule replaces `_retire_superseded` (719),
   `held_by_incoming` (611) and `_repair_unconfirmed` (588): no waiting between transfers, only
   explicit inclusion. A revert leg's confirmation confirms an older target leg as
   applied-and-reverted only when the revert write's `included` lists that target leg's transfer
   as reverted.
5. **Replay.** Replaying any event, or two workers racing, changes nothing after the first
   winner (rule 1).
6. **Per-entity application fence (single flight).** The locks are released before the upstream
   call, and LiteLLM has no compare-and-set, so ordering is enforced by never having two writes of
   one entity outstanding. A leg of entity E is claimed (QUEUED→IN_FLIGHT) only inside the locked
   transaction that also builds its payload, stamps `entity_version` and records `included`, and
   only if **no other leg of E is IN_FLIGHT or UNCERTAIN**. An UNCERTAIN leg of E blocks every
   later write of E until read-back (section 5) makes it CONFIRMED or REJECTED (an unreadable
   upstream keeps E blocked: fail closed, the operator sees the blocked legs). A request counts as
   dead only after the attempt window (`outbox_inflight_timeout` ≥ the client timeout plus margin),
   so a delayed older request cannot apply after a newer confirmed payload. Consequence: the
   upstream value of E follows the order of `entity_version`, and rule 4's `included` sets are
   always cumulative in that order. Model rule `delayed_old_request`: an IN_FLIGHT/UNCERTAIN
   request may apply at any moment; the concurrency model must show that no second request of the
   same entity can exist at that moment.
7. **No sending before the fence.** A `target` leg is claimable only while its transfer is
   `PUBLISHING` (row 4 or the backfill ran the fresh-spend fence); a `restore_source` leg only
   after its `depends_on` revert_target legs are CONFIRMED.

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

A value that equals a _later_ write's expected value confirms this leg only if that write's
`included` set contains it (rule 3.4); otherwise the leg stays UNCERTAIN. Reads never change upstream state.

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
- **Proof by read-back and by the domain.** The executor reads upstream for the transfer's source
  and target and checks the domain, all under the full lock order:
  - `COMPENSATED` allowed only if target upstream = its post-revert expected value AND source
    upstream = its restored expected value (or the executor can write the missing restore now:
    queue `restore_source` after proof of the target revert, state stays STUCK until CONFIRMED),
    AND the domain is reverted: if `domain_reverted=false` (row 12 stuck) the resolve performs
    `RevertDomain` in the same transaction as the transition, with the same balance checks and the
    FRESH-spend parent check as row 8; if that is refused or spend is unreadable → 409
    `RESOLVE_NOT_PROVEN`, still STUCK, nothing changed. Read-back values alone never complete
    COMPENSATED: the transfer's receipt stays pending (unavailable) until the domain is reverted.
  - `SETTLED` allowed only if `domain_reverted=false` (domain still holds source −a, target +a)
    AND target upstream = its post-transfer expected value AND the source upstream = its reduced
    value. `domain_reverted=true` → 409 (the credit already left the domain; use COMPENSATED).
    SETTLED releases the receipt (`MarkIncomingSpendable`) in the same transaction.
  - Reader unavailable or values inconsistent → 409 `RESOLVE_NOT_PROVEN`, no change.
- Audited (`AuditLog`: actor, outcome, reason, observed values, before/after state) and notified.
- Visibility: STUCK transfers appear in the existing platform operations screen for admins
  (frontend `pages/platform/Operations.tsx` list) and the transfer `GET` returns `state: STUCK`
  with `needs_operator: true`. On entering STUCK the executor calls
  `notifications.notify(...)` (`app/services/notifications.py:36`) for
  `ai_team_or_admins` (line 104) with kind `transfer_stuck`, dedupe key `transfer:{id}:stuck`.

## 8. Invariants (testable predicates)

Let, per entity E, `domain(E)` = base total in the domain row, `pending_incoming`, `held_restore`,
`settled(E)` and `payload(E)` are as defined in 1.1b (a reverted receipt is not pending;
a held restore is neither payload nor available); `legs(E)` = legs of E not REJECTED.

1. **Conservation (over the whole model world, not per entity).**
   `Σ_E w(E) ≤ Σ_E domain(E)`, where `w(E)` = `upstream_worst(E)` for an entity with an upstream
   (the largest absolute value among: the last CONFIRMED leg value of E, and every UNCERTAIN leg's
   `expected_value`, UNCERTAIN counting at max(old,new)) and `w(E) = settled(E)` for an entity
   without one (initiative). The domain is the single ledger of credit; each unit of credit is
   therefore owned by exactly one place, whatever the state of a transfer. Per-entity bounds are
   not required: during compensation the credit of one transfer legitimately sits in the source's
   upstream (source write rejected or not yet applied) while the domain already shows the revert.
   Required model examples (U2): _safe, must pass_ — (a) source previously 100, transfer 10, source
   write REJECTED: upstream 100/100, domain 90/110 (before `RevertDomain`) and 100/100 after it
   with the restore still held; (b) source reduced to 90 and the target raised uncertainly to 110,
   domain 100/100 after revert, restore held: 90+110 ≤ 200; (c) STUCK row 10/11/12 states.
   _Counterexamples, must fail the predicate_ — the restore published while a revert_target is
   UNCERTAIN (source 100 + target 110 > 200), a target published before the source reduction is
   applied (src 100 + tgt 110 > 200), and any duplicate-credit step the model can generate.
2. **Incoming is inert.** For a transfer T not `SETTLED`: no leg, commitment check, key reservation
   or allocation counts T.amount as available on T.target (`settled(E)` is used everywhere),
   including COMPENSATING/STUCK receipts whose domain revert has not happened. A
   held restore is likewise unavailable on the source: no commitment check and no
   allocation counts it until `restore_source` is CONFIRMED, and no write of S carries it until
   every revert_target is CONFIRMED (`unreleased_restore`, 1.1b).
   Inclusion: a leg is CONFIRMED by another write only through `included` (3.4), never by version alone.
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
| `source_holdback` (558) / `apply_holdback` (579)              | subtract unsettled incoming from a source write payload                       | `payload(E)` (1.1b) is the written value; no payload mutation                                            |
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

| Old `Transfer.state` | Condition                                                                                            | New state                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| -------------------- | ---------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `applied`            | all legs CONFIRMED or no legs                                                                        | `SETTLED`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `pending`            | source leg QUEUED/IN_FLIGHT                                                                          | `RESERVED`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `pending`            | source CONFIRMED, any target leg not terminal                                                        | `SOURCE_APPLIED` when every target leg is QUEUED with `attempts = 0` (never sent: the fresh-spend fence of row 4 has not been proven, so the receipt stays unactivated and the legs unclaimable, rule 3.6/7, until row 4 passes or row 5 compensates); `PUBLISHING` when a target leg was attempted (UNCERTAIN: v1 sent it after its own fence; read-back follows); no target leg yet → `SOURCE_APPLIED`                                                                                                                                                                                                                             |
| `pending`            | source CONFIRMED, every target leg CONFIRMED                                                         | `PUBLISHING` (row 6 settles on the next `advance`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `pending`            | source CONFIRMED, a target leg REJECTED (failed, attempts 0) and none UNCERTAIN                      | `COMPENSATING`, `domain_reverted=false` (row 7; row 8 then reverts the domain)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `pending`            | source UNCERTAIN                                                                                     | `RESERVED` (read-back)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `pending`            | source REJECTED                                                                                      | `COMPENSATING`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `compensated`        | no restore/revert writes, or all CONFIRMED                                                           | `COMPENSATED`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `compensated`        | a `restore-target` write not applied                                                                 | `COMPENSATING`, `domain_reverted=true`: each `restore-target` write becomes a `revert_target` leg (applied → CONFIRMED; pending attempts 0 → QUEUED; pending attempts > 0 or failed → UNCERTAIN, read-back decides); a `restore-source` write becomes the `restore_source` leg (pending attempts 0 → QUEUED, attempts > 0 → UNCERTAIN, applied → CONFIRMED) and stays held until every `revert_target` is CONFIRMED. Covers a confirmed destination repair with an unfinished source restore (all revert legs CONFIRMED, `restore_source` not: `COMPENSATING`; the write failed: `STUCK` `legacy_restore_failed` as in the next row) |
| `compensated`        | source-only repair: `restore-source` write pending/applied-unknown, no `restore-target` write        | `COMPENSATING`, `domain_reverted=true`, `restore_source` leg per the write state (pending, attempts 0 → QUEUED; attempts > 0 → UNCERTAIN)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `compensated`        | source-only repair, or any shape with a failed `restore-source` write: `restore-source` write failed | `STUCK` (`stuck_reason='legacy_restore_failed'`; operator resolve needs read-back)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `failed`             | `_undo` refused (domain not restored)                                                                | `STUCK` (backfill sets `stuck_reason='legacy_failed'`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `failed`             | target write failed after attempts                                                                   | `STUCK`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |

Every legacy `compensated` row (any shape above) already had its domain reverted, so the backfill
sets `domain_reverted=true` (the receipt is not pending incoming; a not-yet-confirmed restore is a
`held_restore`). `pending` rows and `failed` rows with a refused `_undo` have `domain_reverted=false`.
Legacy initiative-origin `pending` rows with no source leg map to `SOURCE_APPLIED` (row 2b), then
the executor runs the fence. Migration regression tests cover each shape, including source-only repairs.

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
12. Compensation parent check with unreadable spend → holds (12b); with fresh spend and overflowing parent → STUCK (12). In both, the receipt stays in `pending_incoming` and out of every payload and commitment capacity (`settled(target)` unchanged); an operator resolve → COMPENSATED reverts the domain atomically or fails 409 (`domain_reverted` required).
13. Concurrent source writes: two source writes for one entity, the older one delayed (`delayed_old_request`): rule 3.6 forbids the second claim while the first is IN_FLIGHT/UNCERTAIN, so upstream never ends above the confirmed payload.
14. Rejected-source rollback (source 100, transfer 10, source write rejected): conservation (inv. 1) holds in every state including the held restore; restore publication waits for every revert_target, availability for `restore_source` CONFIRMED.
15. Legacy backfill shapes: pending with source CONFIRMED and a REJECTED or QUEUED-never-sent target; compensated with confirmed restore-target and pending/failed restore-source; each with a migration regression fixture.
16. Reciprocal transfers A→B and B→A in flight at once → no circular wait (no cross-transfer holds exist); both terminal.
17. Moved group endpoint: compensation locks and checks the CURRENT parent (`_undo` lines 403–410 behaviour preserved).
18. Operator resolve: idempotent replay, non-admin 403, missing reason 422, reader down 409, inconsistent values 409, valid proof → terminal state + audit + notification.
